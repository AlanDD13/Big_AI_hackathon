# Backend handoff for Codex

Read this, then `backend/NEXT.md`, before editing. Work in this git checkout:

`C:\Users\Nurbol Karimov\Documents\ChatGPT\Big_AI_hackathon\team-checkout`

The parent folder `Big_AI_hackathon\backend` is an old copy. It is not the git repo. Do not continue there.

Human: Nurbi. GitHub login: `zekihxh`. Commit identity, environment only, never `git config`:

- `GIT_AUTHOR_NAME` / `GIT_COMMITTER_NAME` = `Noxardi`
- `GIT_AUTHOR_EMAIL` / `GIT_COMMITTER_EMAIL` = `161127146+zekihxh@users.noreply.github.com`

Board posts start with `[zekihxh's Codex]`. Exclusive paths: `backend/**`. AlanDD13 merges. Do not push `main`. Push only with an explicit refspec such as `HEAD:task/zekihxh/<name>`. A previous `checkout -B <branch> origin/main` tracked `origin/main`; run `git branch --unset-upstream` before pushing. After each finished slice, commit and push the task branch. Never commit `backend/.env` or any secret.

Fetch `origin/main` before branching. This note was written from `origin/main` at `7fb18c6` (merge of PR #18) on 2026-11-04. The Cursor board watcher is stopped. Do not restart it.

## Where the code is

`origin/main` is `7fb18c6`. These backend slices are already merged. Do not rebuild them:

- PR #5, merge `3d5f57b`: HTTP stream and `migrations/007_runtime_grants.sql` (`56579b4`). Grants are already applied.
- PR #11, commit `7534778`: observation persistence. Merged.
- PR #13, merge `e3e5632`: `POST /api/missions` queues one live mission. It is not a robot command.
- PR #14, commit `5e587c8`, merge `1dbc592`: vision ingest.
- PR #16, commit `4b86b16`, merge `79cdcad`: vision rows in `rover_backend`.

The last recorded backend suite at `4b86b16` was 39 passed and 15 skipped. LcyShox later reported the database tests passing on the merged head, including a restart that kept the mapping object, queued mission, and vision object while the operator cookie died and `backendBootId` changed. Re-run the suite before changing code. Postgres tests skip unless `BACKEND_TEST_DATABASE_URL` points at a disposable database. Do not point tests at the shared Supabase URL.

## What the API does now

FastAPI routes in `backend/app.py`:

- `GET /healthz`
- `POST /api/session` sets an HttpOnly `session` cookie. `SameSite=Lax`. Secure unless `BACKEND_COOKIE_SECURE=0`.
- `GET /api/snapshot` and `GET /api/events` (SSE). `Last-Event-ID` wins over `after`. An unknown, expired, or foreign cursor returns `snapshot_required` and no id. The stream carries object, alert, and `mission.updated`. It does not carry LangGraph tool steps.
- `GET /api/evidence/{evidence_id}`
- `POST /api/observations` accepts only `MappingEvidence`, bearer `PEER_TOKEN`. A demo sector body (`sector`, `demoPointM`, `mapId: demo-grid`, `calibrated: false`) returns 422.
- `POST /api/missions` reserves one live mission and does not dispatch.
- `POST /api/detections` shows `object.observed` immediately as `OBSERVING` unless `EXCLUDED` or `LOST`.
- `POST /api/classifications` stores category, description, and confidence `LOW`/`MEDIUM`/`HIGH`. `classificationProvider` is `fixture` or `grok`. Never average it with `detectorScore`.
- `POST /api/vision-events` accepts `object.updated` and `alert.*`. A notice may have a null `objectId`. Replay of the same `eventId` does not emit a second SSE event. A sequence hole returns 409 and is not filled. There is no route for a complete `ACTIVE_OBJECTS` snapshot.

`dispatchEligible` stays false. `potentialTarget` is stationarity only and is not permission to drive or pick up. A missing `visualPickupAdvisory` means no assessment. If that field is present, `pickupVerified` is forced false and the advisory stays advisory. An older `producerBootId` cannot restore `potentialTarget`.

Wire object id is `cameraId:sessionId:trackId`. Postgres primary keys stay UUID. When `BACKEND_DATABASE_URL` is set, accepted vision events write `rover_backend.ingest_events` (producer `vision`), upsert `rover_backend.objects` by camera/session/track, upsert classifications (`PENDING`/`COMPLETED`/`UNAVAILABLE` mapped to `pending`/`completed`/`unavailable`), and insert alerts with `transition_key` equal to the event id. Startup reloads canonical payloads ordered by `received_at`, `event_id` and calls `ingest_vision(..., persist=False)`. The same event id with a different canonical body is 409. Storage failures are HTTP 503 `STORAGE_UNAVAILABLE` with a generic message. Do not put the connection string in that message. `app_from_env` reads `BACKEND_DATABASE_URL` from the environment only and appends `sslmode=require` when missing. Session cursors stay in memory, so a restart changes `backendBootId` and forces a new snapshot even when rows persist.

`dispatch.py`, `bridge_client.py`, and `postgres_dispatch.py` keep one command id and reconcile a lost ack without resending. The HTTP process does not call them. `bridge_client.py` does not open a socket. A bridge `404 UNKNOWN_COMMAND` is mission unknown and must not be resent.

## Database

Supabase project `buojgmoumyzqxmerckjm` (`eu-west-1`). Schema `rover_backend` already exists. Migrations `001` through `007` are applied. Grants are only for `rover_backend_runtime`: `USAGE` on the schema, `SELECT`/`INSERT`/`UPDATE`/`DELETE` on tables, and `EXECUTE` on functions. `public` was not changed. Do not re-apply the migrations. Do not grant `anon` or `authenticated`. Do not expose `rover_backend` through PostgREST. The frontend gets no database role. Credentials stay in gitignored `backend/.env` and off the board.

## Hold

Do not add `/api/manual/*`, pickup, recovery, or any other robot route. Do not call `http://192.168.5.194:8004` or `https://192.168.5.192:8443`. Do not open an SDK session. Do not wire the LangGraph physical adapter. Draft `contracts/recovery-tools-v1.md` on PR #19 at `5730da7` is a simulator interface, not an agreed physical API. A snapshot is data, not mission authority. The fixture `max_duration_ms` of 300 is not an arm-macro limit. Do not extend a drive lease to cover a gripper move. Pickup and place stay rejected until a separate bounded macro and status contract exists.

Manual stage `UNKNOWN` is not mission state `unknown`. Mission `unknown` stays `409 AUTONOMOUS_ACTIVE`. Manual `UNKNOWN` is `503 TELEMETRY_STALE` on lease and drive, and `409 NOT_STOPPED` on rearm. Confirmed `STOPPED` is `409 STOP_LOCKED`. Rearm from `STOPPED` returns `IDLE`, motion false, and does not restore the lease. A clean release returns `200` with stage `UNKNOWN`, `stationaryConfirmed` false, and `stationarySamples` 0. A bridge must not report command `STOPPED` while the chassis stage is still `UNKNOWN`, because the backend treats command `STOPPED` as terminal.

Demo-grid geometry is not settled on `main`. The shared contract still has row A on the bottom and pixel `(855, 423)` on a 1920x1080 frame as C2 at `(0.75, 1.25)` m. Tezqx's `c300e1b` uses row A on top and B2 at `(-0.25, 1.25)` m. Those labels stay `mapId demo-grid`, `calibrated: false`, and are not `MappingEvidence`. PR #22 at `e268fb7` draws that grid and is not merged. `POST /api/observations` still returns 422 for those bodies.

## Teammates, as of 2026-11-04 22:09 UTC

Board: https://github.com/AlanDD13/Big_AI_hackathon/issues/1. `board.conf` is CRLF, so Git Bash rejects `BOARD_ISSUE` from the file. Override it:

```powershell
$env:BOARD_REPO = "AlanDD13/Big_AI_hackathon"
$env:BOARD_ISSUE = "1"
```

Then `tools/board unread` through Git Bash. Reply only when the backend must answer or ask. Do not post acknowledgements. Alan posted a `PEER_TOKEN` on the board. Do not repeat it, copy it into code, or commit it.

Do not re-answer these notes:

- Recovery boundary accepted: https://github.com/AlanDD13/Big_AI_hackathon/issues/1#issuecomment-6641201541 and Alan's confirmation `#6641512613` / `#6641660963`. PR #19 stays draft. `propose_collect` only queues.
- Demo-grid 422, including B2: `#6641740791`. Alan agreed in `#6641820360`.
- Joint-test limits: `#6642747473`. The first chassis milestone does not need backend routes.
- No backend chassis or SDK activity, including no record of camera session `rovcam-a7cafc17a3e173f7`: `#6642828888`. The operator later withdrew the drift report in `#6643046159`.
- Physical adapter and macro refused: `#6643100778`. Robot commit `6a436f2` is not enablement. A queued mission cannot authorize it.

Robot-side facts that are not backend acceptance: PR #17 remains the command-bridge branch. Offline head `76d70b7` was reviewed and is not hardware acceptance. A later local chassis run on firmware `01.01.1150` measured silence-to-zero at 0.265 s and 0.271 s, reached `STOPPED` after fresh stationary samples, and the operator confirmed a brief move and stop. One live Grok 4.7 call chose `approach` for `local-test:empty-can` and ended `BLOCKED`, not `COMPLETE`. That run is not a backend mission. Pickup, transport, and place did not run.

## Tools on this machine

`python`, `git`, `gh`, and `bash` are not on PATH.

- Python 3.12.14: `C:\Users\Nurbol Karimov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
- Git: `C:\Program Files\Git\cmd\git.exe`
- Git Bash: `C:\Program Files\Git\bin\bash.exe`
- GitHub CLI: `C:\Program Files\GitHub CLI\gh.exe`

PowerShell does not accept `&&` or a bash heredoc. Pass commit messages with repeated `git commit -m` arguments. From the checkout root:

```powershell
& "C:\Users\Nurbol Karimov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s backend/tests -v
```
