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
camera frame into a 4 by 4 demo grid of 0.5 m cells and turns a sector label
into that cell's centre. `pixel_to_sector` names the cell that contains a YOLO
box centre. Row A is the bottom of the frame and column 1 is the left. The
point stays uncalibrated and is not MappingEvidence. From this directory:

```
python -m unittest test_mapper.py
```
