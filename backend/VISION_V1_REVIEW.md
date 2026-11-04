# P3 review of vision v1

Reviewed AlanDD13's draft at 6553fe0 and checkpoint e3976c1. Shared Supabase Postgres supersedes SQLite per board comment 6633371895. The checkpoint typed frame detections and added receivedAt and invalidReason to mapping evidence. P3 still needs typed object/alert events and a backend observation fixture. The findings below are proposed P3
consumer decisions for Alan, Tezqx, LcyShox and platonyesin-dev to review.

## Producer handoffs

- Accept object.observed, object.classified and object.updated through the
  three vision ingress paths in the draft. Keep P5 metric observations on the
  separate observation path. Agree event-specific payload schemas and enforce
  vision.v1, known event types and endpoint/type combinations. Require explicit
  state, exclusions and revocation reasons. Specify revision scope, resets and
  snapshot recovery after outbound delivery fails. Producer sequence alone
  cannot order both vision and mapping updates.
- Join by cameraId, sessionId and trackId. Correlate enrichment to the exact
  geometryVersion and frameId. Define which camera sessions are current and
  expose resets so stale results cannot restore a revoked target. Agree the
  backend metric observation fixture, including receivedAt, map and calibration
  versions, validity, null positions, uncertainty and invalidReason.
- Apply the clarified gate to the same uninterrupted fixed-camera episode.
  Require at least 10 samples spanning at least 5 s, consecutive gaps at most
  1 s, newest receivedAt at most 1 s old, distance at most 0.05 m from the
  first valid anchor, and uncertainty at most 0.025 m. At exactly 2 Hz require
  11 samples. Reset on invalid samples, loss or provenance changes. Require
  completed eligible semantics and a tested pickup profile before dispatch.

## Dashboard interface proposal

- Normalize producer envelopes into backend object, alert and mission updates.
  Commit each state change and its event together. Deduplicate producer eventId,
  reject obsolete updates within their producer scope and assign an independent
  increasing backend event cursor. Publish complete changed entities with their
  backend revision so the UI can ignore older updates.
- Return the snapshot and its cursor from the same consistent read. Reconnect
  from that cursor to replay newer SSE events. Require a fresh snapshot when
  replay is unavailable. Agree the snapshot response and cursor transport with
  LcyShox before freezing schemas.
- Show OBSERVING immediately with null position and pending enrichment. Include
  provenance, timestamps, metric position, calibration and uncertainty,
  stationary duration and sample count, detectorScore, classification/provider
  and the qualitative Grok confidence. Expose potentialTarget separately from
  backend dispatch eligibility and its reason. Persist alerts on transitions.
- Serve evidence through an authenticated backend endpoint using stored evidence
  IDs. Resolve only trusted relative frame references against the configured
  Vision host with server-side credentials and redirects disabled. Return an
  unavailable result if the exact image has expired. The browser uses backend
  credentials only.

## Robot handoff

Before pickup, revocation invalidates pending confirmation and triggers a hold.
For an approaching rover, P3 needs the P4 stage and an acknowledged hold/stop
result keyed to the command and mission IDs. If the result is unavailable,
publish the unknown outcome and keep the rover slot reserved. Distinguish
authorized PICKING motion using bridge-reported stage. Agree stage names,
acknowledgment, cancellation and reconciliation fixtures with P4.

## Acceptance fixtures to agree

1. OBSERVING with null mapping, followed by mapping and pending, completed or
   unavailable classification. The object ID stays stable across enrichment.
2. Stationary progress through eligibility, including ten samples at 2 Hz
   falling short and eleven passing. Excluded subjects remain non-dispatchable.
3. Duplicate event, delayed enrichment, lost track, expired observation and
   session or geometry reset. An older result cannot re-enable the object.
4. Eligibility and revocation alerts fire once per transition. Reconnect from a
   snapshot cursor reconciles changes without duplicate alerts.
5. Expired evidence and an unconfirmed hold produce explicit unavailable or
   unknown status without granting dispatch.
