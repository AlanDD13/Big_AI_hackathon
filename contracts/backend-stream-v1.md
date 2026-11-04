# Backend durable snapshot and SSE

Owner: `zekihxh` implements this. Alan records the canonical contract.
Status: draft on `task/AlanDD13/vision-pipeline`, not merged. This is not vision's
diagnostic stream and it is not pickup authorization.

Vision's `GET /api/objects` and `GET /api/events` stay non-durable. They send
`event: snapshot` and `event: resync_required`. They do not send
`snapshot_required` and they do not replay history. Dashboard code uses only
the backend routes below (`POST /api/session`, `DELETE /api/session`,
`GET /api/snapshot`, `GET /api/events?after=<cursor>`, and
`GET /api/evidence/{evidenceId}`).

## Session and demo origin/transport

The dashboard is same-origin with the backend (served directly from the backend
origin or via a same-origin local Vite dev proxy).

- `POST /api/session` accepts `{"token":"<OPERATOR_TOKEN>"}`. `OPERATOR_TOKEN`
  is a backend environment variable of at least 16 characters. It never appears
  in Git, the board, or browser source.
- Success is `204` with `Set-Cookie: session=<opaque>; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=28800`.
  The cookie value is a random server-side session id, not the operator token.
  `Secure` is enabled by default (`BACKEND_COOKIE_SECURE=1`) for HTTPS/production;
  `BACKEND_COOKIE_SECURE=0` is allowed strictly for local `http://localhost` /
  `http://127.0.0.1` development (with a same-origin Vite proxy or backend origin)
  so local HTTP browsers retain the session cookie.
- `DELETE /api/session` clears the session cookie (`204`).
- Expired or missing session cookies on `GET /api/snapshot`, `GET /api/events`,
  and `GET /api/evidence/{evidenceId}` return `401 Unauthorized`, prompting the
  dashboard UI to re-authenticate via `POST /api/session`.
- Browser snapshot, SSE, and evidence requests send that cookie. They do not
  send `Authorization`, `VISION_TOKEN`, `PEER_TOKEN`, a Supabase key, or an xAI key.
- Native `EventSource` is the dashboard transport because the cookie is same-origin.
- Peers that are not the browser continue to use a backend bearer credential on
  server routes. That credential is distinct from the cookie and from `VISION_TOKEN`.

## Snapshot and replay

`GET /api/snapshot` returns one consistent cut:

```json
{
  "schemaVersion": "backend.v1",
  "backendBootId": "<32 lowercase hex>",
  "cursor": "<opaque>",
  "objects": [],
  "missions": [],
  "robots": [],
  "alerts": []
}
```

`cursor` is the last durable event included. `GET /api/events?after=<url-encoded cursor>`
replays strictly after it. On reconnect, `Last-Event-ID` wins over `after`.
Each durable event sets SSE `id` to the next opaque cursor and carries the
complete normalized entity (`object` or `alert`) in `body` (including the entity
ID `objectId` / `alertId`, `backendBootId`, `backendRevision`, and deduplication
fields `eventId` / `visionEventId` / `alertId`) so the dashboard can replace that
row by ID without patching partial diffs.
`snapshot_required` is an SSE event with no `id`, then the server closes the
stream. The client refetches `GET /api/snapshot` and reconnects. Use it for an
unknown cursor, an expired cursor, a backend boot change, or a gap the server
cannot replay.

Wire `objectId` is always the composite `cameraId:sessionId:trackId` across
vision, backend APIs, and the dashboard. Internal Postgres primary keys in
`rover_backend` remain UUIDs (`id uuid primary key`) mapped to the unique wire
`object_id`.

A cursor is bound to `backendBootId` and the operator session. It expires 15
minutes after the event it names, and on every backend restart. Scope is the
operator-visible objects, alerts, missions, and robots. It does not include
vision-internal diagnostics.

## Revisions

`backendBootId` changes when the backend process restarts. `backendRevision`
increases per entity inside that boot only. Clients replace local state from
the snapshot when the boot changes. They must not order `backendRevision`
across boots.

Vision `producerBootId`, `entityVersion`, and `revision` are stored inputs.
The UI orders by `backendRevision` inside the current `backendBootId`.
`potentialTarget` on the normalized object is vision's fixed-camera stationarity
and semantics gate only; it is not dispatch permission. Each normalized object
also carries `dispatchEligible` (`boolean`), which reflects backend mission
policy eligibility (`false` while mission policy, rover availability, or
confirmation is pending, even when `potentialTarget: true`). Backend mission
gates remain mandatory. A producer snapshot is not a command.

Deduplicate notifications by the retained vision `eventId` (`visionEventId`) and
`alertId`. Redelivery of the same `eventId` updates nothing and does not raise a
second alert. Vision `entityVersion` on an alert is the version published with
that event, not a pointer back to an older version, and a retry does not allocate
a new one.

Evidence bytes: `GET /api/evidence/{evidenceId}` with the same cookie. `404`
when the exact frame is gone. Do not redirect the browser at vision.

Fixture: `contracts/fixtures/backend-stream.json`.
