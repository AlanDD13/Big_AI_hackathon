# Mapping and route planning — Tezqx

Own `mapping/**`: calibration, projection, rover localization, map/zone config,
route planning, module dependencies, tests and fixtures. Consume pixel detections
and matching frames from vision. Publish metric observations/pose to backend and
answer map/route requests. Physical execution belongs to `robot/`.

First slice: project fixture pixel observations onto a measured floor map, locate
a rover marker and return waypoints to a drop zone. Done: accuracy is checked
against measured points; stale/out-of-area/unknown positions fail explicitly;
all output uses the agreed map frame and version.

Read `AGENTS.md`, `docs/ONBOARDING.md` and `docs/CONTRACTS.md` before starting.
Add actual install/run/test commands here when implementing the first slice.
