# Step 5: watchdog and explicit recovery

Scope: robot/** internal chassis safety core and real-SDK bench checks. No HTTP
server, manual lease endpoint, autonomous mission state machine, arm control or
camera service is introduced. No live endpoint is listening on port 8004.

## Behavior

`safety.py` accepts a velocity-output callable and serializes state transitions
(drive admission, stop, voluntary release, watchdog faults and rearm) with a
state lock while executing transport writes on a bounded background output
worker outside the lock. `Watchdog` runs a separate host thread, independent of
incoming requests and the DJI timer. It must be running whenever this controller
is used to drive.

- **Non-blocking under lock**: `_write` is never invoked while holding `_lock`.
  If the underlying SDK write blocks past `write_timeout_s` (default 50 ms),
  `drive()`, `stop()`, `tick()` and `status()` do not stall; the controller marks
  `writeStuck=True` and `transportError="WRITE_TIMEOUT"`, revokes authority, and
  queues a priority zero command so if the stalled transport later unblocks it
  immediately writes `(0, 0, 0)` and drops any stale non-zero intent.
- **Resilient watchdog lifecycle**: `Watchdog._run()` catches exceptions from
  both `tick()` and fallback `stop()`, forces fail-closed disarmed `UNKNOWN`
  state via `_force_fault_state()`, exposes `watchdog.alive`, `watchdog.healthy`,
  `watchdogAlive` and `watchdogError` in `status()`, and keeps the loop running.
  `Watchdog.close()` signals `_done` before invoking `stop("SHUTDOWN")` and
  always joins the thread in a `finally` block.
- **No false physical guarantee**: A host timeout or worker thread cannot undo a
  physical command already in flight or protect against host death / network loss.
  Existing standalone diagnostics do not automatically gain this controller
  merely because this module exists.

| Gate | Default |
| --- | --- |
| Input silence guard | 250 ms, leaving scheduling headroom for the 300 ms target |
| Watchdog polling | 20 ms |
| Output write timeout | 50 ms (`write_timeout_s`); never held under `_lock` |
| Maximum intent lifetime | 500 ms |
| Required local telemetry age | <500 ms; real bench additionally requires pose/attitude receipt <300 ms |
| Control heartbeat age | <1.0 s (internal core setting, not a new shared HTTP promise) |
| Stationary confirmation window | >=3 distinct samples (by monotonic `sample_id` or timestamp) spanning >=200 ms (`stationary_span_s`) after zero request |
| Stop-confirmation warning | 1.5 s; an unconfirmed stop remains UNKNOWN |
| Initial maximum requested body velocity | +/-0.35 m/s x/y, +/-45 degrees/s yaw |
| Real acceptance runner | +/-0.05 m/s translation only; no rotation |

All inputs must be finite and in bounds. Positive sequence numbers must increase
strictly, with matching process boot and authority generation.
**Fail-closed rejected-drive policy**: any rejected drive reaching
`SafetyController.drive()` during an active session (expired/future/overlong
intent, stale `boot_id` or `generation`, conflicting `command_id`, duplicate or
lower `sequence`, or invalid/out-of-range velocity) immediately revokes active
authority, retires the active command ID, and requests zero velocity. UTC expiry
is sampled once at admission and translated to a monotonic deadline, so
wall-clock changes cannot extend an accepted intent. A bounded retired-ID set
prevents reuse after recovery; at its limit an explicit process
restart/revalidation is required.

Startup, stop and faults are locked. A zero-speed write is NOT an ACK:

1. Revoke authority and request zero, reporting internal hardware stage UNKNOWN.
2. Require at least three distinct coherent stationary observations spanning
   >=200 ms (`stationary_span_s = 0.2`) after the zero request (`|vx|/|vy| <=0.025 m/s`;
   `|yaw rate| <=3 deg/s`), deduplicated via monotonic `sample_id` (or timestamp
   when `sample_id` is omitted).
3. For faults and explicit `stop()`, confirm STOPPED and keep the fault latched.
   For voluntary `release()`, transition `UNKNOWN -> IDLE` after the 3 fresh
   stationary observations spanning >=200 ms; if confirmation times out, a
   voluntary release stays `UNKNOWN` (never latches `STOPPED` on timeout alone).
   Stale/missing/conflicting data never confirms stop. Later unexpected motion
   invalidates confirmation.
4. Explicit `rearm()` requires confirmed STOPPED plus fresh telemetry and
   heartbeat. It returns IDLE, increments the generation and sends no movement.
   Old authority/IDs cannot execute again; a new validated command is required.

### Internal codes and `contracts/manual-control-v1.md` mapping

The word `UNKNOWN` here is hardware confirmation state, not backend autonomous
mission `unknown`. While HTTP routes and manual lease endpoints remain deferred
out of scope for this PR, `SafetyError` and `SafetyController.manual_http_error()`
carry the exact HTTP status and wire error code mapping aligned with
`contracts/manual-control-v1.md`:

- `drive()` while `stage == "UNKNOWN"` or telemetry stale -> `SafetyError("STOP_LOCKED", http_status=503, http_code="TELEMETRY_STALE")`
- `drive()` while latched `stage == "STOPPED"` -> `SafetyError("STOP_LOCKED", http_status=409, http_code="STOP_LOCKED")`
- `rearm()` while `stage != "STOPPED"` (including `UNKNOWN` or `IDLE`) or stale telemetry/heartbeat -> `http_status=409, http_code="NOT_STOPPED"`
- `drive()` with stale boot/generation/sequence -> `http_status=409, http_code="STALE_INTENT"`
- `drive()` with expired/invalid deadline -> `http_status=409, http_code="LEASE_EXPIRED"`
- `drive()` with conflicting command ID -> `http_status=409, http_code="LEASE_HELD"`
- `drive()` with non-finite or out-of-cap velocity -> `http_status=422`

## Real telemetry adapter

`watchdog_bench.py` uses the real pinned SDK and the confirmed-serial session
helper. A forwarded observation requires new position, attitude and velocity
data and increments a monotonic `sample_id`. Body velocity comes from SDK
telemetry, not the requested command. Angular velocity (`YawRate`) and planar
position rate (`PositionRate`) are derived over a 150–350 ms receipt-time window;
near-simultaneous callbacks cannot amplify tiny angle noise into huge rates, and
both estimators return `None` during the initial `<150 ms` window. `watchdog_bench.py`
also records multi-sensor diagnostics (`sub_velocity` 6-tuple, `sub_position` +
`PositionRate`, `sub_esc` 4-wheel RPMs, and `sub_status` `static_flag`).
This is local chassis feedback, not calibrated P5 map localization. A future
navigation controller must also validate the actual map/versioned P5 pose.

For bench acceptance only, non-zero drive writes set the SDK host timer to 1.0 s
(longer than the 300 ms watchdog threshold) to distinguish a watchdog-requested
zero from SDK timer expiry, while zero-velocity stop writes cancel any pending
`_auto_timer` and call `drive_speed(0.0, 0.0, 0.0)` directly without re-arming
redundant 1.0 s timers on every 100 ms stop retry. Both execute on the same Mac.
Neither guarantees anything after host death, process termination, a blocked
transport or network loss. The output wrapper cannot derive transport success
from the SDK's misleading False/no-ACK return; only fresh motion observations
can establish the later confirmation.

## Verification on 2026-11-04

- **44 deterministic offline tests pass**: expiry, input/heartbeat/telemetry loss,
  stale authority and boot, active-session rejection across all admission faults,
  replay/order, monotonic `sample_id` deduplication, voluntary `release()`
  lifecycle and timeout, `contracts/manual-control-v1.md` HTTP error mapping,
  stalled-write non-blocking `stop()`/`tick()`/`status()` and recovery to zero,
  slow drive racing `tick()`, watchdog survival across `tick()` and `stop()`
  exceptions, `Watchdog.close()` failure resilience, clock changes, and
  telemetry first-150 ms window / burst / wrap / gap / `PositionRate` handling.
  Recording test outputs never create a simulated SDK or claim hardware evidence.
- **All six real-SDK zero-velocity scenarios pass**: `silence`, `deadline`,
  `telemetry`, `heartbeat`, `operator-stop` (confirmed `STOPPED` at `0.544 s`,
  zero write at `0.153 s`), and `stale-intent`. Each ends confirmed `STOPPED`
  and verifies `rearm()` without motion.
- **Moving silence check (`--scenario silence --allow-motion`, `PASS`)**:
  fault detected at `0.274 s` and zero write at `0.274 s` (`<= 0.300 s` target
  met); stop confirmed at `0.832 s` (4 stationary samples spanning `>= 0.2 s`);
  peak measured speed `0.09 m/s` (`esc_speed_rpm` up to `18 RPM`, `position_xyz`
  `+24.1 mm`) for a requested `0.05 m/s`; `rearm()` advanced to generation `2`
  with zero movement. (Earlier `0.267 s` / `0.772 s` run also passed.)
- **Moving explicit-stop check (`--scenario operator-stop --allow-motion`, `PASS`)**:
  after updating `DJIOutput` so zero-velocity stop writes cancel `_auto_timer`
  and invoke `drive_speed(0.0, 0.0, 0.0)` without re-arming the 1.0 s SDK host
  timer every 100 ms (and keeping the exact `0.025 m/s` and `3 deg/s` stationary
  thresholds unchanged), `operator-stop --allow-motion` passed cleanly:
  - Fault detected at `0.156 s`, zero write at `0.156 s`, peak speed `0.03 m/s`
    (`position_xyz` `+4.08 mm`, `static_flag` `1 -> 0`).
  - Confirmed `STOPPED` at `0.678 s` (`stationarySamples: 3` spanning `>= 0.2 s`,
    `velocity_6` settled to `[-0.01, 0.0, 0, -0.01, 0.0, 0]`, `esc_speed_rpm`
    within `[-3, 3] RPM`).
  - `rearm()` advanced to generation `2` with no restored motion, followed by
    clean `BENCH_COMPLETE` stop confirmation.
  - Historical note: an earlier moving `operator-stop` run (before cancelling
    `_auto_timer` on zero writes) saw `vbx` stay around `-0.04` to `-0.07 m/s`
    and rightly stayed `UNKNOWN` and refused `rearm()`. Original failure traces
    remain preserved under `robot/.local/watchdog/`.
- No Wi-Fi interruption, controller kill/crash, physical emergency-stop
  validation, load/stress acceptance or calibrated stopping-distance test ran.

All sessions were closed. Raw reports, bounded telemetry traces and SDK logs
are ignored under `robot/.local/watchdog/`.

## Commands

```bash
# Offline logic tests; no SDK connection or robot movement.
robot/.venv/bin/python -m unittest discover -s robot/tests -v

# Real SDK, zero velocity only by default. One owner/session at a time.
robot/.venv/bin/python robot/watchdog_bench.py --sn YOUR_ROBOT_SERIAL --scenario silence
# Other zero-only scenarios: deadline, telemetry, heartbeat, operator-stop, stale-intent

# Physical tests are separate, supervised, and explicitly opt-in:
robot/.venv/bin/python robot/watchdog_bench.py --sn YOUR_ROBOT_SERIAL --scenario silence --allow-motion
robot/.venv/bin/python robot/watchdog_bench.py --sn YOUR_ROBOT_SERIAL --scenario operator-stop --allow-motion
```

A bench worker timeout terminates a host process; its message deliberately
reports physical stop as unconfirmed. It is not a safe substitute for a
hardware stop.
