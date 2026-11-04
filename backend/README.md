# Backend and Grok — zekihxh

Own `backend/**`: API, mission policy/state, event stream, Grok adapter, storage
migrations, consumer clients, module dependencies, tests and local fakes.
Consume mapped observations/plans and dispatch missions to the bridge.
Coordinate shared wire-contract changes with AlanDD13.

The FastAPI process serves the dashboard stream and mapping observations.
Observations are written to `rover_backend` when `BACKEND_DATABASE_URL` is set,
and reloaded when the process starts. Session cursors stay in process memory,
so a restart asks the dashboard for a new snapshot. The mission queue stores
accepted missions and publishes them through snapshots and `mission.updated`
events. Robot dispatch remains a separate library call.

## Run

From the repository root, with Python 3.12:

```powershell
python -m pip install -r backend/requirements.txt
$env:OPERATOR_TOKEN = "<at least 16 characters>"
$env:PEER_TOKEN = "<a different secret, at least 16 characters>"
python -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

`OPERATOR_TOKEN` and `PEER_TOKEN` stay in the environment. Do not commit them.
`BACKEND_COOKIE_SECURE=0` omits the Secure cookie flag for an http://localhost
demo. The default is Secure, matching the draft stream contract.

Operator routes use the `session` cookie from `POST /api/session`. Mapping
calls `POST /api/observations` with `Authorization: Bearer $PEER_TOKEN`.
Set `BACKEND_DATABASE_URL` to the runtime Postgres URL to store observations
in `rover_backend`. Without it, the process keeps observations in memory.

`POST /api/missions` requires an operator session, an `Idempotency-Key`, and
a JSON body with `objectId`, `destinationZoneId` and `source: "manual"`.
Queue creation returns 202. Replaying the same request in the same session
returns the stored mission with 200. New queue requests require PostgreSQL
and a planner. For disposable local testing, set
`BACKEND_MISSION_FIXTURE_MAPPING=1` and seed an explicitly labelled fixture
classification and approved pickup profile. The fixture produces a queue
row only. Event cursors retain the existing session and boot scope.

Vision ingest uses the same `Authorization: Bearer $PEER_TOKEN` check.
`POST /api/detections` accepts `object.observed` and shows the object
immediately. Until classification is completed, that status is `OBSERVING`
unless the object is `EXCLUDED` or `LOST`. `POST /api/classifications`
accepts `object.classified` and stores category, description, confidence
(`LOW`, `MEDIUM`, or `HIGH`), and `classificationProvider` (`fixture` or
`grok`) separately from `detectorScore`. `POST /api/vision-events` accepts
`object.updated` and `alert.*`. Event ids are deduplicated, so a replay does
not append another SSE event. A missing `visualPickupAdvisory` is no
assessment and is never graspable. When an advisory is present,
`pickupVerified` stays false. `dispatchEligible` stays false even if
`potentialTarget` is true. These routes do not open a mission or call the robot.

## Test

```powershell
python -m unittest discover -s backend/tests -v
```

Postgres checks also need `BACKEND_TEST_DATABASE_URL` pointed at a disposable
database that has already had `migrations/` applied. Without it, those tests
skip. See `SUPABASE_SETUP.md`.
