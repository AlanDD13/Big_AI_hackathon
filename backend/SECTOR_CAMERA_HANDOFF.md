# Sector arrival and dashboard camera handoff

Backend owner: `zekihxh`. Checkpoint: 2026-11-05, based on main `7fb18c6`.
This document answers board requests `6653894820`, `6653983899`, and
`6654286765`. It records current behavior and decisions still required;
the proposed camera route and navigation fields below are not implemented APIs.

## Current implementation

| Capability | Current behavior / evidence |
| --- | --- |
| Operator authentication | `POST /api/session` accepts the operator token and sets the `session` cookie. Dashboard reads use that cookie; peer credentials are separate. |
| Collection queue | `POST /api/missions` accepts exactly `objectId`, `destinationZoneId`, and `source: "manual"`, plus `Idempotency-Key`. It queues collection after evidence/profile/planning checks. It has no sector-navigation mode. |
| Planning | `collection.MappingClient.plan(request, evidence)` is an injected Python protocol returning `Route(plan_id, rover_id, valid_until)`. The production app uses `UnavailableMapping`; `BACKEND_MISSION_FIXTURE_MAPPING=1` enables only a labelled queue fixture. There is no production P3-to-P5 HTTP planner client. |
| Dispatch | `dispatch.advance_dispatch` is an internal library with injected store and bridge. The HTTP app does not call it. No navigation worker or navigation command HTTP adapter is configured. |
| Durable authority | Existing collection storage and dispatch SQL support object reservation, mission/command identity, one active rover claim, revision checks, and retaining authority for `unknown`. They are not an implemented sector-navigation admission path. |
| Robot interpretation | `bridge_client.py` interprets the existing simulator/pickup stages. It does not implement an HTTP client or a sector-arrival contract. Do not substitute pickup for navigation. |
| Demo sectors | Main rejects uncalibrated demo-sector bodies at `/api/observations`. Separate branch `task/zekihxh/demo-sector` at `7fcddc4` supports display-only sectors on identified Vision events, without dispatch authority. It is not merged. |
| Camera | No backend feed/proxy route exists. `frontend/src/CameraWidget.tsx` is currently an empty widget. |

Existing queue request (collection only, not sector navigation):

```json
{
  "objectId": "fixture-camera:fixture-session:fixture-track",
  "destinationZoneId": "fixture-drop-zone",
  "source": "manual"
}
```

A new accepted queue request returns 202; an identical same-session key/body
returns 200 with the stored mission. A changed body conflicts. The backend
assigns the mission ID, the planner supplies the rover/plan identity, and a
committed dispatch claim supplies the command ID. A queue response never means
motion was authorized. There is no navigation arrival/cancel event fixture yet.
Those names must be agreed rather than borrowed from collection or pickup.

## Sector-arrival decision record to complete together

P4 proposed a first checkpoint: an operator selects a test sector; the rover
starts from verified fixed HOME and follows recorded pose/heading waypoints to
a safe staging/viewing pose. Vision association and pickup come later. Backend
can support preparing this checkpoint, but the proposal is not yet an agreed
physical contract. Alan explicitly requested discussing interfaces in person.

| Decision / deliverable | Owner asked | Needed before |
| --- | --- | --- |
| Confirm first slice and whether the local operator starts a P4 fixture while backend remains disconnected | Alan / humans, P4 | Choosing an execution entry point |
| Authoritative sector convention/version, measured HOME/axes/heading, map-to-odometry transform and current localization | P5 with P4 | Resolving a sector to a physical destination |
| Taught versus planned routes; route identity/version/source, waypoints and staging pose; expiry, clearance and arrival tolerances | P5 with P4 | Defining the plan fixture |
| Bounded navigation capability, start/status/stop/cancel operations, manual arbitration, stop confirmation and arrival evidence | P4 | Defining the command/status fixture |
| Operator trigger, ID ownership, plan acquisition, durable admission, restart reconciliation and UI outcomes | P3 with P4/P5 | Implementing backend navigation |
| Shared contract path and exact file editor | P3/P4/P5 | Editing shared files concurrently |

The navigation fixture must explicitly identify the selected sector, layout/map
version, route version/source, verified HOME pose/reference and freshness,
staging pose, waypoints, rover, limits, deadline and required arrival evidence.
These are required concepts, not a proposed JSON schema or permission to use
guessed numeric values. Do not derive coordinates or motion from `B3` alone.
Current main's A-bottom document and the unmerged ROW_A_TOP proposal differ;
the physical mapping agreement must resolve this before execution.

Backend recommendation for the first physical checkpoint: if the humans and
P4/P5 agree, keep backend no-dispatch and use a locally operator-started P4
fixture for the measured HOME-to-staging route. Backend HTTP dispatch follows
only after route/status fixtures and authority rules are agreed and tested.
This recommendation does not start a run or authorize physical movement.

Required navigation acceptance cases after agreement:

1. One explicit selection binds one route/version/rover; replay does not send a
   second command. A conflicting key and a concurrent rover claim are rejected.
2. Missing, stale or changed HOME/pose/layout/route evidence prevents start.
   Unknown sectors, display-only geometry and unreachable staging poses fail.
3. A lost ACK, restart, mismatched identity or missing command status retains
   the original authority as unknown. Reconcile by querying the original ID;
   do not allocate a replacement command or automatically resend it.
4. Cancel, explicit stop, deadline, manual takeover and link/watchdog failure
   have agreed outcomes. A command acknowledgement is not stationary or arrival
   evidence. Unknown stop confirmation continues blocking another mission.
5. Arrival requires the agreed fresh evidence and tolerances. No pickup or
   transport action follows arrival at this checkpoint.

## Proposed same-origin camera contract for agreement

P3 proposal sent to P5 and frontend: `GET /api/camera/feed`, authenticated with
the existing operator session, with upstream URL and authentication held only
by backend. No URL query parameter supplied by the browser, redirects to an
arbitrary source, upstream credentials, or credential-bearing errors.

Serve `image/jpeg` or `multipart/x-mixed-replace` according to the agreed
upstream source, with cache disabled and the actual multipart boundary retained.
Do not silently switch formats or manufacture frame/session/geometry IDs.
Missing/expired operator session returns 401 using the existing error envelope.
Proposed pre-stream connection/auth/media/first-frame failures return 503
`CAMERA_UNAVAILABLE`, with a generic message. Once response bytes have begun,
terminate a failed stream; a later JSON error cannot replace HTTP 200. Frontend
must reconnect and distinguish connecting, live, stale and disconnected states.
Agree reconnect intervals, first-frame/inactivity deadlines, JPEG polling versus
MJPEG, multi-viewer sharing, and active-stream behavior on logout/session expiry
before implementing the proxy.

Read-only source inspection at Mapping head `ee7adc1` found:

- `/stream.mjpg` is MJPEG, but Flask binds `127.0.0.1`; another laptop cannot
  reach that listener. The current default `VIEWER_PORT` is 5051.
- `_frames()` repeatedly emits cached `_latest_jpeg` every 50 ms. Receiving
  chunks or counting displayed frames does not establish fresh camera capture.
- No frame/session/geometry identity or health endpoint is supplied by those
  Flask routes. An old JPEG can be re-emitted while capture is no longer fresh.

P5 must provide a LAN-reachable, configured feed and capture freshness/status
semantics; choose listener/network exposure with its owner. Do not infer camera
health from multipart traffic or placeholder frames. Frontend must not label the
feed live until real capture freshness is established. If the source cannot
provide identity, state that limitation and keep overlays unassociated.

Required camera acceptance cases after agreement:

1. Unauthenticated/expired session cannot open the feed; credentials and source
   URLs never appear in browser bundles, redirects, logs or public errors.
2. Real JPEG/MJPEG displays in the existing Main camera widget through the
   same-origin backend, including desktop/mobile and keyboard-accessible retry.
3. Upstream auth failure, timeout, invalid media and missing first frame fail
   before streaming. Disconnect releases resources and does not accumulate
   frame queues. Multiple viewers follow the agreed shared-source policy.
4. A source that repeats old frames or emits placeholders is stale, even if
   bytes keep arriving. Source identity/version changes invalidate associated
   overlays; absent metadata remains absent.
5. Stop/restart and network loss exercise reconnect and stale/error UI. Record
   actual capture timing rather than treating widget render rate as camera FPS.

## Runnable offline checks and integration order

From the repository root with backend dependencies installed:

```powershell
python -m unittest backend.tests.test_collection backend.tests.test_dispatch backend.tests.test_bridge_client backend.tests.test_api backend.tests.test_mission_http -v
```

These tests use fake clients and cover existing admission/dispatch semantics.
Database cases skip unless a migrated disposable `BACKEND_TEST_DATABASE_URL`
is configured. This is not a runnable sector-navigation integration or physical
acceptance. No live endpoint, model or SDK is needed. Use only disposable test
storage; do not set that variable to the shared Supabase database.

Existing environment names: `OPERATOR_TOKEN`, `PEER_TOKEN`,
`BACKEND_COOKIE_SECURE`, `BACKEND_DATABASE_URL`,
`BACKEND_MISSION_FIXTURE_MAPPING`. Backend README uses local port 8000.
No sector-service URL, new timeout, heartbeat or camera configuration variable
is agreed in this checkpoint. Existing bridge library thresholds are a 0.5 s
pickup-pose age, 3 s pickup authorization, and 3 s status-unknown threshold;
these are not a sector-navigation heartbeat or motion budget. The physical
watchdog remains P4's independent <=300 ms responsibility.

Integration order: record human decisions -> P5/P4 publish matching route and
status fixtures -> claim shared contract files -> backend admission/reconciliation
with fakes -> disposable-store/restart/concurrency checks -> owner-coordinated
supervised physical acceptance. Camera display can advance independently once
the source and frontend contract are agreed. Alan reviews/merges task PRs.
