# EP Core manual-control draft

Hardware confirmed by LcyShox: `rmepcore` (RoboMaster EP Core).
All controls, camera views, poses and acknowledgments here are simulated.
The three selectable droids are fixtures, not discovered devices.

## Official API research

The external Python SDK is published for RoboMaster EP. DJI confirms EP Core
SDK control of chassis, arm and gripper. S1's official programming platform is
RoboMaster Lab; do not assume S1 has the EP external SDK boundary.
Sources: [DJI SDK repository](https://github.com/dji-sdk/RoboMaster-SDK),
[EP Core support](https://www.dji.com/support/product/robomaster-ep-core),
[S1 support](https://www.dji.com/support/product/robomaster-s1).

`chassis.drive_speed(x,y,z,timeout)` takes forward/lateral speed in m/s and yaw
rate in degrees/s; documented ranges are +/-3.5 m/s and +/-600 degrees/s.
`chassis.move(x,y,z,xy_speed,z_speed)` instead takes relative displacement and
rotation, returning an action. It does not accept warehouse pixel coordinates.
Arm relative `move(x,y)` uses millimetres, forward/up positive. Gripper has
open/close/pause operations. Camera frames require SDK handling in the bridge;
they are not a browser-ready video element URL. Robot mode/capabilities and
feedback must be confirmed by P4 before enabling live modules.
Source: [SDK API](https://robomaster-dev.readthedocs.io/en/latest/python_sdk/robomaster.html).

The official speed example uses +x forward, +y right and +z right turn. The
simulator converts this to map metres and counterclockwise-radian yaw explicitly.
This speed convention must not be assumed for every other SDK method.
Source: [official speed example](https://github.com/dji-sdk/RoboMaster-SDK/blob/master/examples/02_chassis/03_speed.py).

The SDK source implements drive_speed timeout with a host-side threading timer.
It is not evidence of an independent physical watchdog under link/process loss.
P4 must verify local stopping behavior and enforce independent expiry/limits.
Source: [DJI chassis implementation](https://github.com/dji-sdk/RoboMaster-SDK/blob/master/src/robomaster/chassis.py).

The SDK supports AP/STA/RNDIS connection choices. Networking and each robot's
connection belong in robot/, never in browser code.
Source: [SDK connections](https://robomaster-dev.readthedocs.io/en/latest/python_sdk/connection.html).

## Try first person

1. Open Manual, choose an online fixture droid and Take control.
2. Hold/drag the translation joystick, or hold Space with WASD. Q/E rotate.
   The rotation buttons also work while held. Release stops local velocity.
3. Choose Precision/Standard/Travel (0.15/0.30/0.50 m/s). Diagonal motion is
   normalized. Rotation is capped at 60 degrees/s. These are draft app limits.
4. In the same Joystick control widget, use 10 mm arm jogs and gripper Open/Close
   while stationary. The illustrated
   arm range is a fixture envelope, not a calibrated EP Core workspace.

The first-person scene is a pose-dependent SVG rendering of the fixture floor,
not video. No gimbal is assumed for EP Core and none is exposed.
The separate Main camera widget is intentionally empty, with no image or stream.
Simulation tools and SDK input previews are collapsed to keep the control view
compact. Control authority and speed presets share one panel.

## Try overhead command

Select a droid, click clear floor, inspect the destination/fixture path and
confirm Move here. Keyboard users can enter east/north metres and Preview
destination. Rack, boundary and other-droid targets are rejected. Halt cancels
the path; selecting a new robot or mode releases control. There is one manually
controlled droid at a time; cooperative routing is not implemented.

The small fixture grid planner exists only to make this UI interactive. Production
mapping/calibration, uncertainty and collision-checked routing remain P5-owned.
For live camera clicks, send camera/session/geometry/frame identity and source
image pixel to P3/P5, then display the validated map/versioned route. Do not reuse
the fixture's rectangle normalization as a real camera calibration.

## Control and integration boundaries

Manual authority is mutually exclusive with recovery simulation missions.
Modal dialogs, leaving the view, switching robot/mode, blur/hidden tab, stale
telemetry, offline backend and stop release authority. Re-arm never restores it.
The 10 Hz loop/500 ms expiry are local demonstrations, not a network contract.
The local tick counter is not a wire sequence, lease generation or acknowledgment.

Live operation needs P3/P4's agreed authenticated expiring per-robot lease,
monotonic intent sequence, generation/session binding, stale/replay rejection,
independent bridge watchdog, stop acknowledgment/reconciliation and capability
telemetry. Unconfirmed stop/release must retain the backend rover slot. No live
adapter, endpoint, credentials or physical command is implemented in this draft.

The work remains frontend/** only on task/LcyShox/dashboard-prototype. The lead
opened a temporary chassis-only live contract at 60b4b91 on the vision review
branch; that transport/proxy is not implemented in this prototype. No live
control bridge or physical-control capability is claimed by this commit.
