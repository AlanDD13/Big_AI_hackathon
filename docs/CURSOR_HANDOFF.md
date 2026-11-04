# Continue Alan's work in Cursor

Saved on 2026-11-04 because Alan is approaching the Codex weekly limit.
This document is a checkpoint, not evidence that pending integration is done.

Open `/Users/alandd/Desktop/Pavia_grok_hackathon` in Cursor. Start a **new Agent
chat** so project rules/skills are discovered. Paste:

> Use /hackathon-vision. Read docs/CURSOR_HANDOFF.md, root AGENTS.md,
> vision/AGENTS.md and vision/CHECKPOINT.md, inspect the current Git state,
> then read the message board. Continue Alan's existing vision implementation
> on this branch. Before creating any subagent, check the board. Use the
> dedicated board-coordinator in a separate clone while coding continues;
> give coding agents disjoint files. Finish the typed contracts/fixtures and
> integration gaps listed in the handoff, run relevant tests, and commit/push
> checkpoints to the existing draft PR. Coordinate with the four owners through
> the board. Do not merge, edit their exclusive paths, or claim hardware/live
> Grok validation without running it.

## Repository and people

- Repo: https://github.com/AlanDD13/Big_AI_hackathon (private).
- Board: https://github.com/AlanDD13/Big_AI_hackathon/issues/1.
- Current branch: `task/AlanDD13/vision-pipeline`. Existing draft PR:
  https://github.com/AlanDD13/Big_AI_hackathon/pull/3. **Not merged.**
- Main was `f9dd8e4` at task start. Prior pushed checkpoints: `6553fe0`
  (initial contract), `e3976c1` (initial implementation), `bef5048` (Supabase
  replaces SQLite), `c8acff9` (human-selected project). Inspect current HEAD
  for the subsequent hardening and Cursor setup commits; do not reset to these.
- AlanDD13: human lead, vision/** and shared contracts/docs/integration/config.
- LcyShox: frontend/**; zekihxh: backend/**; Tezqx: mapping/**;
  platonyesin-dev: robot/**. Only the human lead merges/reassigns ownership.
- Preserve these pre-existing untracked documents, without staging them:
  docs/EVALUATION_FIT.md, docs/FILMING_IDEAS.md,
  docs/HACKATHON_ALTERNATIVES.md.

## Runtime and tests

The isolated Python 3.12 environment is already at vision/.venv. Core/test/ML
dependencies are locked in vision/uv.lock; uv is installed at
/opt/homebrew/bin/uv. From the repository root:

```bash
vision/.venv/bin/python -m pytest -q vision/tests
python3 -m unittest discover -s tests -v
git diff --check
```

At the handoff, the vision suite passes **76 tests** and the board suite passes
**12 tests**. No paid Grok API call was made.

Recreate the environment if necessary with:
`uv sync --project vision --extra test --extra ml`. This Mac used a writable
cache at /private/tmp/pavia-uv-cache and managed Python at /private/tmp/pavia-python
when sandbox defaults prevented installation. Do not assume those tmp paths
persist on a different machine.

YOLOE actually loaded and ran once on MPS using a synthetic white 640x480 image.
Weights are ignored at vision/.local/yoloe-26s-seg-pf.pt (38.1 MB). Initial
download/load/warm-up took about 16.7 seconds. This proves runtime loading,
not accuracy or multi-camera throughput. Set VISION_MODEL to that path when
running from repo root. No physical Mac/iPhone/rover camera has been verified.

VISION_TOKEN is required (>=16 unpredictable characters). Outbound URLs now
require PEER_TOKEN, distinct from VISION_TOKEN. See vision/settings.py for
VISION_CAMERAS, VISION_LOCAL_SOURCES, MAPPING_URL, BACKEND_URL,
ROVER_BRIDGE_URL, ROVER_CAMERA_ID, VISION_MODEL and VISION_DEVICE.
Grok uses XAI_API_KEY and GROK_VISION_MODEL (default grok-4.7); absent key means
UNAVAILABLE. No runtime secrets are supplied by this handoff.

## Implemented vision checkpoint

- Authenticated raw-JPEG ingest, fixed/rover camera registration, exact frame
  retention, phone camera page, Mac/OpenCV and robot HTTP camera adapters.
- Latest-frame bounded queue, YOLOE wrapper, conservative per-camera IoU
  tracking, immediate object/detection events and pixel living/overlap exclusions.
- Grok Responses API image classification and rover match confirmation with
  structured outputs, explicit fixture provider, bounded concurrency/deadlines
  and response validation. Provider metadata and confidence remain separate
  from detector scores. Fixture outputs do not count as live evidence.
- Metric evidence uses >=5 seconds AND >=10 observations, newest age/gap<=1 s,
  each uncertainty<=0.025 m and <=0.05 m from the fixed first episode anchor.
  Moving/stale/lost/changed-epoch evidence revokes eligibility; people/animals/
  carried/uncertain subjects remain excluded even when still.
- Newer unprocessed frames now suppress eligibility without resetting normal
  metric/classification evidence; fresh older-in-flight inference may finish,
  preventing inference starvation under faster ingress. Same-observation mapping
  retries are bounded/idempotent; conflicting/revoked-epoch results are rejected.
- Complete object publications now add producerBootId, entityVersion and
  eligibilityReason. revision is the evidence epoch, not the UI update counter.
  Target-revoked/removal payloads include complete state and reason; revoked
  includes HOLD_BEFORE_PICKUP. These additions still need typed shared schemas.
- Diagnostic REST/SSE, bounded request/upload/subscriber admission, atomic
  snapshot cut, separate peer credential, streamed peer response disposal and
  sticky delivery-gap health. Delivery remains bounded retry, not durable storage.

## Next work, in order

1. Owners review the canonical draft on this branch. It is not merged.
2. Typed object/alert/snapshot schemas, recovery fixtures, mapping invalid
   reasons, and ASCII ID rejection are recorded. Same-revision frame-pending
   suppression does not emit a second target alert; a real loss still revokes.
3. Backend implements `contracts/backend-stream-v1.md` (cookie session, opaque
   cursor, `snapshot_required` with no id). Robot implements simulator slice 1
   from `contracts/robot-simulator-v1.md` only. Manual control stays future.
4. Supabase inventory is blocked until Alan completes OAuth for the read-only
   `supabase-hackathon` MCP. `zekihxh` still owns migrations and must not apply
   them before that inventory. No `vision/cctv_monitor.py`; phone capture stays
   on the authenticated vision path.
5. TLS phone setup, real cameras, live Grok, recorded clips, and peer
   integration remain unverified. Do not treat fixture tests as that evidence.
6. Update the draft PR and board with exact commit/checks; only Alan merges.

## Latest coordination decisions and open requests

The last dedicated Codex board agent used a separate clone at
/private/tmp/pavia-vision-board-coordination. **It and the coding subagents
have stopped.** A new Cursor parent may reuse that clone only after checking
its state; otherwise create a separate clone with a separate board cursor.
No watcher automatically transfers between chats. The main checkout still
must run tools/board unread at commit/task boundaries.

Supabase: Alan explicitly selected existing **AlanDD13's Project**, ref
`buojgmoumyzqxmerckjm`, eu-west-1. Backend owns schema/migrations and must
inventory/preserve unrelated existing data. The request is on the board;
zekihxh acknowledged it. Backend's local private Postgres schema smoke test
passed, but no shared database changes or access grants are verified.
The latest request (#6633477304) asks for individual read-only catalog access
OR a schema/table-only inventory, then a migration owner and separate scoped
runtime identity. Frontend/mapping/robot use backend APIs and do not need
admin keys or direct DB access. Agents do not share Alan's credentials.

.cursor/mcp.json provides project-scoped **read-only** Supabase MCP. Sign in
with your own account in Cursor Settings > Tools & MCP. OAuth is pending;
the config is not proof of access. After connecting, use read-only catalog
metadata to answer backend's inventory request. Backend still handles rollout.

Frontend (#6633501068) and backend (#6633540811) accept evidence revision plus
entityVersion/producerBootId, with backend-owned revisions/cursor. They require
fixtures for same-epoch updates, revocation, new-boot reset, delayed old-boot
delivery, sequence gaps and complete scoped resnapshot. Alert retries must
retain event IDs; a new producer boot must not restore dispatchability.

Mapping (#6633509247) reviewed e3976c1 and confirmed receivedAt/invalidReason,
metric units and stationary gate. Remaining mismatches are listed above.
Its P5-to-P3 body should match MappingEvidence; not yet on main.

Backend/frontend propose same-origin authenticated snapshot/SSE with an opaque
durable cursor. Initial after query; Last-Event-ID takes precedence on reconnect;
durable SSE id; snapshot_required control event without id on unavailable replay.
Session/auth mechanism remains a lead/owner decision. Vision's diagnostic stream
is non-durable and uses snapshot/resync_required; do not confuse the two streams.

Robot (#6633484321) and backend agree the simulator can serialize hold/stop vs
pickup authorization, binding command/mission/object/evidence revision/
confirmation/expiry. Hold winning prevents PICKING; duplicate authorization
does not act twice; explicit stop has priority; lost acknowledgements require
status reconciliation and retain the rover slot. Endpoint/persistence/deadline/
rearm/camera decisions and fixtures still require shared contracts. Simulator
agreement is not physical hardware validation.

Last board read also found frontend's human-requested manual-control simulator
draft (#6633587801), acknowledged by robot/backend (#6633595738/#6633600156).
It includes per-rover joystick/keyboard and overhead click-to-move. This is future
contract work: exclusive expiring control leases, sequenced velocity intents,
blur/input-loss release, bridge watchdog/stop priority and no concurrent manual
and autonomous motion. Overhead pixels must pass mapping calibration and route
validation. Hardware/capability support, units, deadlines and endpoints remain
unagreed; this request does not authorize adding live controls to vision.

## Primary sources already consulted

- https://docs.x.ai/developers/model-capabilities/images/understanding
- https://docs.x.ai/developers/model-capabilities/text/structured-outputs
- https://docs.x.ai/developers/rest-api-reference/inference/responses
- https://docs.ultralytics.com/models/yoloe/
- https://pavia.grokbot.it/ and https://pavia.grokbot.it/tracks

Grok must be substantive. Earlier research recorded demo/jury deadline Nov 5,
16:00 Europe/Rome, measurable baseline/data/limitations in README. Recheck live
event rules before final submission; custom rover acceptance is unconfirmed.
