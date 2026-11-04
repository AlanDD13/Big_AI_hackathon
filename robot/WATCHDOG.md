# Step 5: watchdog and explicit recovery

Scope: robot/** internal chassis safety core and real-SDK bench checks. No HTTP
server, manual lease endpoint, autonomous mission state machine, arm control or
camera service is introduced. No live endpoint is listening on port 8004.

## Behavior

`safety.py` accepts a bounded velocity-output callable and serializes drive,
stop, watchdog faults and rearm with a lock. `Watchdog` runs a separate host
thread, independent of incoming requests and the DJI timer. It must be running
whenever this controller is used to drive. The output callable must return
promptly; a blocking transport can delay the thread and is not an independent
physical fail-stop. Existing standalone diagnostics do not automatically gain
this controller merely because this module exists.

| Gate | Default |
| --- | --- |
| Input silence guard | 250 ms, leaving scheduling headroom for the 300 ms target |
| Watchdog polling | 20 ms |
| Maximum intent lifetime | 500 ms |
| Required local telemetry age | <500 ms; real bench additionally requires pose/attitude receipt <300 ms |
| Control heartbeat age | <1.0 s (internal core setting, not a new shared HTTP promise) |
| Stop-confirmation warning | 1.5 s; an unconfirmed stop remains UNKNOWN |
| Initial maximum requested body velocity | +/-0.35 m/s x/y, +/-45 degrees/s yaw |
| Real acceptance runner | +/-0.05 m/s translation only; no rotation |

All inputs must be finite and in bounds. Positive sequence numbers must increase
strictly, with matching process boot and authority generation. Expired/future/
overlong intents, obsolete authority, conflicting IDs and invalid telemetry
revoke authority and request zero. UTC expiry is sampled once at admission and
translated to a monotonic deadline, so wall-clock changes cannot extend an
accepted intent. A bounded retired-ID set prevents reuse after recovery; at its
limit an explicit process restart/revalidation is required.

Startup, stop and faults are locked. A zero-speed write is NOT an ACK:

1. Revoke authority and request zero, reporting internal hardware stage UNKNOWN.
2. Require at least three distinct coherent stationary observations spanning
   >=200 ms after the zero request (|vx|/|vy| <=0.025 m/s; |yaw rate| <=3 deg/s).
3. Confirm STOPPED and keep the fault latched. Stale/missing/conflicting data
   never confirms stop. Later unexpected motion invalidates confirmation.
4. Explicit rearm requires confirmed STOPPED plus fresh telemetry and heartbeat.
   It returns IDLE, increments the generation and sends no movement. Old
   authority/IDs cannot execute again; a new validated command is required.

The word UNKNOWN here is hardware confirmation state, not backend mission
unknown. Alan approved the stop/latch/rearm semantics on the board. Backend
requested future manual-UNKNOWN mappings of 503 TELEMETRY_STALE on lease/drive
and 409 NOT_STOPPED on rearm. Those are HTTP integration concerns; this module
does not expose or freeze that API. Clean manual release returning to IDLE after
confirmation is also future bridge work, distinct from a latched fault stop.

## Real telemetry adapter

`watchdog_bench.py` uses the real pinned SDK and the confirmed-serial session
helper. A forwarded observation requires new position, attitude and velocity
data. Body velocity comes from SDK telemetry, not the requested command.
Angular velocity is derived from yaw over a 150–350 ms receipt-time window;
near-simultaneous callbacks cannot amplify tiny angle noise into huge rates.
This is local chassis feedback, not calibrated P5 map localization. A future
navigation controller must also validate the actual map/versioned P5 pose.

For bench acceptance only, the SDK host timer is set to 1.0 s, longer than the
watchdog threshold, to distinguish a watchdog-requested zero from timer expiry.
Both execute on the same Mac. Neither guarantees anything after host death,
process termination, a blocked transport or network loss. The output wrapper
cannot derive transport success from the SDK's misleading False/no-ACK return;
only fresh motion observations can establish the later confirmation.

## Verification on 2026-11-04

- 34 deterministic tests pass: expiry, input/heartbeat/telemetry loss, stale
  authority and boot, replay/order, invalid values, output exceptions, unknown
  stop, independent thread wakeup, stop/drive race, rearm without replay,
  clock changes and telemetry burst/wrap/gap handling. Recording test outputs
  never create a simulated SDK or claim hardware evidence.
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
