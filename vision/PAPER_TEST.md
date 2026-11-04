# Paper recognition and pickup checks

Use Grok **4.7** via OpenRouter, not the deprecated 4.1 Fast model. Runtime
selection is GROK_VISION_PROVIDER=openrouter and
OPENROUTER_VISION_MODEL=x-ai/grok-4.7. The key is in ignored vision/.env on
Alan's machine; no secret is stored in this document or shared contracts.
The adapter uses low reasoning (4.7 requires it), strict JSON schemas, bounded
responses, two concurrent calls and a whole-call 20-second timeout. It does not
fall back to a different model family.

## What was actually tested

On November 4, 2026, two real OpenRouter Grok 4.7 calls on Alan's **116x40**
paper screenshot completed together in 6.177 seconds:

- Classification: TRASH, INANIMATE, not carried, MEDIUM confidence; description
  says a crumpled white object consistent with paper or tissue on a wooden floor.
- Separate visual advisory: CRUMPLED, POSSIBLE grasp surface. This is not a
  measurement of size, weight, jaw fit, reach, thickness or actual pickup success.
- No movement or gripper action was performed. No metric scale was available.

The old prompt-free YOLOE called the crop "comet" at 0.978. Higher input
resolution alone did not fix that error. A newly prepared paper-prompted model
also failed to produce a reliable paper detection on this tiny, clipped crop
(its only low-threshold result was cat at 0.1178). Therefore **paper proposal
recall is not yet verified**. Do not claim this screenshot proves real camera
accuracy. Use a full-resolution frame/short recording with the whole object
visible for the next test; semantic classification cannot recover an object that
never receives a detector proposal.

## Retest the live pipeline

The optional `paper` detector profile uses explicit paper/trash prompts,
retains person/animal/hand categories, lowers the small-object area cutoff, and
uses 960-pixel inference unless VISION_IMAGE_SIZE overrides it. It still needs
full-frame testing; do not globally lower thresholds until false positives and
living-object exclusions have been checked.

Prepare text embeddings once (already done on Alan's machine):

```bash
uv pip install --python vision/.venv/bin/python 'git+https://github.com/ultralytics/CLIP.git@7ffa84b3bfa40c42ecc1c77147a855e69cb2dd40'
vision/.venv/bin/python -m vision.prepare_paper_model
```

Weights/text encoder stay under ignored vision/.local. Saved paper weights
contain the prompts so normal inference does not need to rerun the text encoder.
For another machine, prepare them locally rather than adding binary weights to Git.

Start the service using the normal LAN TLS arguments plus
`--detector-profile paper` (or set VISION_DETECTOR_PROFILE=paper). Reopen
`python -m vision.live_view` after restarting the server; already-running Python
processes will not reload these source changes. The viewer now shows Grok's
description, YOLO's independent label/score, visible shape advice and physical
pickup uncertainty separately. Images continue updating even with zero detections.

In live_view, put a measured reference beside the target at the same floor
depth, press **c**, select the reference's horizontal width and enter that actual
width in millimetres in the terminal. A ruler segment is suitable. This enables
a local size screen, not mapping calibration. Moving/restarting/changing the
camera invalidates the scale when its session/geometry/dimensions change.
SIZE_FITS means only the 2D size screen passed; pickup remains UNVERIFIED.

One-shot Grok test on a saved image (two billed model calls per execution):

```bash
vision/.venv/bin/python -m vision.inspect_image path/to/full-frame.png --env-file vision/.env
```

Visual pickup fields are advisory additions to the producer contract; backend
and dashboard owners must adopt them before live ingestion. All stationarity,
metric-quality, living/carried-object and late-result gates remain mandatory.
