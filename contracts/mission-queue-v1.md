# Mission queue checkpoint

Owner: `zekihxh` implements this in `backend/**`. Alan records the wire contract.
Status: draft on `task/AlanDD13/vision-pipeline`, not merged and not on `main`.
This slice stores a queue row. It does not call the robot, does not open a
manual lease, and does not set `dispatchEligible` to true.

`LcyShox` asked for the snapshot and SSE shape in comment 6637374618.
`zekihxh` asked for the statuses in comment 6637338132. The admission outcomes
already implemented in `backend/collection.py` keep their names.

## Request

`POST /api/missions`

Same-origin operator cookie from `POST /api/session`. A missing or expired
session is `401` with code `UNAUTHORIZED`.

Header `Idempotency-Key` is required. It matches `^[A-Za-z0-9_-]{8,128}$`.
The key is scoped to that operator session. A different session is a different
key. The browser keeps one key for one explicit submission. After an ambiguous
timeout, retry that same key and body. Do not mint a second key for a request
that might already have been accepted.

```json
{ "objectId": "phone-1:session1:track1", "destinationZoneId": "zone-1", "source": "manual" }
```

`source` for this slice is only `manual`. `destinationZoneId` matches
`^[A-Za-z0-9_-]{1,128}$`. `objectId` is the vision composite and may contain
colons. Any other body is `422` code `VALIDATION_ERROR`.

## Results

Error body, including validation errors: `{"error":{"code":"<CODE>","message":"<text>"}}`.

| Outcome | HTTP | code | Body |
| --- | --- | --- | --- |
| First accept | 202 |  | mission object, `state` `QUEUED` |
| Same key and same body | 200 |  | same `missionId`, current state |
| Same key and different body | 409 | `IDEMPOTENCY_CONFLICT` | no new mission |
| Unknown object | 404 | `OBJECT_NOT_FOUND` | |
| Stationarity gate fails | 409 | `STATIONARITY_REQUIRED` | |
| Classification missing | 409 | `CLASSIFICATION_REQUIRED` | |
| Person, animal, carried, uncertain, or low confidence | 409 | `CLASSIFICATION_EXCLUDED` | |
| No pickup profile | 409 | `PICKUP_PROFILE_REQUIRED` | |
| Evidence revision changed while planning | 409 | `EVIDENCE_CHANGED` | |
| No usable route | 409 | `ROUTE_UNAVAILABLE` | |
| Mapping call failed | 503 | `MAPPING_UNAVAILABLE` | |
| Queue write failed | 503 | `STORAGE_UNAVAILABLE` | no mission id |

Replay returns 200, not 202. It does not emit a second SSE event and does not
enqueue a second mission. A failed queue write returns `503 STORAGE_UNAVAILABLE`
and no mission id. A concurrent reservation of the same object stays
`409 EVIDENCE_CHANGED`; this slice does not add `OBJECT_RESERVED`. A fixture classification already stored on the object
may satisfy the classification gate only when its provider is `fixture` and the
dashboard keeps that label. This route must not invent a classification.
Mapping may be a local fixture whose `planId` starts with `fixture-` when no
mapping service is configured. That plan is not permission to move.

## Snapshot and SSE

`GET /api/snapshot` includes every accepted mission for the operator:

```json
{
  "missionId": "mission-1",
  "state": "QUEUED",
  "objectId": "phone-1:session1:track1",
  "destinationZoneId": "zone-1",
  "source": "manual",
  "backendBootId": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "backendRevision": 1
}
```

The first accept also sends SSE event `mission.updated` with a durable `id`.
The data is that same object. `backendRevision` increases inside one
`backendBootId` only. Later states and cancel are not part of this checkpoint.

Fixture: `contracts/fixtures/mission-queue.json`.
