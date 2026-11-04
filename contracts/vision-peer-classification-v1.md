# Vision classification for mapping and backend

Owner: AlanDD13. Producer: vision/**. Consumer: Tezqx (mapping), optional backend.
Endpoint is stateless image analysis; it does not bypass tracking or authorize pickup.

Alan's current camera test base is **https://192.168.5.192:8443**, not port 8002.
`GET /health` is public. Other routes require bearer auth. Use PEER_TOKEN via
the agreed private channel (Signal); never post it or the OpenRouter key on the
board. Pin the public dev certificate at infrastructure/certs/vision-dev.crt
through VISION_CA_CERT. That file is the public certificate only. It expires
2026-11-07 16:47:08 GMT and names IP 192.168.5.192. The private key is not in
git. Do not copy lan.key or disable verification.

## Detect, crop, classify, map

1. `POST /api/detect-frame` accepts JPEG and returns YOLO detections only.
2. Select/crop a non-living candidate from that **same original frame**.
3. `POST /api/classify-object` accepts application/json:

```json
{
  "requestId": "camera-session-frame-track",
  "cropImageBase64": "<JPEG or PNG bytes encoded as base64, no data URL prefix>",
  "contextImageBase64": "<optional original context frame, base64>",
  "includePickup": false
}
```

cropImageBase64 is required. Context defaults to the crop. requestId is optional
(1–128 characters) and is echoed for caller correlation, not server idempotency.
includePickup defaults false: mapping needs classification without paying for a
second grasp assessment. Set true when the robot-specific estimate is needed.
This second assessment uses the target crop, to prevent a neighbouring can from
being mistaken for paper. The general classification still sees context for
living/carrying checks.

Response 200 has provider:grok, transport:openrouter, model:x-ai/grok-4.7,
assessmentId, requestId and:

```json
{
  "classification": {
    "status": "COMPLETED",
    "result": {
      "category": "TRASH",
      "subject_kind": "INANIMATE",
      "carrying": "NO",
      "confidence": "MEDIUM",
      "description": "Crumpled white paper",
      "reason": "Visible discarded paper-like object"
    }
  },
  "visualPickupAdvisory": {"status": "NOT_REQUESTED", "result": null},
  "advisory": true,
  "pickupVerified": false,
  "metricSizeKnown": false,
  "dispatchAuthorized": false
}
```

With includePickup:true, visualPickupAdvisory uses COMPLETED/UNAVAILABLE and its
result contains object_form, visual_suitability, robot_grasp_estimate
(LIKELY/UNLIKELY/UNKNOWN), estimate_confidence, reason, assumptions and required_checks.
Grok receives published EP Core jaw/force/travel/payload parameters, labelled
uncalibrated for this specific unit. These are estimates, not verified limits.

Image limits: JPEG/PNG only, <=4 MiB each, <=6 MiB decoded aggregate, <=16M
pixels per image; JSON <=8 MiB. Two peer requests admitted at once, 5-second body
read deadline, 30-second OpenRouter whole-call deadline (including queue wait).
The helper allows 35 seconds. Excess capacity/provider timeout/unavailable model
returns 503, never fabricated classification; a partial result may accompany 503.
Other errors: 401 wrong bearer, 415 content type, 413 body limit, 422 malformed
fields/images. Redirects are not followed by the client helper.

## Python client (no OpenRouter key)

```python
from vision.cctv_monitor import detect_frame_objects, classify_object_remote

# Environment on Tezqx's machine:
# VISION_REMOTE_URL=https://192.168.5.192:8443
# PEER_TOKEN=<current shared value received through Signal>
# VISION_CA_CERT=infrastructure/certs/vision-dev.crt
result = classify_object_remote(object_crop_bgr, context=original_frame_bgr)
classification = result["classification"]["result"]
```

Keep original camera/session/geometry/frame/track association and reject a late
result if that association changed. Cache classifications per object episode;
do not call Grok for every frame or blindly retry a timed-out billed call.

Mapping may filter TRASH/INANIMATE/NO and assign an agreed demo sector from
the original bbox centre. LOW/UNCERTAIN evidence must not become a target.
The 0.5m equal-strip grid is an **uncalibrated demo label**, not MappingEvidence.
Grok does not invent sector, floor coordinates or uncertainty. Metric projection
and physical stationarity remain mapping/backend responsibilities.

Live-camera diagnostics, fixtures and backend missions are separate. The
current camera-only test server has all peer dispatch and rover connections
disabled; classification responses cannot move hardware.
