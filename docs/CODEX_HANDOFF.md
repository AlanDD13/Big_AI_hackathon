# Codex handoff — rover camera, then motion tools

Updated 2026-11-04, 20:28 Europe/Rome. Written because Alan will continue in Codex.
This is not a statement that driving or grabbing is ready.

**Review update:** PR #17 now exists at dd347b4, but review found physical adapter,
watchdog, stop/rearm and pending-stop pickup defects. PR #16 is merged and current
main has durable vision ingestion. The older publication/persistence statements
below are historical. Read [INTEGRATION_REVIEW.md](INTEGRATION_REVIEW.md) for the
verified checkpoint and owner fix request before continuing. No movement or
pickup has been authorized by this review.

## Verdict

Do not start a move or a grab. Platon's closed tool bridge is a claim, not
an implementation. The branch `task/platonyesin-dev/command-bridge` was not on
GitHub at this writing, and there is no pull request. The camera process he
reported at `http://192.168.5.194:8004` did not accept a connection from this
checkout a minute later. Treat that URL as his last reported address, then
check `GET /health` before using it.

What is ready:

- Detection on a rover frame is specified. Vision polls
  `GET /camera/frame` through `RoverHTTPAdapter` when `ROVER_BRIDGE_URL` is set.
- The camera contract is JPEG plus `X-Camera-Id`, `X-Session-Id`,
  `X-Geometry-Version`, `X-Frame-Id`, `X-Captured-At`, and
  `X-Timestamp-Basis`. The same frame id is the same image. No frame, or a
  frame older than 1.0 s, is `503 STALE_FRAME` and must not be invented.
- Camera id for this rover is `rover-1`. Bearer is the current `PEER_TOKEN`
  posted on the board in comment 6638278492. Do not copy it into this file,
  Git, or a new board comment.
- Platon said the running camera process, if it is still up, exposes only
  `GET /camera/frame` and `GET /health`. No drive or pickup routes.

What is not ready:

- `POST /manual/lease`, `POST /manual/drive`, `POST /manual/release`,
  `POST /stop`, `POST /commands/pickup`, hold, and rearm are the routes he
  agreed to build. They are not merged and were not on a remote branch.
- Pickup stays simulated until he separately enables physical manipulation.
  A camera frame never authorizes pickup. `pickupVerified` stays false.
  `dispatchEligible` stays false.
- The Grok adapter can only `inspect_objects`, `explain_alert`, and
  `propose_collect`. `propose_collect` queues a mission and does not call
  the robot. Do not add raw SDK or velocity calls to that adapter.
- A new rover sighting is not in Supabase. `BackendState.ingest_vision` on
  the merged ingest keeps the object in memory. LcyShox measured zero
  `rover_backend.objects` rows for that path. Mapping observations and queued
  missions do persist. Do not assume the database already has the
  classification for the object on the rover camera.

## Who owns the next move

- Alan / vision: detect the object on the frame. Match a stored classification
  only when a row already exists for that object. Do not command motors.
- Tezqx / mapping: turn a located object into a route. A rover pixel is not a
  floor coordinate.
- zekihxh / backend: persist vision objects if the dashboard and the decision
  loop need them after a restart. He owns the database login.
- platonyesin-dev / robot: the only DJI SDK session, and the only process that
  may implement drive, stop, hold, pickup, and rearm. His watchdog stays in
  front of every tool. The model must not receive the SDK.

## Decision loop to build later

Build this only after his tool routes exist on a branch you can call.
Simulator first. One tool per turn.

1. Analytical gate. Read the newest frame and rover status. If the frame is
   stale, telemetry is stale, or the stage is `UNKNOWN` or `STOPPED`, the
   only choices are observe, stop, or rearm-from-STOPPED. Do not ask the
   model to invent a velocity.
2. Grok chooses one allowed tool from the latest frame and the eligible list.
3. Analytical check again. Reject a drive outside 0.35 m/s, 0.35 m/s, and
   45 deg/s, a pickup before hold, a pickup without a fresh target revision,
   or any call while a manual stop is latched.
4. Send that one call to Platon's bridge with `PEER_TOKEN`. Then take the
   next frame.

A rejected tool ends the turn. Do not open the SDK from LangGraph or from
vision. Do not enable pickup on the camera process.

## Local checkout

Branch `task/AlanDD13/vision-pipeline`, last pushed commit `8739ac9`.
Preserve uncommitted edits in `vision/cameras/adapters.py` and
`vision/tests/test_cameras.py`. Preserve untracked `.agents/`,
`docs/EVALUATION_FIT.md`, `docs/FILMING_IDEAS.md`, and
`docs/HACKATHON_ALTERNATIVES.md`. Do not commit those research notes.
Do not reset or pull over the dirty camera files.

Draft PR #3 stays draft. PR #14 vision ingest is merged at `1dbc592`.
PR #15 camera service is merged at `03fd321`. PR #12 frontend fixture
screen is still draft at `efe794e`.

## Do not do

Do not drive the physical rover. Do not close the gripper. Do not post
tokens, database URLs, or model keys. Do not edit `robot/**`, `frontend/**`,
`backend/**`, or `mapping/**` without that owner's handoff. Do not merge.
