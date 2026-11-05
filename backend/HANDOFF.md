# Backend handoff for Codex

Read this, then `backend/NEXT.md`, before editing. Work in this git checkout:

`C:\Users\Nurbol Karimov\Documents\ChatGPT\Big_AI_hackathon\team-checkout`

The parent folder `Big_AI_hackathon\backend` is an old copy. It is not the git repo. Do not continue there.

Human: Nurbi. GitHub login: `zekihxh`. Commit identity, environment only, never `git config`:

- `GIT_AUTHOR_NAME` / `GIT_COMMITTER_NAME` = `Noxardi`
- `GIT_AUTHOR_EMAIL` / `GIT_COMMITTER_EMAIL` = `161127146+zekihxh@users.noreply.github.com`

Board posts start with `[zekihxh's Codex]`. Exclusive paths: `backend/**`. AlanDD13 merges. Do not push `main`. Push only with an explicit refspec such as `HEAD:task/zekihxh/<name>`. `checkout -B <branch> origin/main` tracks `origin/main`; run `git branch --unset-upstream` before any push. After each finished slice, commit and push the task branch. Never commit `backend/.env` or any secret.

This note was written on 2026-11-05 from `origin/main` at `96e7b0f`. The Cursor 30-second board watcher is stopped. Do not restart it.

## Where the code is

`origin/main` is `96e7b0f` (`fix(robot): prevent standing motor twitch and Wi-Fi write timeout on physical bridge`). These slices are already merged. Do not rebuild them:

- PR #5: HTTP stream and `migrations/007_runtime_grants.sql`. Grants are already applied.
- PR #11: observation persistence.
- PR #13: `POST /api/missions` queues one live mission. It is not a robot command.
- PR #14: vision ingest on the dashboard stream.
- PR #16: vision rows in `rover_backend`.
- PR #24: vision writes stay atomic and keep the shared pickup policy.
- PR #26 and PR #30: camera proxy, then manual HTTP client reuse outside the motion deadline.
- PR #29: session-bound manual chassis forwarding with bridge-restart fencing.
- PR #31 and PR #32: dashboard manual adapter and the default-off physical bench gate. Those are frontend-owned. Do not edit `frontend/**`.

Open, not merged, as of this note:

- PR #33, `task/zekihxh/demo-sector`, head `2040e8d`. Display-only `demoSector` on a confirmed inanimate trash classification. `dispatchEligible` stays false. It is not an observation.
- PR #34, `task/AlanDD13/manual-clock-skew`, head `dc8f7af`. Alan owns this. It allows `issuedAt` up to 100 ms ahead in both `backend/manual_requests.py` and `robot/safety.py`. Do not edit either file while this PR is open. Do not duplicate the allowance on another branch.
- PR #35, `task/zekihxh/board-tests`, head `18fccd2`. Windows board tests only (`tests/test_board.py`). Not a backend behavior change.
- PR #36, `task/zekihxh/manual-gate-latency`, head `374b229`. Held-direction gate fix. See below.

The previous handoff branch `task/zekihxh/codex-handoff` at `7b55ac2` describes `main` at `7fb18c6`. Ignore it. Its `NEXT.md` instruction to branch `task/zekihxh/mission-http` is finished.

## Manual hold, which is the live backend bug

On main, every drive waits for `PostgresManualGate` before it is forwarded. That gate opened a new database connection per drive. A 250 ms intent that expires during the check becomes `409 STALE_INTENT`, and `end_session` stop-latches the lease. The dashboard then needs a re-arm. Four different failures share that same code: a future `issuedAt`, an intent already expired on arrival, an intent that expires during the gate, and a duplicate sequence.

PR #36 changes only `backend/manual_proxy.py` and its tests:

- The exclusion query still runs on every drive. One connection is reused. The yes/no answer is not cached. `rollback()` after each read lets the next tick see a newly committed mission.
- A drive that was valid on arrival and whose `expiresAt` has passed after the gate is not forwarded. The response is `409 INTENT_EXPIRED`, including expiry at final pre-send validation. The lease stays. The sequence is consumed, so that tick cannot be replayed.
- Stop, release, a duplicate sequence, and an intent that is already stale on arrival still stop-latch.

Main still rejects any future `issuedAt`. PR #34 is the 100 ms allowance, and the robot copy in `robot/safety.py` must land with it. A backend-only reload leaves the robot rejecting a 1 ms-ahead `issuedAt`. Do not reload `192.168.5.126:8000` or the bridge.

`frontend/src/manualBackend.ts` (LcyShox) still revokes the lease on any rejection and when the round trip exceeds 250 ms. PR #36 alone does not make a held direction continuous until that client treats `INTENT_EXPIRED` as a missed tick. Do not edit that file unless LcyShox hands it over.

Continuation verification on `374b229`: all 99 backend tests passed using disposable PostgreSQL 18 and the scoped runtime role, plus all four SQL smoke scripts. This includes the actual fake-output P4 loopback lifecycle. PR #33 at `2040e8d` independently passed all 100 backend tests and four SQL smoke scripts; PR #35 at `18fccd2` passed all 20 board/contract tests on Windows. PR #34 was inspected only, respecting Alan's reserved files/test instruction. Do not point tests at the shared Supabase URL.

## What the API does now

FastAPI routes in `backend/app.py` include health, session, snapshot, SSE, evidence, observations, missions, detections, classifications, vision events, the camera proxy, and `/api/manual/{lease,drive,release,stop,rearm}`.

- `POST /api/observations` accepts only `MappingEvidence`, bearer `PEER_TOKEN`. A demo-grid body (`mapId: demo-grid`, `calibrated: false`) returns 422. Display `demoSector` must not be stored through this route.
- `POST /api/missions` queues one live mission and does not dispatch.
- `dispatchEligible` stays false. A queued mission, snapshot, Grok advisory, command ACK, or `visualPickupAdvisory` is not permission to drive or pick up.
- Manual stage `UNKNOWN` is not mission state `unknown`. Mission `unknown` is `409 AUTONOMOUS_ACTIVE`. Manual `UNKNOWN` is `503 TELEMETRY_STALE` on lease and drive, and `409 NOT_STOPPED` on rearm. Confirmed `STOPPED` is `409 STOP_LOCKED`. Rearm from `STOPPED` returns `IDLE`, motion false, and does not restore the lease. A clean release returns 200, stage `UNKNOWN`, `stationaryConfirmed` false, `stationarySamples` 0.
- A bridge must not report command `STOPPED` while the chassis stage is `UNKNOWN`, because the backend treats command `STOPPED` as terminal. `404 UNKNOWN_COMMAND` must not be resent.
- Session cookie is HttpOnly, Secure, `SameSite=Lax`. `BACKEND_COOKIE_SECURE=0` is only for local http. SSE: `Last-Event-ID` wins over `after`. An unknown, expired, or foreign cursor returns `snapshot_required`. Session cursors stay in memory, so a restart changes `backendBootId`.
- Wire object id is `cameraId:sessionId:trackId`. Postgres primary keys stay UUID.
- Storage failures are HTTP 503 `STORAGE_UNAVAILABLE` with a generic message. Do not put the connection string in that message. `app_from_env` reads `BACKEND_DATABASE_URL` from the environment only and appends `sslmode=require` when it is missing.

## Database

Supabase project `buojgmoumyzqxmerckjm` (`eu-west-1`). Schema `rover_backend`. Migrations `001` through `007` are applied. Grants are only for `rover_backend_runtime`. Do not re-apply the migrations. Do not grant `anon` or `authenticated`. Do not expose `rover_backend` through PostgREST. Credentials stay in gitignored `backend/.env` and off the board.

## Do not do these

- Do not call port `8004`, the vision server `https://192.168.5.192:8443`, or a DJI SDK session.
- Do not start or reload `192.168.5.126:8000` during this review. A previous Codex session started that runtime; Cursor did not. Check current process ownership/readiness before a separately authorized restart. Review branches are not automatically loaded into a running service.
- Do not send lease, drive, rearm, or stop to the physical bridge. The bridge was last intentionally stopped. Last reported boot `95d8b1ddf1b3483a9cab93a983dbefda` was offline. `http://192.168.5.194:8004/health` was connection refused.
- Do not add recovery HTTP or another `/api/manual/*` action. Do not wire the LangGraph physical adapter. PR #19 is merged as a simulator-first loop. `contracts/recovery-tools-v1.md` is not an agreed physical API. Pickup and place stay rejected until a separate macro and status contract exists.
- Do not implement Alan's 2026-11-05 route claim (`task/AlanDD13/route-pickup-return`: forward 0.30 m, right 90 degrees, forward 1.54 m, Grok pickup, return while holding the can). That claim is `robot/**` and is addressed to platonyesin. Board comment `#6658920756`.
- Alan posted a `PEER_TOKEN` on the board. Do not repeat it, copy it into code, or commit it.

## Tools on this machine

`python`, `git`, `gh`, and `bash` are not on PATH.

- Python 3.12.14: `C:\Users\Nurbol Karimov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe`
- Git: `C:\Program Files\Git\cmd\git.exe`
- Git Bash: `C:\Program Files\Git\bin\bash.exe`
- GitHub CLI: `C:\Program Files\GitHub CLI\gh.exe`

PowerShell does not accept `&&` or a bash heredoc. Pass commit messages with repeated `git commit -m` arguments. `board.conf` is CRLF, so set `BOARD_REPO=AlanDD13/Big_AI_hackathon` and `BOARD_ISSUE=1` in the process environment before `tools/board`. Reply only when the backend must answer or ask. Do not post acknowledgements.

From the checkout root:

```powershell
& "C:\Users\Nurbol Karimov\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest discover -s backend/tests -v
```
