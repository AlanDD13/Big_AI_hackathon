# Manual control boundary

Status: future contract, not robot simulator slice 1, not implemented, and not
hardware-validated. No route in this file is to be built until a later lead
commit says slice 2 is open. Vision does not gain live driving controls.

Owners: `LcyShox` dashboard input, `zekihxh` lease and mission exclusion,
`platonyesin-dev` bridge watchdog, `Tezqx` pixel-to-map routes. Alan records
the boundary the four reviews already share.

Platform reported by @LcyShox on 2026-11-04, comment 6634007814: the human
confirmed RoboMaster EP Core (`rmepcore`). That selects the SDK family for
later manual control. It does not mean this repository has driven the robot.

## Authority

One authenticated lease per rover. The lease has `leaseId`, `roverId`,
`generation`, and `expiresAt`. It is mutually exclusive with an autonomous
command and with `HELD`, `PICKING`, `STOPPED` while the stop is unconfirmed,
and an unknown link. An unknown or unconfirmed stop does not grant a new lease.
Rearm does not resume manual motion.

Manual velocity and an autonomous command must not execute in the same interval.
Expired, replayed, out-of-order, or wrong-generation input causes no motion.

## Velocity

A velocity intent carries `leaseId`, `generation`, a strictly increasing
`sequence`, and `expiresAt`. For the simulator shape, expiry is at most 200 ms
after issue. That number is not a measured radio or firmware limit.

The unit convention for a later EP Core slice is the SDK `drive_speed` shape
reported with that confirmation: body frame `x` metres/second forward, `y`
metres/second right, and yaw degrees/second. Relative arm jogs, if a later
slice includes the arm, are millimetres. No maximum speed is frozen because
this team has not measured the hardware. Frontend-only caps stay labelled as
simulation.

The SDK timeout is a host threading timer. It is not an independent physical
fail-stop. The bridge watchdog and local stop remain mandatory and must not be
described as that SDK timer. Blur or input-loss release is best effort from the
browser. The bridge stops when no fresh intent arrives before `expiresAt`, even
if the release packet is lost. Local stop and the watchdog outrank a late intent.

## Overhead click

The click is not a motor command. The request carries `cameraId`, `sessionId`,
`geometryVersion`, `frameId`, and the pixel. Backend asks mapping for a
calibration-checked, collision-checked route in the current map version.
Robot receives only that route, or a rejection. Pixels are never forwarded as
SDK coordinates.

Arm, gripper, and gimbal controls stay out of this boundary until a capability
report names them.

Fixture shape only: `contracts/fixtures/manual-control.json` (`FUTURE_NOT_SLICE_1`).
