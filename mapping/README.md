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

The measured-floor projection is not implemented. `grid_mapper.py` splits the
camera frame into a 4 by 4 grid of estimated 0.5 m cells. Row A is the top of
the frame and column 1 is the left. The origin is the bottom centre of the
frame. `pixel_to_sector(855, 423, 1920, 1080)` is B2, at (-0.25, 1.25) m.
The point stays uncalibrated and is not MappingEvidence. From this directory:

```
python -m unittest test_mapper.py
```
