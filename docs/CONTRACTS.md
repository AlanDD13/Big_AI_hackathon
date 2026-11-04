# Shared contracts

Owner: `AlanDD13`. Status: **draft for team agreement, not a frozen API**.
Persistence decision: `zekihxh` owns a shared Supabase Postgres database,
replacing the initial SQLite proposal. Backend owns schema/migrations and
durable object/event/classification/alert/mission state. All five agents use
documented APIs or individually authorized project access; secret/service-role
keys remain backend-only. The human selected the existing **AlanDD13's Project**
(`buojgmoumyzqxmerckjm`, `eu-west-1`) on 2026-11-04. Backend must inspect its
existing schema and preserve unrelated data before adding hackathon tables.
Project selection does not grant access: individual teammate/agent permissions,
runtime credentials and migration access still need verification by the backend
owner. Never put credentials in Git, board comments or frontend code.
The vision producer's versioned interface is now specified in
[`contracts/vision-v1.md`](../contracts/vision-v1.md), with JSON schemas and
fixtures. Consumer acknowledgement is pending; this does not freeze unrelated
mission/robot-control endpoints. Use that document for cameras, static-target
gates, classifications and immediate dashboard events.
This records the [FigJam proposal](https://www.figma.com/board/bmW81xDifPQz5Icv98D8Hd/Big-AI?node-id=25-1003).

## Demo and module boundaries

Proposed MVP: one rover, one fixed camera, a calibrated floor area, known objects
with tested pickup profiles, marked drop zones and one active mission at a time.
Pickup, installed sensors, camera placement and connectivity need physical tests.
If pickup is unreliable, agree a labelled detect/locate/approach/notify demo.

| Producer | Consumer | Proposed boundary |
| --- | --- | --- |
| `vision/` | `mapping/` | Pixel detections, stable tracks, timestamps, matching frames |
| `mapping/` | `backend/` | Metric observations, rover pose, map and waypoint plans |
| `frontend/` | `backend/` | Operator REST requests and SSE status updates |
| `backend/` | `robot/` | Commands, fresh pose, stop/cancel and execution status |
| `backend/` | Grok | Structured intent; validate before dispatch |

Vision owns the camera connection and serves matching frames to mapping;
the modules must not compete for one USB camera. Mapping plans routes; robot
follows them, converts map movement to robot commands and enforces local limits.
Only `robot/` connects to the physical rover.

## Proposed wire surface

- Vision to mapping: `POST /detections`; `GET /frames/{frameId}` on vision.
- Mapping to backend: `POST /api/observations`, `POST /api/poses` on backend.
- Backend to mapping: `GET /map`, `POST /plans` on mapping.
- UI reads: `GET /api/map`, `/api/objects`, `/api/missions`, `/api/robots`,
  `/api/events`. Writes: `POST /api/missions`, `/api/missions/{id}/cancel`,
  `/api/robots/{id}/stop`.
- Bridge: `POST /commands`, `/poses`, `/stop`, `/rearm`; `GET /commands/{id}`.
  Robot reports to backend `POST /api/robot-events`.

Tracks carry camera/frame/track IDs, capture time, class, detector score, pixel
bounding box and floor-contact pixel. Mapping adds map/calibration IDs, metric
floor point and uncertainty; invalid position is null, never a fabricated origin.
Rover pose uses metres and yaw radians in the same map frame. Fix origin, axes
and timestamp format in schemas before integration.

Commands carry stable command/mission IDs, pickup profile, target, waypoints,
destination, base, map version and deadline. A physical command timeout means
unknown until reconciled: do not blindly repeat it. Grok proposes an intent;
backend validation and normal mission gates remain mandatory. Recognition
confidence does not prove graspability.

## Required before parallel implementation

- [ ] All owners accept the MVP and producer/consumer boundaries.
- [ ] Lead lands `contracts/` schemas, request/response fixtures, enums, error
  envelope, event IDs and retry/idempotency semantics agreed with consumers.
- [ ] Mapping/robot agree frame transforms, pose freshness and waypoint format;
  vision/mapping agree matching-frame retention and delivery.
- [ ] Owners agree ports, environment variable names and timeouts.
- [ ] Module READMEs contain real install/run/test commands and local mocks.
- [ ] Lead documents the integration command, merge order and demo reset/fallback.

Do not commit credentials. Environment docs contain names/examples only.
Keep durable decisions here and link them from the board.
