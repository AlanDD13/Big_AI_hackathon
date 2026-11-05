# Shared contracts

Owner: `AlanDD13`. Status: **draft for team agreement, not a frozen API**.
Persistence decision: `zekihxh` owns a shared Supabase Postgres database,
replacing the initial SQLite proposal. Backend owns schema/migrations and
durable object/event/classification/alert/mission state. All five agents use
documented APIs or individually authorized project access; secret/service-role
keys remain backend-only. The human selected the existing **AlanDD13's Project**
(`buojgmoumyzqxmerckjm`, `eu-west-1`) on 2026-11-04. Backend must inspect its
existing schema and preserve unrelated data before adding hackathon tables.
Project selection does not grant access. The existing `public` schema in
`buojgmoumyzqxmerckjm` is actively used by `adaptive-python-mentor` (24 tables
and 17 RPCs) and **must not** be used or modified by this project. All hackathon
tables, views, sequences and routines must live in the dedicated Postgres schema
**`rover_backend`** (`SUPABASE_DB_SCHEMA=rover_backend`). The schema bootstrap
script is [`infrastructure/supabase-rover-backend-schema.sql`](../infrastructure/supabase-rover-backend-schema.sql)
and shared environment variable names are in [`.env.example`](../.env.example).
`rover_backend` revokes all privileges from `PUBLIC, anon, authenticated` and is
not exposed via PostgREST; `backend/` connects directly over Postgres using the
separate migration role (`BACKEND_MIGRATION_DATABASE_URL`, `rover_backend_migrator`),
the narrower DML-only runtime role (`BACKEND_DATABASE_URL`, `rover_backend_runtime`),
and `BACKEND_TEST_DATABASE_URL` for tests, all owned by `zekihxh`.
`SUPABASE_SERVICE_ROLE_KEY` does not replace the narrower `BACKEND_DATABASE_URL`
runtime login and never reaches browser or peer modules.
Frontend, mapping, and robot use backend APIs and get no database role and no
service key. Never put database credentials, service keys, model API keys, or private
keys in Git, board comments, or frontend code. The vision `PEER_TOKEN` is the
exception: post its new value on the board when it changes.
The vision producer's versioned interface is specified in
[`contracts/vision-v1.md`](../contracts/vision-v1.md). Stateless peer
classification for mapping is in
[`contracts/vision-peer-classification-v1.md`](../contracts/vision-peer-classification-v1.md).
The live two-camera operator console, authenticated notices/audio, completed
route steps and model metadata are specified in
[`contracts/operator-console-v1.md`](../contracts/operator-console-v1.md).
Native xAI configuration and model usage evidence are documented in
[`docs/GROK_MODELS.md`](GROK_MODELS.md); active OpenRouter transport is removed.
Camera test control (implemented in merged PR #22): `GET/PUT /api/cameras/{cameraId}/grok`
accepts that camera's scoped upload bearer or the vision master bearer, never
another camera's scoped token or PEER_TOKEN. PUT requires exactly
`{"enabled": true}` or `{"enabled": false}` (strict boolean); both return
`{"cameraId":"phone-2","enabled":false,"includePickup":true,"pickupVerified":false}`
with the actual camera ID and state. Errors: 401 invalid bearer, 404 unconfigured
camera, 422 invalid body. ON runs existing stationary-track classification plus
pickup advice; OFF prevents new automatic calls and cancels that camera's
pending automatic tasks. Already-sent provider requests may be billed. Cached
completed assessments remain; explicit stateless peer calls are independent.
Overrides are in-memory and restart from VISION_AUTOMATIC_CLASSIFICATION.
This control does not authorize movement or pickup. Health exposes per-camera
automaticClassificationByCamera and aggregate automaticClassificationEnabled.
The separately armed measured-route integration is specified in
[`contracts/camera-recovery-v1.md`](../contracts/camera-recovery-v1.md): any
configured fixed camera, numeric grabbability estimate, supported can profile,
durable scene/job deduplication, return/release, and frontend rejection/lifecycle
alerts. Its explicit measured setup does not change generic mapped mission
eligibility. Consumers must be upgraded before enabling its producer.
Backend durable
snapshot/SSE/session rules are in
[`contracts/backend-stream-v1.md`](../contracts/backend-stream-v1.md).
Robot simulator slice 1 is in
[`contracts/robot-simulator-v1.md`](../contracts/robot-simulator-v1.md).
Temporary live manual chassis driving (`MANUAL_CHASSIS_SLICE_1`) is specified in
[`contracts/manual-control-v1.md`](../contracts/manual-control-v1.md) (chassis-only
precision driving with P4 as sole DJI `rmepcore` owner, `<= 300 ms` bridge
watchdog, and overhead/manipulation kept `SIMULATED_ONLY`).
The mission queue checkpoint is
[`contracts/mission-queue-v1.md`](../contracts/mission-queue-v1.md). It stores
`QUEUED` only and does not call the robot. The uncalibrated demo grid is
[`contracts/demo-grid-v1.md`](../contracts/demo-grid-v1.md).
Simulator-first recovery and proposed adapter boundaries are in
[`contracts/recovery-tools-v1.md`](../contracts/recovery-tools-v1.md).
This adds no physical endpoints or dispatch authority; P3/P4 agreement and
hardware acceptance remain required. See
[`docs/LANGGRAPH_PHYSICAL_HANDOFF.md`](LANGGRAPH_PHYSICAL_HANDOFF.md).
These contract documents are integrated on `main`; draft status and physical
capability restrictions still apply. In-person navigation interface agreement
is still required before implementing the newly assigned sector flow.
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
Only `robot/` (`platonyesin-dev`) connects to the physical rover via the DJI SDK
(`rmepcore`), and **only one machine on the LAN (STA) may open a DJI SDK session
at a time**. Other machines on the same LAN (such as `frontend/` on Windows or
`backend/`) talk to `robot/`'s HTTP bridge via `ROBOT_BRIDGE_URL`
(`http://<p4-lan-ip>:8004` with server-side `PEER_TOKEN`), never by opening a
second DJI SDK connection.

## Proposed wire surface

- Vision to mapping: `POST /detections`; `GET /api/frames/{cameraId}/{sessionId}/{frameId}` on vision.
- Mapping to backend: `POST /api/observations`, `POST /api/poses` on backend.
- Backend to mapping: `GET /map`, `POST /plans` on mapping.
- Dashboard (`frontend/` <-> `backend/`, per [`contracts/backend-stream-v1.md`](../contracts/backend-stream-v1.md)):
  - Session & reads: `POST /api/session`, `DELETE /api/session`, `GET /api/snapshot`,
    `GET /api/events?after=<cursor>`, `GET /api/evidence/{evidenceId}`, `GET /api/map`
    (`GET /api/objects` is only `vision/`'s local non-durable diagnostic endpoint,
    not the dashboard source; expired/missing session returns `401 Unauthorized`).
  - Operator writes: `POST /api/missions`, `POST /api/missions/{id}/cancel`,
    `POST /api/robots/{id}/stop`, and manual chassis slice routes
    `POST /api/manual/lease`, `POST /api/manual/drive`, `POST /api/manual/release`,
    `POST /api/manual/stop`, `POST /api/manual/rearm`, `GET /api/manual/status`.
- Bridge: `POST /commands`, `/poses`, `/stop`, `/rearm`; `GET /commands/{id}`;
  manual chassis mirror routes `POST /manual/lease`, `POST /manual/drive`,
  `POST /manual/release`, `POST /manual/rearm`, `GET /manual/status`.
  Robot reports to backend `POST /api/robot-events`.

Tracks carry camera/frame/track IDs, capture time, class, detector score, pixel
bounding box and floor-contact pixel. Wire `objectId` is always
`cameraId:sessionId:trackId` (while internal Postgres primary keys in
`rover_backend` remain UUIDs). Mapping adds map/calibration IDs, metric floor
point and uncertainty; invalid position is null, never a fabricated origin.
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
