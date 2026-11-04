# Full-stack validation — 2026-11-05 (Asia/Tashkent)

Base: main `9a237fd`, including dashboard PR #12, vision ingest PR #14,
camera PR #15, persistence PR #16 and vision/shared contracts PR #3.
Frontend fixes are on `task/LcyShox/full-stack-validation`.

## Software checks

- Frontend: **25 passed**, TypeScript/Vite production build passed.
- Backend: **54 passed**, with all PostgreSQL integration tests enabled.
- PostgreSQL 18: **four SQL smoke tests passed** using the scoped runtime role.
- Robot: **47 offline tests passed**, including camera protocol/telemetry tests.
- Main-branch mapping: **13 passed**.
- Board/shared-contract fixtures: **20 passed** in a network-disabled Linux
  container using canonical Git source. A raw Windows checkout bind mount
  initially failed Bash parsing because Git had converted the script to CRLF.
- Vision: **142 passed, one failed**. Remaining failure is
  `vision/tests/test_cctv_monitor.py::test_remote_detect_frame_endpoint_and_cctv_monitor_remote_offload`:
  the monkeypatched `httpx.post` lambda does not accept the `verify` argument
  passed by `vision/cctv_monitor.py:98`. Reported to Alan at board #6640121917.
  Initial sandbox temporary-directory errors were eliminated by using a new
  workspace-owned pytest base directory. No vision source was edited here.

## Real local integration

Ran a real FastAPI/Uvicorn process, Vite same-origin proxy/session helper and
PostgreSQL 18 in a loopback-bound disposable Docker container. Migrations
001–007 were applied only to disposable databases. Database credentials were
generated in process memory; only the Python backend/tests used that role.

Verified operator-cookie and peer-token separation, immediate observed objects,
classification and advisory updates through HTTP/SSE, event replay deduplication,
classification confidence separate from detector score, pending clearing,
camera-wide alerts with no object ID, queued-mission creation/idempotency/conflict,
and no robot command allocation. The fixture planner/classification were clearly
labelled test data; queueing never activated a robot.

On an actual backend process restart, the previous session became invalid and
backendBootId changed. Mapping observations, the queued mission, vision object,
classification/advisory and the camera notice remained available. The browser
reauthenticated through the existing local helper and loaded the new snapshot.
The restarted backend's files matched the tested PR #16 head `4b86b16` exactly.

The first run against main `03fd321` caught vision ingest being memory-only:
HTTP accepted the object, but its database row was absent and it disappeared on
restart. Backend owner fixed this in PR #16, now merged. The repeated test passes.

Frontend also had a compatibility bug: snapshot parsing rejected a camera-wide
alert with `objectId: null`, and the stream did not subscribe to camera/detector
notice events. This change accepts and renders those notices without requiring
an object selection, keeps other objects visible, and adds regression tests.
A fresh snapshot waiting on an idle stream no longer retains a stale-data status.

## Live camera read check

The human explicitly approved read-only access after automatic approval review
initially blocked live-device credential/data access. No motion, pickup, new SDK
session or live model call was attempted.

The P4 camera-only process at `http://192.168.5.194:8004` returned health 200,
401 without its bearer and 200 for two authenticated JPEG frames. Frames decoded
at **640 × 360**, camera `rover-1`, session `rovcam-99bef5ba04ff1455`, geometry 1,
with RECEIVE timestamp basis. All six required frame headers were also
verified on a separate authenticated read. Frame IDs advanced **16823 → 16854** and TOF values
were **2170 → 2171 mm**. End-to-end frame reads from this Windows PC took about
**628 ms and 577 ms**; those are observed requests, not a latency guarantee.
Credentials and camera image bytes were not written to Git or the report.

## Supabase boundary

The shared Supabase project was **not tested or modified**. Alan and P3 explicitly
prohibited pointing suites at its shared `rover_backend` URL and confirmed local
PostgreSQL as this checkpoint (board #6639643926, #6639721056, #6639807324 and
#6640126184). No hosted backend URL or shared database login was supplied.
Local PostgreSQL validates the schema/runtime behavior; it does not verify
Supabase networking, connection pooling, hosting configuration or credentials.

## Repeat the safe local integration check

With Docker running, Node/npm dependencies installed in `frontend/`, and
`backend/requirements.txt` installed in the selected Python environment:

```powershell
python frontend/scripts/full_stack_test.py
```

The script creates only its own ephemeral local Postgres, uses generated
credentials, runs the backend/SQL/API/proxy checks, exercises lifecycle stages
and process restart, then stops its servers and removes its Docker container.
It returns nonzero on a failed check. Reports default to ignored
`frontend/.test-artifacts/`; `ROVR_TEST_OUTPUT_DIR` can select another output
directory. It never accepts or connects to a shared database URL.

For manual browser QA, use `--interactive` and write one of the printed commands
to the printed command file (`observe`, `classify`, `advisory`, `pending`,
`notice`, `restart`, `quit`). This mode still uses local fake inputs and no robot.
