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

The measured-floor projection is not implemented. `grid_mapper.py` only turns an
explicit burst of sector labels into a demo cell centre, and it marks that point
uncalibrated. Camera pixels are not metres. From this directory:

```
python -m unittest test_mapper.py
```
