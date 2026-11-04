# Backend handoff for Codex

Read this, then `backend/NEXT.md`, before editing. Work in this git checkout:

`C:\Users\Nurbol Karimov\Documents\ChatGPT\Big_AI_hackathon\team-checkout`

The parent folder `Big_AI_hackathon\backend` is an old copy. It does not contain `postgres_projection.py` and it is not the git repo. Do not continue there.

Human: Nurbi. GitHub login: `zekihxh`. Commit identity, environment only, never `git config`:

- `GIT_AUTHOR_NAME` / `GIT_COMMITTER_NAME` = `Noxardi`
- `GIT_AUTHOR_EMAIL` / `GIT_COMMITTER_EMAIL` = `161127146+zekihxh@users.noreply.github.com`

Board posts start with `[zekihxh's Codex]`. Exclusive paths: `backend/**`. AlanDD13 merges. Do not push `main`. After each finished slice, commit and push the task branch. Never commit `backend/.env` or any secret.

## Where the code is

Open pull request: https://github.com/AlanDD13/Big_AI_hackathon/pull/11

- Branch: `task/zekihxh/postgres-api`
- Commit: `7534778` (`753477870e3d911fbeb94b622641645c8f2c7f31`)
- Base at branch creation: `2922c1d`. `origin/main` later moved to `52a74ff` (robot hardware PR #9 only). GitHub reported the PR `MERGEABLE` on 2026-11-04. Fetch before assuming that is still true.
- Board ready note: https://github.com/AlanDD13/Big_AI_hackathon/issues/1#issuecomment-6636817017

Already on `main` from earlier backend work: PR #5, merge `3d5f57b`, including `56579b4` / `migrations/007_runtime_grants.sql`. Do not open another grants PR.

## What the API does now

FastAPI routes in `backend/app.py`:

- `GET /healthz`
- `POST /api/session` sets an HttpOnly `session` cookie. `SameSite=Lax`. Secure unless `BACKEND_COOKIE_SECURE=0`.
- `GET /api/snapshot` and `GET /api/events` (SSE). `Last-Event-ID` wins over `after`. An unknown, expired, or foreign cursor returns `snapshot_required` and no id.
- `GET /api/evidence/{evidence_id}`
- `POST /api/observations` accepts only `MappingEvidence`, bearer `PEER_TOKEN`. A demo sector body (`demoPointM`, `mapId: demo-grid`) returns 422.

`dispatchEligible` stays false until mission policy passes. `potentialTarget` means stationarity only. Object and alert bodies follow the vision fixture shape from `origin/task/AlanDD13/vision-pipeline` commit `2f8e5f6`: nested `mapping`, `eligibilityReason`, alert `type` / `eventId` / `acknowledged` / `createdAt`. Vision-only fields (`bboxPx`, detector label, classification) stay null or `PENDING` because `MappingEvidence` does not carry them. Wire object id is `cameraId:sessionId:trackId`. Postgres primary keys stay UUID.

`PostgresObservationLog` in `backend/postgres_projection.py` saves through `PostgresEvidenceStore.record_observation` and reloads joined rows on startup. `event_id_for` uses `X-Vision-Event-Id` or `mapping:camera:session:geometry:track:frame`. Storage failures become HTTP 503 `STORAGE_UNAVAILABLE` with a generic message. Do not put the connection string in that message. `app_from_env` reads `BACKEND_DATABASE_URL` from the environment only and appends `sslmode=require` when missing. Session cursors stay in memory, so a restart changes `backendBootId` and forces a new snapshot.

These libraries are not called by the HTTP process yet: `collection.py`, `dispatch.py`, `mission_policy.py`, `postgres_collection.py`, `postgres_dispatch.py`. `grok_intent.py` only decodes local action names. `bridge_client.py` interprets simulator replies and does not open a socket.

## Database

Supabase project `buojgmoumyzqxmerckjm` (`eu-west-1`). Schema `rover_backend` already exists. Migrations `001` through `007` are applied. Eight tables. Grants are `USAGE` on the schema, `SELECT`/`INSERT`/`UPDATE`/`DELETE` on tables, and `EXECUTE` on functions, only for `rover_backend_runtime`. `public` was not changed. Do not re-apply the migrations. Do not grant `anon` or `authenticated`. Do not expose `rover_backend` through PostgREST.

Credentials live only in gitignored `backend/.env`. Do not print them, commit them, or paste them on the board. Tests must use `BACKEND_TEST_DATABASE_URL` on a disposable database. Do not point tests at the shared Supabase URL.

Verified on disposable PostgreSQL 18 using the scoped runtime role. All 34 Python tests and four SQL smoke tests passed. New observations preserve the original wire payload, producer metadata and alternate event IDs across restart. Legacy rows still load from the fields they originally stored. A retry with original fields that were never stored can still return 409 for those legacy rows.

## Contracts and teammates

Board: https://github.com/AlanDD13/Big_AI_hackathon/issues/1. `board.conf` is CRLF, so Git Bash rejects `BOARD_ISSUE` from the file. Override it:

```powershell
$env:BOARD_REPO = "AlanDD13/Big_AI_hackathon"
$env:BOARD_ISSUE = "1"
```

Then `tools/board unread` through Git Bash. Reply only when the backend must answer or ask. Do not post acknowledgements.

Do not re-answer these settled notes:

- No multi-frame detections route. A 1 m demo cell needs `positionErrorM` covering the cell (about 0.5 m or more) or `valid: false`. It must not pass the 0.025 m gate.
- Tezqx waypoint bursts are not `MappingEvidence`.
- Root pytest on Alan's branch `7c9df0a` includes `backend/tests`. No further reply.
- Manual stage `UNKNOWN` is not mission state `unknown`. Mission `unknown` stays `409 AUTONOMOUS_ACTIVE`.
- While manual stage is `UNKNOWN`: lease and drive return `503 TELEMETRY_STALE`, rearm returns `409 NOT_STOPPED`.
- After three fresh stationary samples, a stop, watchdog silence, expiry, or telemetry fault latches `STOPPED`: lease and drive return `409 STOP_LOCKED`. Rearm then returns `IDLE` with motion false and does not restore the lease.
- A clean release returns `200` with `stage: UNKNOWN`, `stationaryConfirmed: false`, `stationarySamples: 0`. Callers poll status. A confirmation timeout stays `UNKNOWN`.
- Those words are in `contracts/manual-control-v1.md` at `6026078` on `origin/task/AlanDD13/vision-pipeline`. They are not on `main`. Alan owns `contracts/**`.

Robot draft PR #10 (`4dfed46`, `task/platonyesin-dev/watchdog`) is changes needed. Physical acceptance is blocked: an explicit stop still reported about `-0.04` to `-0.07` m/s, so `UNKNOWN` correctly refused rearm. `:8004` is not live. Do not call `http://192.168.5.194:8004` or Alan's vision service `http://192.168.5.192:8002`. Do not add `/api/manual/*` until platonyesin-dev posts a live bridge URL and the manual contract is on the branch you are implementing.

## Tools on this machine

`python`, `git`, `gh`, and `bash` are not on PATH.

- Python 3.12.14: `C:\Users\Nurbol Karimov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
- Git: `C:\Program Files\Git\cmd\git.exe`
- Git Bash: `C:\Program Files\Git\bin\bash.exe`
- GitHub CLI: `C:\Program Files\GitHub CLI\gh.exe`

PowerShell does not accept `&&` or a bash heredoc. Pass commit messages with repeated `git commit -m` arguments, or a bash script file. The old Cursor watcher was stopped. The Codex board watcher uses the read-only GitHub connector and does not consume this checkout's unread cursor.

## Board notes already marked read

The stopped watcher consumed these comments, so `tools/board unread` will not show them again. Do not post an acknowledgement.

- `#6636640820` Tezqx asked vision and backend to make Grok return only a grid sector such as `A1`, with no pixel or metre coordinates. `#6636663251` Alan rejected that. An LLM sector is not calibrated `MappingEvidence` and cannot pass stationarity. `mapping/` may project `contactPointPx` and mark the result uncalibrated. `POST /api/observations` keeps rejecting demo sector bodies. Do not add a sector field unless Alan adds an explicit uncalibrated `demoSector` that stays ineligible.
- `#6636659691` Alan added advisory graspability on `origin/task/AlanDD13/vision-pipeline` at `8445397` (`LIKELY_GRASPABLE`, `TOO_LARGE`, `TOO_SMALL`, `UNKNOWN_SCALE`). It is not an eligibility decider. Physical gripper measurements are for `@platonyesin-dev`, not backend. Do not treat that verdict as `dispatchEligible`. No backend code until a contract field exists.
