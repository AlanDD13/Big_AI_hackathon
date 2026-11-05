# Local detector timing, 2026-11-05

The MPS detector batches the four equal-size zoomed tiles and transfers each
image's boxes to CPU once. The full image, four crops, 960-pixel inference
size, thresholds, original-pixel coordinates and class/exclusion handling are
preserved. CPU/CUDA keep sequential tiles; only MPS batching was measured.
This change does not make detector labels into Grok identification.

Measured on Alan's Apple Silicon Mac with PyTorch 2.15.0 and local
`yoloe-26s-seg-pf.pt`, using `mapping/new_photo.jpg` (saved image, not live).
Each complete-detector measurement excluded three warmups and used 12 samples.
Baseline is vision source `6948776`, with default profile, all tiles enabled.

| Experiment | Before median | After median | Decision |
| --- | ---: | ---: | --- |
| Tile calls only, first comparison | 143.65 ms | 135.02 ms | Keep MPS tile batching |
| Tile calls only, repeated comparison | 143.82 ms | 134.88 ms | Gain reproduced |
| Complete detector: batching plus box transfer | 159.11 ms | 139.47 ms | Keep both; 12.3% lower latency |
| Complete detector, repeated comparison | 170.20 ms | 149.57 ms | 12.1% lower latency reproduced |

The first complete runs ranged 158.24–160.68 ms before and 138.20–140.74 ms
after. All 12 curated outputs had identical labels and scores; maximum box
coordinate delta was 0.000074 pixels. Outputs were stable within each run.
The repeat had more timing noise (162.20–233.44 ms versus 139.93–185.14 ms).
These are measurements on one image, not a detection-accuracy evaluation or
proof of live camera FPS. MPS batch memory use is higher than sequential tiles.

Reproduce from the checkout being measured, using an existing image and local
weights (the benchmark makes no network, Grok, backend or robot calls):

```bash
python -m vision.benchmark_inference mapping/new_photo.jpg \
  --model /absolute/path/to/yoloe-26s-seg-pf.pt --device mps \
  --image-size 960 --warmup 3 --runs 12
```

Use the same environment, image, weights and options for both revisions. The
JSON includes the image SHA-256, sample times and detections for comparison.
Test correctness with `python -m pytest -q vision/tests tests`. New tests cover
odd-size tile offsets, full-frame living detections, all tile classes, CPU
sequential behavior and rejection of mismatched batch results.

## Tezqx viewer handoff

`mapping/run_realtime_stream.py` at `98c6441` remains Tezqx-owned. It hardcodes
CPU inference and port 5000, which conflicts with macOS Control Center on this
Mac. A separate test using its 416-pixel input settings and half-size saved
frame measured 23.88 ms CPU versus 16.03 ms MPS, with six boxes on both.
The camera at `192.168.5.146:8081` was TCP reachable but `/video` returned 401;
no local camera password was configured. Live smoothness remains unverified.
These blockers and requests for configurable device/port and no-Grok test mode
were posted on board comment 6647089382. No mapping files were edited.
