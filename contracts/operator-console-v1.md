# Operator console v1

The two camera panels and rover message panel use actual backend data. An empty
backend displays a waiting state. No preview mission runs automatically.

Every route below requires the existing operator session cookie. Missing or
expired sessions return 401. The browser receives no upstream camera or xAI key.

| Route | Behavior |
| --- | --- |
| `GET /api/camera/status`, `GET /api/camera/feed` | Fixed camera configuration and bounded JPEG/MJPEG proxy |
| `GET /api/rover/camera/status`, `GET /api/rover/camera/feed` | Equivalent rover feed using separate server configuration |
| `GET /api/rover/notices` | `{backendBootId, cursor, notices}` snapshot; newest 200 messages |
| `GET /api/rover/notices/events?after=<cursor>` | Session/boot-scoped SSE; `rover.notice` and `snapshot_required` events |
| `GET /api/rover/notices/{id}/audio` | Native xAI MP3 for an existing server-authored notice; 404 unknown notice, 503 unavailable speech |
| `GET /api/models` | Configured xAI model roles, observed vision model IDs and speech availability/usage |

Each notice contains `id` (32 lowercase hex), `kind`, `text`, `objectId`,
`createdAt`, `backendBootId`, `audioUrl`, nullable `grabbabilityScore`,
`classificationModel` and `classificationConfidence`.
Kinds: `found`, `classified`, `going`, `grabbed` (pickup command sequence),
`finished`, `manual`. IDs deduplicate repeated accepted source events.
Reconnects load history silently; only new SSE notices play after the operator
enables voice. Mute and unmount stop playback. Playback failures remain visible.

Messages derive from accepted observations/classifications and recovery alerts.
`alert.recovery_step` adds a required `recoveryStep` to the existing recovery
alert object payload. Ordered steps are `FORWARD_30`, `TURN_RIGHT_90`,
`FORWARD_154`, `PICKUP`, `RETURNED`, `LOWERED`, `RELEASED`. The robot writes
structured progress only after each step completes; vision forwards it through
the authenticated existing publication channel. Model prose and SDK log lines
cannot generate completed-step notices. `recovery_completed` follows successful
return/release/arm raise. Neither this event nor a pickup notice verifies a load.

Classification includes optional nullable `classificationModel`, a native
`grok-*` ID reported by completed native inference. The numeric score remains
separate from categorical classification confidence and detector confidence.

Set `BACKEND_CAMERA_URL` and `BACKEND_ROVER_CAMERA_URL` to fixed server-owned
camera sources. Each supports corresponding `_AUTHORIZATION` and `_CA_CERT`
variables. Transport configuration is not capture freshness; the UI marks feeds
unverified and displays connection/decoding errors explicitly.

Set `XAI_API_KEY` on the backend for voice (see
[`docs/GROK_MODELS.md`](../docs/GROK_MODELS.md)). For local HTTP development,
set `BACKEND_COOKIE_SECURE=0`; keep it enabled for HTTPS. Start backend on 8000,
then run `npm --prefix frontend run dev`. Configure `BACKEND_URL` and matching
`OPERATOR_TOKEN` in `frontend/.env` for the local Vite session helper. Production
hosting must proxy `/api` to the backend and use the normal operator session;
the local helper only runs in Vite development mode.

No database migration is needed: source publications persist through the
existing vision event log when PostgreSQL is configured. Sessions/cursors and
the bounded TTS cache remain in memory. Backend startup reconstructs notices
from stored source events. No new automatic dispatch authority is added here.
