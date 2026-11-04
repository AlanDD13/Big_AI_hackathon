# P3 first slice: mapped observation to collection mission

Local proposal for zekihxh. AlanDD13 selected shared Supabase Postgres in board comment 6633371895. Earlier SQLite notes record exploratory work only. Based on the Rover implementation proposal, backend README and draft shared contracts on main. Wire examples below describe the fields to agree with AlanDD13, Tezqx and platonyesin-dev before starting the integration branch.

## 1. Ingest one mapped object

P5 sends a fixture to POST /api/observations. Include eventId, cameraId, frameId, capturedAt, trackId, classLabel, detectorScore, evidenceRef, floorPointM, positionErrorM, mapId and calibrationId. An unknown floor point is null. The fixture should represent a known demo object within the calibrated area, with a stable track and a tested pickup profile available from backend policy.

Persist the observation and derive a stable objectId from the stored camera and track identity. Deduplicate event IDs. A newer event for the same track updates the same object; older observations must not overwrite newer state. Use the stored object ID in the operator request.

## 2. Queue a manual collection request

POST /api/missions takes objectId, destinationZoneId and source plus an Idempotency-Key. Validate the object, its freshness, track stability, location, uncertainty, category and tested pickup profile. Use a fake P5 client to supply the map, pose and valid waypoint plan. Reserve the object and insert the mission and its idempotency record in one Postgres transaction. Return HTTP 202 with missionId and QUEUED. Replaying the same key and body returns the existing mission status; changing the body returns 409.

Checkpoint: one mapped object becomes one queued mission. The fake robot records zero commands at this checkpoint.

## 3. Advance one mission with the fake robot

The backend README also expects status for failed and unknown execution. After the queue checkpoint, add a worker step that preflights the object, pose and route, enforces one active mission per rover, and persists dispatch intent and a stable command ID before calling the fake robot. Feed fake status events back into the coordinator. Persist the outcome and make it visible to the UI through mission reads and the event stream.

An acknowledgment timeout leaves execution unknown. Reconcile by looking up the same command ID. Hold the rover slot while execution is uncertain, and prevent another dispatch until the outcome has been reconciled.

## 4. Verify through backend entry points

| Scenario | Expected result |
| --- | --- |
| Valid observation and manual request | One stored object, one reservation and one queued mission; zero robot commands during enqueue |
| Duplicate event, newer observation, then older observation | One object; duplicate has no effect and stored data stays at the newest timestamp |
| Same request and idempotency key repeated or sent concurrently | Same mission ID; one reservation and one mission |
| Same key with a different destination | Conflict; original mission retained |
| Two different requests race for the same object | Only one reservation and mission succeed |
| Missing object, stale observation, null or uncertain position, unsupported pickup profile | Request rejected; no mission or robot command |
| Invalid, expired or unavailable mapping plan | Request rejected or dispatch held at preflight, depending on when the plan becomes unusable |
| Two queued missions for one rover | Only one robot command issued while the first mission is active |
| Explicit execution failure or duplicate status event | Failure persists and is visible to UI; duplicate event has no effect |
| Acknowledgment lost after fake robot records the command | Status shows unknown; lookup uses original command ID; no duplicate send or second active mission |

## 5. Add Grok after the manual checkpoint

Allow structured inspection, alert explanation and collection proposals. Collection proposals go through the same validation and policy as manual requests. Test ambiguous input, malformed or unsupported intent, unknown IDs and provider timeout before wiring the live adapter.

## Contract decisions needed from AlanDD13

- Observation batch and pose schemas, timestamp format and units, track stability signal and freshness/uncertainty limits.
- Mission and robot event enums, transition rules, cancellation and timeout/reconciliation semantics, and SSE payloads.
- Planning inputs, waypoint and route expiry formats, pickup-profile and destination IDs, and runnable integration fixtures.

Once the lead lands agreed contracts, start from origin/main on task/zekihxh/first-mission-slice and keep implementation, dependencies, fakes and tests inside backend/. Verify the queue checkpoint first, then fake dispatch and UI-visible status.

## Pending vision handoff from board issue #1

Alan requested that a new detection appear as OBSERVING immediately, even while
mapping or classification is pending. Vision publishes API events and P3 owns
Postgres. Keep detectorScore and qualitative Grok confidence separate. Publish
SSE updates for scores, map position, stationary progress and classification.
Emit one eligibility notification per transition into eligibility and surface
revocation or failure without alerting on every frame. People, animals, carried
objects and uncertain objects remain ineligible.

For contract review, P3 needs stable event IDs, camera/session/track identity,
event ordering and replay semantics, a correlation key for late map and
classification results, and explicit eligibility and revocation signals.
Agree whether P3 consumes stationary progress directly or derives it from
timestamped evidence. Late enrichment must preserve the OBSERVING object ID
and must never re-enable a revoked target using stale data.

Add acceptance cases for detection before enrichment, out-of-order results,
repeated eligibility events, exclusion of disallowed targets, and revocation
during approach. Agree the hold/stop behavior with P4 before live dispatch.
This is pending review of contracts/vision-v1.md.

## Storage checks for implementation

Re-run the rollback, concurrent replay and snapshot checks in Postgres. Add these product tests as backend entry points become available:

- Inject a failure between reservation, mission and idempotency writes and
  verify the transaction leaves no partial records.
- Race requests over separate connections. The same key replays one mission,
  and distinct keys cannot reserve the same object concurrently.
- Commit an event during a snapshot read, then resume SSE from the returned
  cursor. The change must appear in the snapshot or replay, without a gap.
- Restart after dispatch intent is persisted or the acknowledgment is lost.
  Reconcile by the original command ID and keep the rover slot reserved while
  the outcome is unknown.
