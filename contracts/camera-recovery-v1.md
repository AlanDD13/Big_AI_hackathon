# Camera-triggered measured recovery

This opt-in demo connects any configured **FIXED** camera to the measured
30 cm forward / right 90° / 154 cm forward route from PR39, followed by rover
camera pickup, return, lower/open/raise. It does not create a route to an
arbitrary detected floor position. The rover must start at the tested mark;
interchangeable cameras must observe the same physical pickup setup.

## Classification and score

Automatic assessment uses one structured `assess_object` call for the same
target crop and surrounding context. `visualPickupAdvisory.grabbability_score`
is an additive nullable number from 0 to 1. It is the model's grasp-feasibility
estimate, not a calibrated probability, detector score, classification confidence,
or evidence of physical pickup. Missing/null/invalid scores cannot dispatch.
Old producers remain valid; their missing scores remain unknown.

The measured can profile requires live `grok` classification, HIGH classification
confidence, TRASH / INANIMATE / not carried, POSSIBLE visual suitability,
LIKELY grasp estimate, and score >= configurable threshold (default 0.8).
It also requires fresh non-future fixed-camera observations, five seconds of
stationarity, no exclusion, a can label, correct assessment-frame lineage, and
no second can/person/animal in that camera's pickup area. Other object profiles
notify the frontend rather than reusing the can macro.

Existing generic calibrated metric eligibility and backend `dispatchEligible`
remain unchanged. If mapping is supplied and rejects the target, recovery is
rejected. With no mapping, only this explicitly confirmed measured demo setup
can run; an image grid or estimated score does not supply metric navigation.

## Arming and interchangeable cameras

Copy `vision/camera-recovery.example.json` to an ignored private file, set the
existing robot Python executable and persistent local state directory, and set
`VISION_RECOVERY_CONFIG` to its absolute path when starting vision. Existing
`BACKEND_URL`, separate `PEER_TOKEN`, camera authentication and live model
configuration are required. No new browser credentials or HTTP controls are added.

`setupConfirmed: true` is explicit operator confirmation of the measured route,
starting pose, supported lightweight can and sole SDK owner. It defaults false.
Camera/session/geometry `"*"` accepts any registered fixed camera and new camera
session after fresh observation/classification; no camera ID has to be chosen.
Optional exact IDs and `pickupAreaPx:[x1,y1,x2,y2]` restrict a fixed installation.
Do not reuse an image ROI after changing the camera geometry.

The local SQLite journal atomically reserves one job per object and one active
rover run. A scene latch also prevents a second camera or fresh track ID from
launching another run for the same scene, including after process restart.
After completion, five seconds of fresh empty-scene frames are required before
a new object can launch. Camera loss or changing IDs does not clear this latch.
RUNNING/UNKNOWN jobs block further starts until the operator confirms the rover
stopped and runs:

```sh
python -m vision.camera_recovery /absolute/state/dir/recovery.sqlite3 --confirmed-stopped
```

This reconciles a stopped state; it never repeats the old job. Keep the journal
between restarts. A local process lock also rejects competing updated bridge
or direct SDK sessions. There must still be only one SDK owner on the LAN.

The worker executes the fixed checked-in robot runner without a shell, passes
the exact classification crop and target object ID, and captures its report.
Before the grasp stage, lost/stale/revised/excluded/ambiguous target evidence
requests interruption and preserves an UNKNOWN reservation. A controller-written
phase file, rather than model text, identifies the start of the grasp macro.
Normal target disappearance during the grasp does not interrupt return.
SDK warnings remain logged; known failed lower/close/lift results abort.
Reports continue to distinguish ACKs from independently verified holding,
cross-camera association and placement.

## Frontend notifications and delivery

Additive `vision.v1` full-object alerts:

| Event | Meaning |
| --- | --- |
| `alert.pickup_unavailable` | Rejected, uncertain, unsupported, unarmed or busy; no new route launched |
| `alert.recovery_started` | A job was durably reserved and the route task scheduled |
| `alert.recovery_completed` | Return and release commands completed; placement remains unverified |
| `alert.recovery_failed` | Outcome needs an operator check; automatic retries blocked |

Each has a required uppercase `reason`, ordinary object lineage and optional
grabbability score in its advisory. Backend persists/replays these through
existing JSONB storage and publishes the corresponding dashboard SSE event.
The frontend subscribes to all four, shows unread alerts and the model score
in object details. An alert does not grant ordinary mission dispatch authority.

`Publisher` mirrors `frame.detections` to backend `/api/vision-events` as well
as mapping `/detections`. Backend validates `vision-detections.schema.json`
fields as a sequence checkpoint, stores the original canonical event, and does
not project those pixels as mapped evidence. This preserves the single producer
sequence; backend still rejects real delivery gaps and conflicting retries.
Object/alert envelopes continue to use `vision-event.schema.json`.

Upgrade backend and frontend consumers before enabling this vision producer.
The existing bounded peer queue is not a durable outbox: delivery failure is
visible in vision health and requires reconciliation. No shared database schema
or migration changes are introduced.
