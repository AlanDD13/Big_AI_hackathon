# Object detection and tracking — AlanDD13

Own `vision/**`: camera ingest, detection, classification, pixel tracking, frame
serving, module dependencies, tests and recordings. Supply pixel detections and
matching frames to `mapping/`. Calibration, metric projection, rover localization
and route planning belong to `mapping/`.

First slice: process a recorded clip and emit the agreed pixel-track fixture.
Done: supported objects have stable track IDs, timestamps and scores; matching
frames are retrievable; missing/stale camera input is reported.

Read `AGENTS.md`, `docs/ONBOARDING.md` and `docs/CONTRACTS.md` before starting.
## Intermediate implementation checkpoint

Read [CHECKPOINT.md](CHECKPOINT.md) before using this branch. It is a tested work
in progress, not ready for robot dispatch or a claim of live camera/Grok validation.
Shared producer interfaces: [vision-v1](../contracts/vision-v1.md).

From the repository root, install the locked environment with uv:

```bash
uv sync --project vision --extra test --extra ml
vision/.venv/bin/python -m pytest -q vision/tests
```

For local-only startup, set a fresh VISION_TOKEN of at least 16 unpredictable
characters in your terminal, then:

```bash
vision/.venv/bin/python -m vision --host 127.0.0.1 --port 8443
```

Set VISION_MODEL to the local checkpoint path if it is already downloaded.
The first inference may download weights; warm up before measuring performance.
Grok uses XAI_API_KEY and optional GROK_VISION_MODEL (default grok-4.7). Without
an xAI or OpenRouter API key classification is UNAVAILABLE, never simulated as a live result.

For identification testing, set `VISION_AUTOMATIC_CLASSIFICATION=false` before
starting the server. Frames and local detection/tracking continue, but stationary
objects trigger no automatic Grok calls or pickup assessments. Use an explicit
`POST /api/classify-object` with `includePickup:false` for one identification call
per requested test; do not poll or automatically retry this billed endpoint.
`GET /health` reports `automaticClassificationEnabled` and the pending automatic
task count. The default remains `true` for the normal pipeline. This flag does
not disable explicit peer classification/confirmation requests.

The `/camera` page has a **Grok identification + pickup estimate** switch.
After Start, it reads the server-confirmed state for that camera. ON enables
the existing stationary-object assessment (two provider calls per new eligible
episode); OFF prevents new automatic calls and cancels pending automatic tasks
for that camera. Requests already received by the provider may still be billed.
Completed assessments are reused for the same episode. Explicit stateless peer
requests are independent of this switch. Runtime overrides reset to the environment
default when the service restarts. Pickup estimates never verify a physical grab.

The /camera page is an operational camera-source page, not the main dashboard.
For independent phones, use LAN HTTPS with --cert and --key; plain LAN HTTP is
rejected by the CLI. Configure camera IDs with VISION_CAMERAS, for example
phone-1:FIXED,phone-2:FIXED,rover-1:ROVER. Generate a scoped upload token with:

```bash
vision/.venv/bin/python -m vision --camera-token phone-1
```

VISION_LOCAL_SOURCES is a JSON object mapping configured fixed camera IDs to
OpenCV device indexes or video files, e.g. {"mac-1":0}. ROVER_BRIDGE_URL enables
the configured bridge adapter. Only robot/** owns DJI; vision consumes its JPEGs.
Peer URLs MAPPING_URL/BACKEND_URL are optional until integration. Configuring
an outbound peer or rover bridge requires PEER_TOKEN distinct from VISION_TOKEN.

Cursor continuation: [handoff](../docs/CURSOR_HANDOFF.md) and
[project skills/rules](../.cursor/README.md).

Paper detection, Grok 4.7 over OpenRouter, live test results and the viewer's
size calibration/pickup advisory are documented in [PAPER_TEST.md](PAPER_TEST.md).
