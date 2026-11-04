# Vision v1 consumer contract

Owner: AlanDD13. Status: implemented producer interface on
`task/AlanDD13/vision-pipeline`; consumer review pending. This branch is NOT main.
No consumer may assume a requested interface is merged or implemented by peers.

## Ownership and transport

- Alan: `vision/`, Grok image classification, shared schemas and this contract.
- Tezqx: pixel-to-map transforms, map/version/calibration data, metric positions.
- zekihxh: Supabase Postgres, durable object/classification/alert/mission state and
  dashboard SSE. Vision never writes the backend database.
- LcyShox: dashboard showing observations immediately, scores and status updates.
- platonyesin-dev: sole DJI connection; rover frame endpoint and physical control.

All vision `/api/` endpoints require `Authorization: Bearer VISION_TOKEN` except
camera upload, which also accepts the camera-specific token derived by the CLI.
Peer URLs and PEER_TOKEN are operator configuration, never request-supplied URLs.
Phone camera pages use HTTPS on port 8443; local-only development can use HTTP
on 127.0.0.1. Phone capture cannot use LAN plain HTTP. Phones need not share an
Apple account. Each phone must keep the page open and camera stationary.

## Frames, clocks and identity

IDs are nonempty ASCII letters/digits/underscore/hyphen, at most 128 characters.
Frame IDs are nonnegative integers, strictly increasing within a camera session;
the first is 0. Restarting a stream creates a new session. Changing orientation,
resolution, zoom/crop or mounting creates a new geometry version and invalidates
calibration. Camera movement must be reported by the operator as a new geometry;
automatic camera-motion detection is not claimed.

`capturedAt` and `receivedAt` are UTC UNIX seconds (JSON numbers, fractions
allowed). Capture time is source reported; phone clocks are NOT assumed synced.
Freshness and intersample gaps are measured using vision `receivedAt` for the
exact referenced frame. `timestampBasis` is SOURCE or RECEIVE; RECEIVE is a
timestamp on receipt, not proof of sensor exposure time.

Canonical JPEGs are upright and unmirrored. Coordinates use their actual pixel
dimensions (origin top-left, x right, y down); boxes are [x1,y1,x2,y2]. Any detector
resize/letterbox is reversed before publication. Contact points are estimates,
not measured dimensions. Mapping validates floor contact and its calibration.

Vision retains frames up to 60 seconds within a 64 MiB total memory budget;
memory pressure may evict earlier. Consumers fetch promptly. Missing/expired
frames return 404, never a substituted newer image under the old frame ID.

## Vision to mapping: POST /detections

`MAPPING_URL` is mapping's service base URL. Body is the event envelope below,
type `frame.detections`; payload contains cameraId, sessionId, geometryVersion,
frameId, cameraKind (FIXED or ROVER), capturedAt, receivedAt, timestampBasis,
width, height, frameRef, and tracks[]. Every track contains trackId, objectId,
detectorLabel, detectorScore, bboxPx, contactPointPx, motionState,
pixelStationarySeconds and exclusionReason. Empty tracks[] is meaningful.

`frameRef` is relative to the vision base URL:
`/api/frames/{cameraId}/{sessionId}/{frameId}`. It identifies that exact JPEG.
The object key is cameraId:sessionId:trackId. No cross-camera identity fusion is
implemented in vision. ROVER observations never establish floor stationarity.

## Mapping to vision: POST /api/mapping-evidence

Continue publishing metric observations to backend as separately agreed. Also
send one result per referenced track/frame to vision for the eligibility gate.
See `vision-mapping.schema.json` and `fixtures/mapping-evidence.json`.

Required keys: cameraId, sessionId, geometryVersion, frameId, trackId, capturedAt,
receivedAt, mapId, mapVersion, calibrationId, calibrationValid, valid, invalidReason.
Use invalidReason:null for valid observations. A valid result also
needs floorPointM:[x,y] and positionErrorM >= 0. An invalid result uses
floorPointM:null and may use positionErrorM:null. Never replace unknown position
with [0,0]. `capturedAt` must echo the original frame exactly. Map origin/axes
are fixed by Tezqx's map definition; point coordinates are metres.

Unknown/expired/incompatible references return 409 and cannot create targets;
malformed fields return 422. Repeated/non-increasing mapping frame IDs are
rejected. Map/calibration/version changes reset that track's metric window.

Stationary: BOTH >=10 observations AND span >=5 seconds (11 samples at exactly
2 FPS); maximum intersample gap
1.0 second, maximum observation age 1.0 second; each uncertainty <=0.025 m;
every point stays within 0.05 m of the first valid episode point. Keep this anchor
fixed until reset; do not slide it forward. The age gate applies only to the
newest sample at evaluation, not historical samples in the five-second episode.
The proposed 200 ms gap
cannot work with 3 FPS capture/2 FPS inference, so v1 uses 1 second. Invalid,
stale, out-of-area, gapped or changed-identity/calibration samples reset evidence.
Pixels are a preliminary filter only. People/animals/carried/uncertain subjects
are excluded separately; standing still alone never makes them eligible.

## Vision to backend and dashboard

All event envelopes contain schemaVersion:"vision.v1", eventId (unique within
and across process boots), sequence, type, timestamp and payload. See
`vision-event.schema.json`. Ingest must deduplicate eventId and join by objectId;
classification/mapping may arrive after the initial observation.

| Vision event | Backend destination | Required dashboard behavior |
| --- | --- | --- |
| object.observed | POST /api/detections | Show immediately as Observing; do not wait for Grok/map |
| object.classified | POST /api/classifications | Update category, description, confidence and provider |
| object.updated, alert.* | POST /api/vision-events | Update validity, revoke eligibility, deduplicate alerts |

Object payload includes objectId and all camera/track/frame IDs, bboxPx,
detectorLabel, detectorScore [0,1], motionState, pixelStationarySeconds,
metricStationarySeconds, classificationStatus, classification,
classificationProvider, mapping, potentialTarget, revision, frameRef and
lastSeenAt. Detector score is not a calibrated probability. Grok confidence is
LOW/MEDIUM/HIGH, a separate model assessment; do not average the two.

Classification statuses: PENDING, COMPLETED, UNAVAILABLE. Classification fields:
category TRASH/POTENTIALLY_LOST_PROPERTY/OTHER/UNCERTAIN; subject_kind
INANIMATE/PERSON/ANIMAL/UNCERTAIN; carrying YES/NO/UNCERTAIN; confidence,
description, reason. Provider is grok or fixture. Fixture evidence is visibly
labelled and is not live model evidence. Only completed inanimate, not-carried,
medium/high-confidence trash/potentially-lost classifications pass semantics.

Object states: OBSERVING, STATIC_PENDING_REVIEW, POTENTIAL_TARGET, EXCLUDED, LOST.
`alert.potential_target` fires once on an eligibility transition; movement/loss/
staleness emits `alert.target_revoked`. A later valid episode may alert again.
Ordinary frames only update the list, not notification popups. Detector failures,
expired cameras and failed deliveries are operational states, not fabricated data.
Backend owns acknowledgement, alert persistence and optional hold/stop dispatch.
Revocation means HOLD_BEFORE_PICKUP; expected motion during authorized PICKING
is handled by mission state, not blindly turned into another pickup command.

Lead decision after checkpoint e3976c1: backend persistence uses shared Supabase
Postgres instead of SQLite. The human selected existing project
`buojgmoumyzqxmerckjm` (AlanDD13's Project, eu-west-1); inspect and preserve
existing unrelated data. Backend owns schema/migrations and project setup;
all teammates receive documented APIs, fixtures and individually authorized
project/agent access where needed. Secret/service-role credentials remain
backend-only and are never shared in Git, board posts or browser code.
Persist current objects, sampled observations, classifications, alerts, missions
and event IDs; evidence JPEGs use separate file/object storage with DB references.
Raw continuous video is not stored in database rows. Backend SSE relays updates
to the dashboard; browser never
needs a Grok key. Vision GET /api/objects and /api/events are diagnostic/consumer
interfaces, not a replacement dashboard or durable backend.

Outbound delivery uses a bounded queue and bounded retries. Vision reports
delivery failures; it cannot claim durable ingestion without backend acceptance.
Consumers must support snapshot/reconciliation and eventId deduplication.

## Rover frame contract

Robot bridge GET /camera/frame returns image/jpeg, no fabricated placeholder.
Headers: X-Camera-Id, X-Session-Id, X-Geometry-Version, X-Frame-Id,
X-Captured-At (UTC UNIX seconds) and X-Timestamp-Basis (SOURCE or RECEIVE).
One source frame ID always refers to the same captured image. Restart changes
session ID. No frame available: 503 with a safe error, not HTTP 200 stale imagery.
Vision only polls an operator-configured bridge URL with redirects disabled.

Vision POST /api/targets/confirm takes missionId, objectId, roverCameraId;
returns CONFIRMED/NOT_VISIBLE/UNCERTAIN plus candidateTrackId and evidence IDs.
Only currently eligible fixed-view targets can be confirmed. A moving rover
view never establishes static floor coordinates. No vision endpoint commands
motors. Late confirmations are rejected if target revision or evidence expires.

## Consumer acceptance

Review and acknowledge this branch before integration. Coordinate necessary
changes through Alan; do not edit another owner's directory. This interface
does not establish event-track approval or verified hardware capability.
