# Intermediate checkpoint — not dispatch-ready

This branch implements camera frame ingestion/retention, phone capture page,
local and rover adapters, YOLOE wrapper, conservative pixel tracking, Grok
classification/confirmation, metric evidence gating, diagnostic REST/SSE and
bounded peer delivery. It does not implement teammates' dashboard or database.

Validation at this checkpoint: 60 vision tests and 12 board tests pass. A real
YOLOE-26s prompt-free inference ran on this Mac using MPS and PyTorch 2.15.0
with a synthetic image. That proves runtime loading, not detector accuracy or
multi-camera throughput. Initial loading/download/warm-up took about 16.7 s.

Not verified: physical iPhone HTTPS streaming, actual Mac camera capture,
rover hardware/bridge, live Grok (key pending), and peer backend/mapping ingestion.

Known work before dispatch-ready review:

- Resolve conservative eligibility handling when a newer same-epoch camera frame
  is waiting for processing; verify inference admission avoids starvation.
- Add event-specific object/alert schemas and transition/reconnect fixtures for
  frontend/backend; consumer interface reviews are still open.
- Make mapping same-observation retry idempotent while rejecting older/conflicting
  results; keep callback schemas/prose aligned with required evidence fields.
- Bound JSON/concurrent upload/SSE admissions, fix snapshot/event ordering, and
  retain delivery-gap health without sharing the master token with peers.
- Add documented TLS setup, integration demo/baseline and hardware acceptance.

Do not merge or dispatch the rover based only on passing current tests. The
human lead reviews/merges; backend remains authoritative for approved missions.
