# Object detection and tracking — AlanDD13

Own `vision/**`: camera ingest, detection, classification, pixel tracking, frame
serving, module dependencies, tests and recordings. Supply pixel detections and
matching frames to `mapping/`. Calibration, metric projection, rover localization
and route planning belong to `mapping/`.

First slice: process a recorded clip and emit the agreed pixel-track fixture.
Done: supported objects have stable track IDs, timestamps and scores; matching
frames are retrievable; missing/stale camera input is reported.

Read `AGENTS.md`, `docs/ONBOARDING.md` and `docs/CONTRACTS.md` before starting.
Add actual install/run/test commands here when implementing the first slice.
