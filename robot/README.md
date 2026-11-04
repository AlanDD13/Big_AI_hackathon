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
