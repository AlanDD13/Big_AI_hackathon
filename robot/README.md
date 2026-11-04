# Rover execution — platonyesin-dev

Own `robot/**`: DJI adapter, command server/store, waypoint follower, arm/gripper,
local stop/watchdog, hardware configuration, module dependencies and tests.
Execute routes from mapping; route planning belongs to `mapping/`.
Only this module opens the physical rover connection.

First slice: accept a command against a simulated SDK and report status.
Done: duplicate command IDs do not repeat motion, stop interrupts execution,
and stale pose/heartbeat causes a local stop requiring explicit recovery.
Then test connection, bounded movement, pickup and release on the actual rover.

Read `AGENTS.md`, `docs/ONBOARDING.md` and `docs/CONTRACTS.md` before starting.
Add actual install/run/test commands here when implementing the first slice.

## Real SDK environment (step 2)

Working branch: `task/platonyesin-dev/sdk-environment`.
Hardware model EP Core (`rmepcore`) was confirmed by the team on the board.
The human reports chassis, arm, gripper, camera and infrared distance sensor.
Firmware, SDK connectivity and installed-module responses are NOT yet queried.
Only this module will open the physical robot connection.

Use the **DJI RoboMaster Python SDK**, not a separate macOS SDK. This environment
uses official SDK 0.1.1.68 source at commit
`ff6646e115ab125af3207a4ed3df42cc76c795b2`:
https://github.com/dji-sdk/RoboMaster-SDK/tree/ff6646e115ab125af3207a4ed3df42cc76c795b2

The published SDK wheels target old Python versions on Windows/Linux. They
cannot be installed directly on this Apple Silicon Mac. The pure Python SDK
source is installed unchanged. `compat/libmedia_codec.py` supplies the decoder
API expected by that SDK using real PyAV/FFmpeg H.264 and Opus decoding. This is
a local compatibility adapter, not DJI's native codec binary or a fake SDK.
It supports BGR/RGB video and DJI's default 48 kHz mono audio. Other audio
formats are explicitly unsupported. Live stream behavior remains unverified.

Validated host: macOS arm64, Python 3.11.4. Dependency wheels require macOS 14+.
Python 3.11 retains `audioop`, which the legacy SDK imports; do not use the
machine's Python 3.14 for this module. A C compiler/Command Line Tools is needed
to build netifaces. Git is needed to install the pinned official source.

From repository root:

```bash
# Existing Python 3.11 is detected; alternatively set ROBOT_PYTHON explicitly.
bash robot/setup_environment.sh

# Subsequent offline verification, no robot or network needed:
robot/.venv/bin/python robot/check_environment.py
robot/.venv/bin/python -m pip check
```

All runtime and transitive dependency versions are pinned in
`requirements-macos.lock`. Build tools are pinned in the setup script. The
virtual environment, local builds and caches are ignored. No credentials are
required for this step. Do not commit `.env` files or camera/network secrets.

The offline diagnostic imports robot, camera, arm, gripper and sensor modules;
encodes then decodes real H.264 with fragmented input; verifies BGR color and
dimensions; checks JPEG round trip and real Opus decoding. Its generated media
is only a codec test, not rover evidence or a simulated robot. It does not create
a `Robot`, connect, subscribe to telemetry, or issue actuator commands.

## Connection-only check (step 3)

Choose AP/direct or STA/shared-network mode, confirm robot/network details,
initialize the SDK, query versions and telemetry, and close cleanly. Repeat
connection/shutdown before any movement. Verify actual camera frames and sensor
responses separately; import success proves no hardware capability.
Run from repository root, using the serial physically confirmed on the label/app:

```bash
robot/.venv/bin/python robot/check_connection.py --sn YOUR_ROBOT_SERIAL --repeat 2
```

This script is STA/shared-router only. It passively matches the serial before
negotiating an explicit UDP route, refuses the SDK's default-client fallback,
initializes the SDK, verifies the returned serial, queries firmware and closes.
It issues no chassis, arm or gripper movement commands, camera stream or sensor
subscriptions. SDK initialization does change SDK/control/subscription modes;
close the phone app and other controllers before using it. Each cycle has a
35-second worker-process bound. A timeout terminates that process and reports
cleanup as unconfirmed; it is not proof of a physical stop. Add `--verbose`
only when SDK protocol logs are needed. No automatic actuator recovery exists.

On macOS, allow Local Network access for the app hosting the terminal (Codex /
ChatGPT here). Broadcast discovery worked before that permission was allowed,
but unicast handshake and ping failed with OS error 65, No route to host.
After the human enabled permission, two real STA/UDP cycles succeeded on
2026-11-04: device serial matched, firmware was `01.01.1150`, both SDK close
calls returned, and both worker processes exited successfully. No movement
commands were sent. IP addresses are discovered afresh, not hardcoded.

Camera, infrared distance sensor, arm/gripper responses, movement and watchdog
behavior still require separate checks. No command server or shared API
integration is included in this task.

## Hardware functionality checks

See [HARDWARE_CHECKS.md](HARDWARE_CHECKS.md) for real camera/sensor/chassis/arm/
gripper results, reproduction commands, SDK quirks and remaining limitations.
`check_hardware.py` is a staged bench diagnostic, not a live control bridge.

## Watchdog and recovery (step 5)

See [WATCHDOG.md](WATCHDOG.md) for the internal safety core, offline tests,
and real-SDK stationary and moving bench results.
The implementation requests zero independently of input delivery, keeps an
unconfirmed stop UNKNOWN, latches confirmed faults, and requires explicit
rearm plus new authority before movement.

## Rover camera and TOF sensor service (step 6)

`robot/camera.py` captures live video frames (`start_video_stream` -> BGR -> JPEG)
and 4-slot infrared TOF distance telemetry (`sub_distance`) from the EP Core and
exposes `GET /camera/frame` (`Authorization: Bearer <PEER_TOKEN>`) plus public
`GET /health` per `contracts/robot-simulator-v1.md` and `contracts/vision-v1.md`:

- Returns `200 OK` (`image/jpeg`) with `X-Camera-Id`, `X-Session-Id`,
  `X-Geometry-Version`, `X-Frame-Id`, `X-Captured-At`, `X-Timestamp-Basis: RECEIVE`,
  and `X-Distance-Mm-Slot0`.
- Returns identical bytes for repeated reads of the same `X-Frame-Id`; increments
  `X-Geometry-Version` if resolution changes; mints a new `X-Session-Id` on
  restart.
- Fails closed with `503 STALE_FRAME` when no frame has been captured, the stream
  is stopped/faulted, or the newest frame is older than `1.0 s` (never serves a
  stale or placeholder image).

Run the bounded live camera + sensor verification or continuous frame server from
repository root:

```bash
# Offline unit tests (47 tests across safety, telemetry, and camera):
robot/.venv/bin/python -m unittest discover -s robot/tests -v

# Bounded physical rover camera + TOF sensor check (360p or 720p, closes session cleanly):
robot/.venv/bin/python robot/camera.py --sn YOUR_ROBOT_SERIAL --resolution 360p --duration 4.0
robot/.venv/bin/python robot/camera.py --sn YOUR_ROBOT_SERIAL --resolution 720p --duration 3.0

# Continuous GET /camera/frame server on :8004 (requires PEER_TOKEN in env):
PEER_TOKEN=your_peer_token robot/.venv/bin/python robot/camera.py --sn YOUR_ROBOT_SERIAL --serve --port 8004
```

Measured on physical unit `3JKCK7W0030DJS` (firmware `01.01.1150`, STA/UDP):
- `360p` (`640x360`): `29.9 FPS` sustained capture (`120` frames in `4.0 s`),
  `0.57–1.13 ms` HTTP poll latency, `21.1 ms` frame age, `503 STALE_FRAME` before
  start and after close, `401 UNAUTHORIZED` on invalid bearer token.
- `720p` (`1280x720`): `30.9 FPS` sustained capture (`95` frames in `3.0 s`),
  `0.53–0.68 ms` HTTP poll latency, `24.6 ms` frame age, `slot_0` forward TOF
  reading `2294–2305 mm`.

## Command tracking and backend bridge (step 7)

`robot/bridge.py` implements the closed P4 tool surface (`contracts/robot-simulator-v1.md`,
`contracts/fixtures/robot-simulator.json`, `contracts/manual-control-v1.md`, and
`contracts/fixtures/manual-control.json`) guarded by `SafetyController` and `Watchdog`:

- Mission command routes (`Authorization: Bearer <PEER_TOKEN>`):
  - `POST /commands/pickup`, `POST /commands/{commandId}/hold`,
    `POST /commands/{commandId}/stop`, `POST /commands/{commandId}/rearm`,
    `GET /commands/{commandId}`, `POST /stop`
  - Tracks `bridgeBootId` (`404 UNKNOWN_COMMAND` after bridge restart without
    re-creating or repeating prior commands) and idempotent `statusRevision`.
- Manual control routes (`MANUAL_CHASSIS_SLICE_1`):
  - `POST /manual/lease`, `POST /manual/drive`, `POST /manual/release`,
    `POST /manual/rearm`, `GET /manual/status`
  - Enforces mutual exclusion between autonomous commands (`409 AUTONOMOUS_ACTIVE`)
    and manual leases (`409 MANUAL_LEASE_ACTIVE`), `<= 0.5 s` telemetry freshness
    (`503 TELEMETRY_STALE` in `UNKNOWN`), `409 STOP_LOCKED` in latched `STOPPED`,
    `409 NOT_STOPPED` on `rearm` until 3 stationary samples spanning `>= 200 ms`,
    and body-frame velocity caps (`0.35 m/s`, `0.35 m/s`, `45 deg/s` -> `422 VALIDATION_ERROR`).

```bash
# Run offline unit tests (safety, telemetry, camera, bridge):
robot/.venv/bin/python -m unittest discover -s robot/tests -v

# Start simulator bridge (no hardware actuation):
PEER_TOKEN=your_peer_token robot/.venv/bin/python robot/bridge.py --mode simulator --port 8004
```

The HTTP server owns an independent watchdog in both modes; status and health
expose `watchdogAlive` and `watchdogError`. Physical startup uses a dedicated
chassis adapter with the same limits as the safety core, not the bench-only
0.05 m/s adapter. It subscribes coherent chassis pose/attitude/velocity feedback
and feeds heartbeat only from new valid observations. Shutdown unsubscribes
telemetry and stops the watchdog. Zero writes cancel the SDK host timer and do
not replace it with another timer.

Physical command stop and rover-wide stop remain `UNKNOWN` until telemetry
confirms stationarity. GET reconciles the command to `STOPPED`; command rearm
returns `409 NOT_STOPPED` while confirmation is pending or core recovery fails.
Simulator mode retains the normative command fixtures; those immediate simulated
states are not physical acceptance evidence. Pending manual release/stop also
blocks pickup, including the concurrent lease-release window.

PR #17 review fixes were verified offline with SDK-shaped recording fixtures
and loopback HTTP tests. No physical bridge startup or movement is claimed by
those checks. Updating these files does not replace a running camera-only server;
stop the sole existing SDK owner before any separately authorized physical launch.

### Supervised acceptance harness

`physical_acceptance.py` uses one SDK session and opens no HTTP listener. Stop
the existing camera/bridge owner first; a listener on local port 8004 blocks
launch. This is a useful guard, not proof that no other machine owns the SDK.
Keep an operator at the physical power switch throughout. Unexpected motion
means power off, regardless of what the report says.

Start with the disarmed, zero-only camera and telemetry observation:

```bash
robot/.venv/bin/python robot/physical_acceptance.py --sn 3JKCK7W0030DJS
```

The operator must type `ALLOW ZERO ONLY`. It sends startup/shutdown zero
commands, observes camera and telemetry for two seconds, checks UNKNOWN and
early rearm rejection, then confirms shutdown before disconnecting. SDK
initialization changes SDK/mode state; this is not a passive network probe.
Telemetry thresholds cannot exclude very slow drift; watch the actual rover.

Only after investigating unexpected movement and agreeing a supervised test
window, the separate `--allow-motion` option requires `ALLOW FORWARD TEST`.
It sends one 0.05 m/s forward intent through the bridge, makes no requests for
350 ms, verifies a zero dispatch within 300 ms, waits for stationary telemetry,
and checks that explicit rearm does not issue another movement. No sideways,
reverse, rotation, arm, gripper, transport, or policy execution is included.

Reports are saved under ignored `robot/.local/acceptance/` with unique names.
`PASS` refers only to the report's named scope. Dispatch timing is not physical
stop latency; physical outcome requires operator/video confirmation. Remaining
physical gates are listed in the report, including release/pickup arbitration.
Existing offline tests cover that arbitration but do not establish live acceptance.
No full physical recovery-loop completion is claimed. SDK calls can block;
the operator must not rely on host cleanup as the emergency stop.

### Physical recovery adapter proposal

`recovery_adapter.PhysicalRecoveryAdapter` is explicitly `mode="physical"`.
It accepts an existing `PhysicalBridgeRuntime`; it never opens another SDK
session, exposes a new HTTP route, or implicitly rearms. The current shared
LangGraph rejects this mode: the integration owner must explicitly wire and
review physical support, rather than relabelling this adapter as a simulator.

Constructor dependencies are mandatory:

- `authority.reserve(mission_id)` must durably reserve through P3; only literal
  `True` grants authority. `authority.allows(command)` checks current dispatch
  authorization. No permissive authority stub is supplied.
- `observations(binding)` must return actual trusted perception/sensor evidence
  for the mission or command binding: matching object/revision, camera/session,
  increasing frame, identity evidence and capture/telemetry ages <=500 ms.
  It must also provide command-bound pickup/place evidence before those future
  capabilities can be integrated. Camera images or TOF alone do not establish
  object identity, a safe route, or successful grasp.
- `planner(command, observation)` returns a calibrated `ChassisPlan` tied to
  that exact command/object/revision and refreshed frame/identity evidence.
  Grok is not permitted to supply velocities or pretend a demo grid is calibrated.
- `journal_path` is a private persistent local SQLite file, for example under
  `robot/.local/recovery/`. It supplements P3 authority; it is not backend storage.

Currently only bounded `approach` and `align` are implemented, through shared
manual lease arbitration. Test caps are stricter than the bridge: 0.05 m/s per
axis, 5 degrees/s, <=150 ms per segment. The controller receives an expiring
intent; an independent watchdog remains active. Clean release must be confirmed
by fresh telemetry before ACKNOWLEDGED is returned. Receipt ACK is not evidence
that the target was reached. Perception must independently establish that.

Pickup, transport and place are rejected without output. Agreed physical arm
macro durations, verified object profiles, held-object/destination evidence and
shared graph wiring are still required for a full five-phase test. No executable
entry point or default fixture can turn this proposal into an autonomous run.

Local reservations survive process restarts; command IDs are written UNKNOWN
before any possible output. No ID is resent, even after a previous successful
ACK. An ambiguous drive outcome remains uncertain even if a later stop confirms.
There is intentionally no automatic journal reset/release or replay operation.
