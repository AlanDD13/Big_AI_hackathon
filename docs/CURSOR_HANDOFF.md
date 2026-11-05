# Cursor handoff — native Grok operator console

Updated 2026-11-05, Europe/Rome. This replaces the older 2026-11-04 handoff.

## Resume prompt

> Read this file, AGENTS.md, docs/CONTRACTS.md, docs/GROK_MODELS.md and
> contracts/operator-console-v1.md. Inspect current Git status first and preserve
> untracked files. Continue the native xAI operator-console integration. The user
> explicitly requested no other agents and no further board posts; do not spawn
> agents or write to the board. Core implementation and offline/browser checks
> are complete. Finish private runtime setup and supervised live acceptance.
> Do not launch physical motion just to check the UI. Never put secrets in chat,
> commits, the frontend, screenshots or this handoff. Do not claim a fixture is a
> live camera, live Grok inference or confirmed physical pickup.

## Git and workspace

- Repository: `AlanDD13/Big_AI_hackathon` (private).
- Primary checkout: `/Users/alandd/Desktop/Pavia_grok_hackathon`.
- Current implementation worktree: `/private/tmp/pavia-main-checkpoint-1105`,
  branch `task/AlanDD13/operator-backend-grok`.
- At the user's request, the existing frontend and camera-route integration were
  committed and pushed to `main` **before** starting these backend changes:
  checkpoint `191f3eeb13296532e6e3c955b74791575e4b635b`.
- This handoff ships with the subsequent native xAI/backend implementation.
  Use `git log -5 --oneline` and `git status --short` to identify its exact commit;
  check remote state rather than relying on older PR/handoff snapshots.
- Preserve the primary checkout's pre-existing untracked `.agents/`,
  `docs/EVALUATION_FIT.md`, `docs/FILMING_IDEAS.md`,
  `docs/HACKATHON_ALTERNATIVES.md`, `mobileclip2_b.ts` and model checkpoints.
- The implementation worktree has an untracked `frontend/node_modules` symlink
  to the primary checkout's dependencies. Do not commit it or temporary fixtures.
- The lead authorized this cross-layer work and pushing to main. Do not reset,
  force-push, merge unrelated work, deploy or touch shared database schemas.

## Implemented

1. Fixed physical demo route: forward 30 cm from the initial front-wheel line,
   right 90 degrees, forward 154 cm, Grok alignment/pickup, retrace recorded
   odometry to the initial position/heading, lower 35 mm, open, raise, stop.
   Travel speed is 0.16 m/s. Alignment can rotate the rover. Seating motion is
   included in return breadcrumbs. Hard motion failures remain abort conditions;
   routine SDK warning logs are not interpreted as mission failure.
2. Any interchangeable fixed camera may supply observations. Eligible recovery
   still requires a locally confirmed physical arrangement, fresh stationary
   evidence, HIGH classification confidence, supported can profile, and model
   grabbability estimate >= 0.80. People, animals, carried/ambiguous objects,
   stale evidence and rejected mapping do not dispatch. This fixed route is not
   general navigation to arbitrary camera coordinates.
3. Durable SQLite job reservations and scene latch prevent repeated detections,
   cross-camera duplicates, concurrent runs and automatic retry after uncertain
   outcomes. Preserve the journal; do not delete it to force another attempt.
4. Native xAI only: `XAI_API_KEY`; vision uses `/v1/responses`, rover/policy use
   `/v1/chat/completions`, default model `grok-4.7`. OpenRouter runtime adapter
   and credentials are no longer used. Legacy provider selection is rejected.
5. Two camera proxies: `/api/camera/*` and `/api/rover/camera/*`. Fixed source
   URLs/auth/CA stay server-side. A feed decoding successfully does not verify
   capture freshness; the UI labels freshness unverified.
6. Real operator notices: authenticated snapshot + SSE, deduplication, bounded
   history, reconnect, empty/error states. Canned runtime progress was removed.
   Classification messages expose model ID, categorical confidence and numeric
   grabbability estimate.
7. Each controller-completed route step emits `alert.recovery_step`. Progress
   comes from structured controller files, not model prose or SDK stdout.
   Steps: FORWARD_30, TURN_RIGHT_90, FORWARD_154, PICKUP, RETURNED, LOWERED,
   RELEASED; successful final arm raise is followed by recovery_completed.
8. Native Grok Voice API `/v1/tts` speaks known server-authored notices. Voice
   `eve`, language `en`; no invented TTS model parameter. Operator enables voice
   once; new notices queue in order. Historical snapshot/reconnect messages stay
   silent. Mute stops playback. Missing key and playback failures are explicit.
9. `/api/models` distinguishes configured roles from observed completed vision
   model IDs and successful speech usage. See docs/GROK_MODELS.md.
10. Closed gripper/actuator ACK no longer claims verified holding. Route reports
    retain `holdingVerified:false` and `placementVerified:false`.

## What remains, in order

### 1. Native xAI credentials and private runtime configuration

At the last check, neither the root `.env` nor `vision/.env` contained a nonempty
`XAI_API_KEY`. No paid native xAI request was made for this implementation.
Configure the key privately for backend, vision and robot. An OpenRouter key is
not valid here. Existing private env files may still select
`GROK_VISION_PROVIDER=openrouter`; migrate them to `xai`,
`GROK_VISION_MODEL=grok-4.7`, `GROK_ROVER_MODEL=grok-4.7`, and
`GROK_RECOVERY_MODEL=grok-4.7`. Templates are updated; private env files were not
rewritten. Backend does not automatically load the root `.env`: load variables
through the shell/process runner. Never expose a key as a `VITE_*` variable.

### 2. Connect real cameras and restart the actual services

Set `BACKEND_CAMERA_URL` and `BACKEND_ROVER_CAMERA_URL`; optional corresponding
`_AUTHORIZATION` and `_CA_CERT` values remain server-side. The existing rover
feed commonly uses `http://127.0.0.1:8088/stream.mjpg`; verify the current source
and sole SDK owner rather than opening a second rover connection.

Run backend on loopback 8000 with distinct >=16-character OPERATOR_TOKEN and
PEER_TOKEN, and BACKEND_COOKIE_SECURE=0 for local HTTP only. Configure
frontend/.env with matching OPERATOR_TOKEN and BACKEND_URL, then run Vite.
Production requires an /api reverse proxy and normal operator-session setup;
the Vite local session helper is development-only.

Start/restart vision from the current code, connect the selected fixed camera,
and enable its Grok assessment switch. Automatic classification must be enabled.
Configure vision's BACKEND_URL and matching PEER_TOKEN so events reach backend.
Seeing a camera in the console alone does not activate vision ingestion or arm
physical recovery. Do not assume already-running processes use the new code.

### 3. Live classification and speech acceptance

With a connected camera, confirm one real native classification and advisory,
actual classificationModel, numeric score, frontend classified notice, and
successful Grok voice output after Enable voice. Check `/api/models` and vision
health; configured model labels are not proof of live usage. Test an unsupported
or low-score object and confirm the manual handoff with no route launch.

### 4. Supervised physical acceptance, only when requested

Use a private VISION_RECOVERY_CONFIG based on
vision/camera-recovery.example.json. Set absolute robot Python/state paths.
`setupConfirmed` defaults false; only enable after checking the tested start,
orientation, clear route and target arrangement. Wildcard camera identifiers
allow camera replacement, not physical route recalibration.

The original measured route was physically tested before the native xAI
migration, including return and release. The new native API + camera-triggered
+ live notices/voice end-to-end combination has **not** been physically retested.
Only one machine may own the DJI SDK connection. Observe pickup and final
placement independently; ACKs do not prove a can is held or on the floor.
For an UNKNOWN job, physically confirm stopped before the explicit reconcile
command in contracts/camera-recovery-v1.md; never blindly replay an old mission.

### 5. Optional deployment/database acceptance

No production deployment or shared database migration was performed. PostgreSQL
integration checks were skipped without a disposable test DB. Existing vision
JSONB storage supports the additive fields; no migration was added. Peer
publication queue is bounded but not a durable outbox: failed delivery requires
checking health and reconciliation. Generic mapping/mission dispatch acceptance
is separate from this measured-route demo.

## Verification completed

- Vision/shared: 205 passed plus 7 subtests (includes native API wire mocks,
  recovery gates, structured progress validation and sequence checkpointing).
- Robot: 136 offline tests passed; synthetic odometry/SDK, no physical motion.
- Backend: 103 tests run, 19 PostgreSQL tests skipped, remaining 84 passed.
  The five operator-console tests passed again after the final metadata change.
- Recovery policy: 45 tests passed, then the policy suite passed all 11 after
  adding the native-model environment regression (combined coverage: 46 tests).
- Frontend: 41 tests passed; TypeScript and Vite production build passed again
  after the final model/confidence display change.
- Browser: actual frontend + actual backend with clearly labelled synthetic
  fixed/rover JPEGs and mocked native TTS returning a short test tone. Both feeds
  decoded, all route notices streamed, audio requests occurred in order,
  reconnect did not repeat old speech, 42% score produced Needs a person,
  browser error/warning log was empty. This is not live camera/Grok acceptance.
- `git diff --check` passed before final staging.

Useful local environments (temporary paths may disappear):

- `/private/tmp/pavia-merge-review-venv/bin/python`: backend dependencies.
- `/private/tmp/pavia-robot/.venv/bin/python`: SDK + robot offline dependencies.
- `/private/tmp/pavia-langgraph-env/bin/python`: recovery policy tests.
- Primary checkout `vision/.venv/bin/python`: vision/tests; backend-linked tests
  can additionally use PYTHONPATH pointing to the backend venv's site-packages.

For standard commands see root README and module READMEs. Loopback HTTP server
regressions need local socket permission in sandboxed environments. No tests
need to connect to a physical rover or shared production DB.

## Screenshot fixture requested by the user

The user also requested the frontend with mock information just for a screenshot.
A disposable fixture lives outside Git at `/private/tmp/pavia_console_fixture.py`.
It serves labelled synthetic frames and mocked speech only; it does not command
hardware or contact xAI. Test backend port 8511 and Vite port 5178 are separate
from the real service configuration. Check whether these processes are still
running before reusing the screenshot URL. Keep mock data clearly identified;
do not use it as live acceptance evidence or production configuration.
