# EP Core fleet setup interface

Connected → Fleet prepares browser-local robot profiles. Add/edit a name and
the 14-character serial from the EP Core label or DJI app. Profiles persist in
localStorage under `rovr.epcore.setup.v1`; they contain no passwords or tokens.
Removing a setup removes only browser metadata, never a backend registration or
an active robot session. Serial format validation is not physical verification.

## Actual connection path

Hardware: DJI RoboMaster EP Core (`rmepcore`). The human selected the same
router/LAN, rather than direct AP or USB, for this setup.

The team’s robot module uses STA and UDP. `robot/check_connection.py` passively
discovers the requested serial, negotiates an explicit route, refuses a failed
route/default-client fallback, and verifies `get_sn()` and firmware before
accepting that device. The dashboard never guesses a default IP or connects to
the first discovered robot. The robot module owns discovery and SDK sessions.

The current agreed topology is Windows dashboard → backend → P4 robot bridge
on the SDK host → EP Core over the router. The validated hardware SDK host is
the robot owner's Mac. The draft manual-control contract permits moving P4's
bridge to Windows after an explicit SDK-host handover; frontend code never
becomes an SDK host. The team currently permits only one active SDK host on the
LAN. Separate robot profiles do not claim simultaneous multi-robot capability.

Joining a robot to the router remains DJI setup outside this browser. Do not
store router passwords or pairing QR payloads in frontend profiles. Close the
DJI app/other controllers before the robot owner opens the SDK session.

Robot owner confirmed these details on the board at comment #6637881403:
macOS arm64 / Python 3.11.4, firmware 01.01.1150 in hardware tests, UDP broadcast
discovery on port 40926, dynamic address and serial verification, no live :8004
bridge while watchdog review/physical stop-confirmation remain unresolved.
The firmware value is historical evidence, not current UI telemetry.

Sources checked on 2026-11-04:
- `robot/check_connection.py` and `robot/HARDWARE_CHECKS.md` on main b15053c.
- `contracts/manual-control-v1.md` on lead draft branch
  `task/AlanDD13/vision-pipeline` (51e0e8b), Single Active DJI SDK Host & LAN Bridge Rule.
- [Official DJI SDK connection guide](https://robomaster-dev.readthedocs.io/en/latest/python_sdk/connection.html).

## Readiness and implementation boundary

No registration/discovery/connect HTTP routes have been published on main.
`robots` currently comes back empty in the backend snapshot. Fleet displays its
record count and backend transport availability, without assuming that an HTTP
connection proves any robot is connected. Local profiles are not silently
associated with backend rows; a canonical identity/telemetry contract is needed.

Saved profiles show Setup only / Connection unverified. Battery, pose,
arm/gripper, control owner and stop confirmation remain unknown. Connect,
take/release control, stop and re-arm are disabled; this feature makes no robot
writes or SDK calls. The main camera widget stays empty.

The robot owner later reported passing the physical stop-confirmation bench,
and watchdog PR #10 merged on main at 94cb8bc. That merge supplies bench tooling,
not a live HTTP bridge. Backend/robot owners must publish the bridge/API and
identity/status contract before those actions can be enabled. In
particular, SDK speed writes are not a stop acknowledgement; no status is
fabricated from a click or elapsed time. The existing joystick/overhead widgets
remain isolated in Demo mode.

Verification: frontend tests and production build; browser checks for add,
invalid serial, duplicate serial, edit, reload persistence, removal, keyboard
modal handling and responsive layout. No real robot connection is attempted.
