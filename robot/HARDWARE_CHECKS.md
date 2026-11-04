# EP Core hardware checks — 2026-11-04

Real robot, shared-router STA/UDP, firmware 01.01.1150. User confirmed the
discovered serial against the robot. Tests used one active SDK session at a
time and closed each session. Reports and camera image remain local/ignored
under robot/.local/hardware-check/. No HTTP bridge is running.

## Results

| Component | Evidence | Limit |
| --- | --- | --- |
| Connection | Two prior serial/firmware/close cycles, plus successful test sessions | Network permission required on Mac |
| Camera | Real frame decoded at 640 x 360 BGR and saved as JPEG | Not a sustained-stream/latency test |
| Chassis telemetry | Position, attitude, velocity callbacks received at requested 10 Hz | No external position accuracy calibration |
| Battery | 54% initially; 51% at later tests | Not a battery endurance test |
| Infrared distance | One slot returned changing positive readings, about 6.6 m initially and 1.3 m later; other slots zero | No measured reference or accuracy/slot-orientation validation; zeros are not confirmed free space |
| Chassis translation | +/-x and +/-y at 0.05 m/s for approximately 0.6 s each; displacement roughly 2.7–4.2 cm and appropriate body-velocity signs | Floor/slip/latency affect actual displacement |
| Chassis rotation | +/-5 deg/s pulses; yaw changed approximately +1.84 and -2.81 degrees | Not heading accuracy or route calibration |
| Explicit stop | Stationary velocity samples between pulses and zero final velocity | Software/telemetry evidence, not certified physical emergency stop |
| SDK host timer | 0.25 s velocity expiry followed by stationary telemetry at 0.7 s | Not independent protection against host crash or network loss |
| Arm | Position callbacks and action completion; +5 mm request changed Y from -74 to -67 mm | Return -5 mm request completed, but immediate reading -66 mm and later -70 mm did not prove exact return; needs calibration/settling checks |
| Gripper | Open, close and pause acknowledged at power 10, 0.4 s pulses; repeated gripper-only stage, human explicitly confirmed visible travel in both directions | Status stayed normal; full endpoints and cargo retention not verified |

The human also explicitly confirmed observing chassis movement. The arm and
gripper are not verified pickup routines. No object was picked up.
No autonomous mission, manual control lease, HTTP bridge, independent bridge
watchdog, loss-of-network stop or recovery protocol was tested.

## SDK issues discovered

- ProtoChassisSpeedMode is a PUSH/no-ack message. The SDK's generic synchronous
  wrapper returns False despite sending; a Boolean must not be advertised as a
  speed/stop acknowledgment. Verify outcomes through fresh telemetry instead.
- ArmSubject decodes coordinates using unsigned 32-bit integers. Raw Y
  4294967222 corresponds to -74 under signed 32-bit interpretation. Preserve
  raw data; the test normalizes it locally before a conservative upward jog.
- Firmware also sends unregistered cmdset 0x24 / cmdid 0x21 pushes. SDK warns
  and skips them while the subscribed telemetry still arrives. Their meaning
  has not been established; do not claim complete firmware protocol coverage.
- The first chassis check stopped at the test's incorrect acknowledgment gate
  before any nonzero command. A later forward pulse stopped, but the initial
  0.25 s stop sample was still settling. The test now allows up to 1.5 s and
  requires three quiet samples. Yaw stability was added after the successful
  sequence, since angular velocity fields alone are insufficient evidence.

## Re-running

From repository root, with the phone app/other SDK controllers closed:

```bash
robot/.venv/bin/python robot/check_hardware.py --sn YOUR_ROBOT_SERIAL --stage passive
robot/.venv/bin/python robot/check_hardware.py --sn YOUR_ROBOT_SERIAL --stage chassis --allow-motion
robot/.venv/bin/python robot/check_hardware.py --sn YOUR_ROBOT_SERIAL --stage arm --allow-motion
robot/.venv/bin/python robot/check_hardware.py --sn YOUR_ROBOT_SERIAL --stage gripper --allow-motion
```

Only run actuator stages with a clear floor, empty gripper, clear arm travel
and an operator at the rover. Each stage is separate; never run them concurrently.
The 40 s worker bound terminates a stuck host process; it cannot guarantee
physical stop or cancel an arm action. A failed/missing stop or stale telemetry
ends the sequence without automatically resuming. COMPLETED means the bounded
stage executed, not that every component is calibrated or deployment-ready.
