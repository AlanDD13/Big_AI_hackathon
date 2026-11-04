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

## Next: connection-only check (step 3, not implemented)

Choose AP/direct or STA/shared-network mode, confirm robot/network details,
initialize the SDK, query versions and telemetry, and close cleanly. Repeat
connection/shutdown before any movement. Verify actual camera frames and sensor
responses separately; import success proves no hardware capability.
No command server, live connection script, motor control, manipulation or shared
API integration is included in this environment task.
