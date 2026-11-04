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

- Typed object, alert, snapshot, and recovery fixtures are on this branch.
  Same-revision frame-pending suppression no longer emits a second target alert.
  A real loss during that suppression still revokes.
- Mapping evidence now rejects a missing invalid reason and IDs outside the
  shared ASCII set. The bridge and backend routes in the new contracts are not
  implemented here.
- Peer reconciliation still needs the backend and mapping services.
- TLS phone setup, live Grok, and hardware acceptance are not done.
- Supabase catalog inventory is blocked on lead OAuth for the read-only MCP.

Do not merge or dispatch the rover based only on passing current tests. The
human lead reviews/merges; backend remains authoritative for approved missions.

For the switch to Cursor, read ../docs/CURSOR_HANDOFF.md. Project skills/rules
live in ../.cursor; the prior Codex subagents and board watcher have stopped.
