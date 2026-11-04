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
an API key classification is UNAVAILABLE, never simulated as a live result.

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
