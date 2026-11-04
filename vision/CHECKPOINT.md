# Intermediate checkpoint — not dispatch-ready

This branch implements camera frame ingestion/retention, phone capture page,
local and rover adapters, YOLOE wrapper, conservative pixel tracking, Grok
classification/confirmation, metric evidence gating, diagnostic REST/SSE and
bounded peer delivery. It does not implement teammates' dashboard or database.

Validation at this checkpoint: 76 vision tests and 12 board tests pass. A real
YOLOE-26s prompt-free inference ran on this Mac using MPS and PyTorch 2.15.0
with a synthetic image. That proves runtime loading, not detector accuracy or
multi-camera throughput. Initial loading/download/warm-up took about 16.7 s.

Not verified: physical iPhone HTTPS streaming, actual Mac camera capture,
rover hardware/bridge, live Grok (key pending), and peer backend/mapping ingestion.

Known work before dispatch-ready review:

- Newer same-epoch frames now suppress eligibility and in-flight inference can
  progress; verify continuous-camera throughput and avoid repeat notification
  churn without weakening freshness gates.
- Add event-specific object/alert schemas and transition/reconnect fixtures for
  frontend/backend; consumer interface reviews are still open.
- Mapping retries are now idempotent; align callback schemas/prose with per-track
  ordering and require non-empty invalid reasons. Producer boot/entity versions
  still need canonical shared types and recovery fixtures.
- JSON/upload/SSE bounds, snapshot cuts, sticky delivery-gap health and separate
  outbound peer credentials are implemented; peer reconciliation needs integration.
- Add documented TLS setup, integration demo/baseline and hardware acceptance.

Do not merge or dispatch the rover based only on passing current tests. The
human lead reviews/merges; backend remains authoritative for approved missions.

For the switch to Cursor, read ../docs/CURSOR_HANDOFF.md. Project skills/rules
live in ../.cursor; the prior Codex subagents and board watcher have stopped.
