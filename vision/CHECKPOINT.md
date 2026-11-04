# Intermediate checkpoint — not dispatch-ready

This branch implements camera frame ingestion/retention, phone capture page,
local and rover adapters, YOLOE wrapper, conservative pixel tracking, Grok
classification/confirmation, metric evidence gating, diagnostic REST/SSE and
bounded peer delivery. It does not implement teammates' dashboard or database.

Validation at this checkpoint: 91 vision tests and 16 root tests pass. A real
YOLOE-26s prompt-free inference ran on this Mac using MPS and PyTorch 2.15.0
with a synthetic image. That proves runtime loading, not detector accuracy or
multi-camera throughput. Initial loading/download/warm-up took about 16.7 s.

Not verified: physical iPhone HTTPS streaming, live IP-camera stream at
`http://192.168.5.146:8081/video`, actual Mac camera capture,
rover hardware/bridge, live Grok (key pending), and peer backend/mapping ingestion.

Known work before dispatch-ready review:

- `vision/cctv_monitor.py` is included on `task/AlanDD13/vision-pipeline` as an
  explicit standalone OpenCV IP-camera capture/debug helper
  (`http://192.168.5.146:8081/video`) supporting both single-frame capture
  (`get_latest_frame`, `encode_frame_to_base64`) and 5–10 frame / 2-second burst
  capture (`get_frame_burst`, `select_highest_confidence_frame`,
  `build_burst_payload`) that fails closed on empty bursts, confidence ties,
  unknown sectors, or `(0, 0)` fallback coordinates. Standalone single/burst
  capture does not mint a `vision-v1` `sessionId`, `geometryVersion`, or
  `frameId`, `MappingEvidence` continues to require valid calibrated
  observations or an explicit `invalidReason` (never `(0, 0)`), and `mapping/`
  still does not open camera streams directly.
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
