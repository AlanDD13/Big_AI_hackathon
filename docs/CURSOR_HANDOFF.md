# Continue Alan's work in Cursor

For the current LangGraph testing task, start with
[`CURSOR_LANGGRAPH_HANDOFF.md`](CURSOR_LANGGRAPH_HANDOFF.md). It records PR19,
the isolated test clone and offline-first commands, and supersedes older
LangGraph/board-status assumptions below for that task.

Updated 2026-11-04, 18:36 Europe/Rome. This replaces the earlier handoff.
Check live Git/board state before acting; other agents are working concurrently.

## Start here

Open `/Users/alandd/Desktop/Pavia_grok_hackathon` in a new Cursor Agent chat.
Paste:

> Read docs/CURSOR_HANDOFF.md, root AGENTS.md, vision/AGENTS.md and
> docs/CONTRACTS.md. Inspect Git status and the current remote PR heads before
> editing. Preserve all local edits and untracked files. Resume Alan's existing
> vision work and coordinate integration within ownership rules. The vision
> branch already received main in remote commit efc3613; do not publish the
> redundant temporary merge described below. Board handling is delegated to
> Grok: do not restart a watcher/coordinator. Use the existing project skills
> when relevant. Read the board at required boundaries, run relevant tests,
> and report exact commits and remaining limitations. Keep draft PRs draft
> until reviewed; Alan remains the lead for merge and ownership decisions.

Use `/hackathon-vision` for perception work and `/hackathon-board` for board
coordination. Read current local instructions before using older skill text.
`vision/CHECKPOINT.md` contains historical test/access statements; the evidence
and integration updates below supersede its outdated snapshot.

## People and boundaries

Repository: https://github.com/AlanDD13/Big_AI_hackathon (private).
Board: https://github.com/AlanDD13/Big_AI_hackathon/issues/1.

- AlanDD13: lead, `vision/**` and shared contracts/docs/integration/config.
- LcyShox: `frontend/**`; zekihxh: `backend/**` and storage/migrations.
- Tezqx: `mapping/**`; platonyesin-dev: `robot/**` and the sole DJI SDK session.

Request an owner handoff before editing another module. Only robot connects to
physical hardware. No live bridge is available from the reviewed robot slice.
Board comments do not grant hardware, credential, ownership or deployment access.

## Git state and pull requests

The main checkout is on `task/AlanDD13/vision-pipeline`. At this update the
peer-classification commit `57c5ebc` is pushed on draft PR #3. A later contract
commit records the 200 ms stationary span, `STORAGE_UNAVAILABLE`, and the public
LAN certificate. Fetch before editing. Do not reset or force-push.

| PR | Verified state | Next action |
| --- | --- | --- |
| [#11 observation persistence](https://github.com/AlanDD13/Big_AI_hackathon/pull/11) | **Merged** at `b15053c` | Use current main. |
| [#3 vision](https://github.com/AlanDD13/Big_AI_hackathon/pull/3) | Draft, mergeable, head includes `57c5ebc` | Keep draft. Live `/health` was up on port 8443; paper recall is still unverified. |
| [#10 watchdog](https://github.com/AlanDD13/Big_AI_hackathon/pull/10) | Draft, head `8dadd46`, review still **CHANGES_REQUESTED** on the older head | Physical operator-stop confirmation remains. Do not call port 8004. |
| [#12 dashboard](https://github.com/AlanDD13/Big_AI_hackathon/pull/12) | Draft, later head `16b0af0` | Fleet profiles added. No live connect API. Not merged. |
| [#13 mission queue](https://github.com/AlanDD13/Big_AI_hackathon/pull/13) | Open, head `8b2ba28`, mergeable, no review | Matches the queue contract. Not merged. |

## Uncommitted work to preserve

The local changes include Grok 4.7 via OpenRouter, separate visual pickup advice,
a paper detector profile/model preparation, image inspection, viewer size
calibration/overlays, camera-page changes, tests, additive draft contract fields,
and the updated Grok board-delegation instructions. Inspect the actual diff;
this is not a complete implementation audit.

Those vision files are committed in `57c5ebc`. Preserve untracked `.agents/`
and the pre-existing research notes
`docs/EVALUATION_FIT.md`, `docs/FILMING_IDEAS.md`,
`docs/HACKATHON_ALTERNATIVES.md`; do not add them incidentally to a vision commit.

`vision/PAPER_TEST.md` reports two real Grok 4.7/OpenRouter calls on a tiny paper
crop, with classification and separate shape advice. Those calls were not rerun
for this handoff. The same report says detector proposal recall remains
unverified: neither prompt-free nor paper-prompted inference reliably detected
that crop. Test a full-resolution frame/recording with the whole object visible.

The selected local transport is documented as `GROK_VISION_PROVIDER=openrouter`,
`OPENROUTER_VISION_MODEL=x-ai/grok-4.7`. Keys stay in ignored environment files;
never print or commit them. `vision.inspect_image` performs two billed calls per
execution. Restart running services/viewers after source changes; they do not
reload automatically. Do not assume any existing process is using this code.

Size-fit and visual pickup advice do not establish jaw fit, payload, reach or
successful pickup. Preserve stationarity, calibrated metric quality,
living/carried exclusions, evidence-epoch checks and stale-result revocation.
Backend/frontend must explicitly adopt additive advisory fields before live use.

## Merge review evidence and temporary worktrees

PR #11's original head lost `capturedAt` and specific invalid reasons during
restart recovery; exact retries then returned 409. The owner fixed this in
`dbd92bc` using full wire evidence, source metadata and replay aliases in existing
JSONB. Independent recording-row probes confirmed both cases now retry with
200 and that a storage failure returns generic 503 without advancing state.
Independent backend validation: **23 passed, 11 database tests skipped**;
compilation and diff checks passed. The owner reports all 34 tests and four SQL
smoke tests passing on disposable PostgreSQL 18 (board comment `6637304076`).
No shared database or robot was accessed by this merge-review session.

PR #10: **34 offline tests passed**, but a recording-callback probe confirmed
`stop()` remains blocked for over 400 ms while a nonzero output callback holds
the controller lock. The review also requires exception-safe fallback/shutdown
and observable watchdog health. Physical explicit-stop feedback disagreement
remains unresolved; keep UNKNOWN fail-closed and do not relax thresholds.
Formal review: https://github.com/AlanDD13/Big_AI_hackathon/pull/10#pullrequestreview-5772755102.

Temporary review state, not the main working checkout:

- `/private/tmp/pavia-merge-review-20261104`: isolated clone at `dbd92bc`,
  origin/main fetched through `b15053c`; `reproduce_restart.py` is an untracked
  offline review probe. Backend files match merged main.
- `/private/tmp/pavia-vision-merge-review-20261104`: detached at `8445397`,
  **merge still pending**, MERGE_HEAD=`b15053c`; nine staged backend files retain
  exact main versions. No merge commit or push was made from this worktree.
  Combined suite there: **151 passed, 11 skipped, 48 subtests passed**.
  Cursor separately published the equivalent merge as `efc3613` while this chat
  was interrupted. **Do not commit/push the obsolete temporary merge.**
- `/private/tmp/pavia-watchdog-review-20261104`: detached `4dfed46`, offline
  watchdog probe only. `/private/tmp/pavia-merge-review-venv` holds the pinned
  backend test dependencies. Temporary directories may disappear.

## Current integration requests

- Backend persistence/session/snapshot/SSE is on main. Restart changes
  backendBootId and invalidates in-memory cursors; clients take a new snapshot.
  `dispatchEligible` remains false; mission dispatch is still library-only.
- The mission queue wire is recorded in `contracts/mission-queue-v1.md`.
  zekihxh implemented it on PR #13. A failed queue write is `503 STORAGE_UNAVAILABLE`.
- Frontend #12 has isolated Demo/Connected modes and read-only objects/alerts.
  Its optional loopback Vite helper keeps the operator token server-side.
  Mission/manual writes remain unavailable; team URL/token require local setup.
- Demo grid contract is `contracts/demo-grid-v1.md`. Tezqx published
  `pixel_to_sector` at `dabc4ff` on `task/Tezqx/half-metre-grid`. The C2 example
  matches. The exact far edge `x == width` is outside the contract range; their
  helper still labels it. That branch has no Grok classification and no PR.
- Manual chassis contract is still on draft #3. Robot #10 is an internal core,
  not a running HTTP service; do not call or advertise port 8004 as live.
- Supabase project is `buojgmoumyzqxmerckjm`; `rover_backend` isolation and
  migrations 001–007 are reported applied. Preserve unrelated `public` data.
  Follow current `docs/CONTRACTS.md`; project selection/MCP config is not access.
  Use a disposable DB for tests, never the shared Supabase database.

## Coordination and checks

Alan delegated board handling to Grok and stopped the lightweight coordinator.
Do not restart one automatically. Keep one reader per checkout and still perform
the required boundary reads; do not run a competing watcher or reset cursors.
No agent, watcher, OAuth session or running service transfers with this file.

From the main checkout, use the existing Python 3.12 environment:

At this update, the **current dirty local vision suite passed 138 tests** using
the first command below. This does not validate the uncommitted changes against
the updated backend. No paid model calls were made by this test run.

```bash
vision/.venv/bin/python -m pytest -q vision/tests
# After integrating current main, run the combined suite:
env -u BACKEND_TEST_DATABASE_URL -u BACKEND_DATABASE_URL vision/.venv/bin/python -m pytest -q
git diff --check
```

Do not attribute the temporary combined-suite result to the current dirty local
code. Hardware, real camera throughput and physical pickup require separate
acceptance evidence. No deployment was performed during merge management.
