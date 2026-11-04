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
telemetry, not the requested command. Angular velocity is derived from yaw over
a 150–350 ms receipt-time window; near-simultaneous callbacks cannot amplify
tiny angle noise into huge rates, and `YawRate.update()` returns `None` during
the initial `<150 ms` window. This is local chassis feedback, not calibrated P5
map localization. A future navigation controller must also validate the actual
map/versioned P5 pose.

For bench acceptance only, the SDK host timer is set to 1.0 s, longer than the
watchdog threshold, to distinguish a watchdog-requested zero from timer expiry.
Both execute on the same Mac. Neither guarantees anything after host death,
process termination, a blocked transport or network loss. The output wrapper
cannot derive transport success from the SDK's misleading False/no-ACK return;
only fresh motion observations can establish the later confirmation.

## Verification on 2026-11-04

- 43 deterministic offline tests pass: expiry, input/heartbeat/telemetry loss,
  stale authority and boot, active-session rejection across all admission faults,
  replay/order, monotonic `sample_id` deduplication, voluntary `release()`
  lifecycle and timeout, `contracts/manual-control-v1.md` HTTP error mapping,
  stalled-write non-blocking `stop()`/`tick()`/`status()` and recovery to zero,
  slow drive racing `tick()`, watchdog survival across `tick()` and `stop()`
  exceptions, `Watchdog.close()` failure resilience, clock changes, and
  telemetry first-150 ms window / burst / wrap / gap handling. Recording test
  outputs never create a simulated SDK or claim hardware evidence.
- All six real-SDK **zero-velocity** scenarios pass: silence, short deadline,
  withheld telemetry forwarding, withheld heartbeat, operator stop and replayed
  sequence. Each ends confirmed STOPPED and verifies rearm without motion.
- Final moving silence check: zero requested at 0.267 s after command start;
  stop confirmed at 0.772 s; maximum reported body speed 0.08 m/s for a requested
  0.05 m/s. Rearm did not issue movement. Earlier ~0.305 s timing was accepted
  by the human; the final 250 ms guard adds scheduling headroom. Desktop timing
  is measured, not a hard realtime guarantee or a stopping-distance bound.
- **Moving explicit-stop acceptance is incomplete.** The zero request and
  repeated zero writes occurred, but body-velocity feedback stayed around
  -0.04 to -0.07 m/s and the controller correctly stayed UNKNOWN and refused
  rearm. The human confirmed the rover was stationary on the floor. Subsequent
  zero-only diagnostics showed stable position, low wheel readings and near-zero
  body velocity. The source of the disagreement is unresolved. Do not widen
  confirmation thresholds or advertise live-control readiness from this test.
- An earlier moving silence test also stayed UNKNOWN; burst-safe yaw estimation
  and later successful checks are recorded, but that does not prove every
  disagreement was caused by yaw jitter. Original failure reports are preserved.
- No Wi-Fi interruption, controller kill/crash, physical emergency-stop
  validation, load/stress acceptance or calibrated stopping-distance test ran.

All sessions were closed. Raw reports, bounded telemetry traces and SDK logs
are ignored under robot/.local/watchdog/. Hardware stop feedback and supervised
host/network-failure behavior remain required before claiming full acceptance.

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

Do not repeat physical tests while stop confirmation is unresolved without an
operator verifying the rover is stationary and the workspace is clear. A bench
worker timeout terminates a host process; its message deliberately reports
physical stop as unconfirmed. It is not a safe substitute for a hardware stop.
