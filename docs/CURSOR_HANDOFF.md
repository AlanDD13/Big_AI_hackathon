# Continue Alan's work in Cursor

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

The main checkout is on `task/AlanDD13/vision-pipeline`. At this update:

- Local HEAD: `844539753aedaa10a0f20ef45ef8cb74dc4a7a92`.
- Remote vision head: `efc3613ce3341f25e5e092fc6d5f768bddaea069`.
  The checkout reports **behind 18 commits**, with substantial unstaged work.
- Main includes merge `b15053c11885ae600e2c4c4bd63f7a090685a394`.

Do not reset, force-push, discard, blanket-stage, or blindly pull over the dirty
checkout. Fetch and inspect differences first. Preserve the local work when
bringing it onto the updated vision branch; it is not included in PR #3 yet.

| PR | Verified state | Next action |
| --- | --- | --- |
| [#11 observation persistence](https://github.com/AlanDD13/Big_AI_hackathon/pull/11) | **Merged** at `b15053c`, reviewed head `dbd92bcb6bf4239959d28c26dc3fe42bbdb3e408` | Use current main; do not recreate this slice or reapply migrations. |
| [#3 vision](https://github.com/AlanDD13/Big_AI_hackathon/pull/3) | Draft, head `efc3613`, mergeable when checked | Cursor already merged main into this branch, preserving the exact merged backend. Continue local vision work and refresh validation/PR description before readiness review. |
| [#10 watchdog](https://github.com/AlanDD13/Big_AI_hackathon/pull/10) | Draft, head `4dfed46b63bbdeeb6059fd8ede782f3e4ccb3b7e`, formal **REQUEST_CHANGES** | Robot owner must address output locking, watchdog lifecycle/health and physical acceptance. |
| [#12 dashboard](https://github.com/AlanDD13/Big_AI_hackathon/pull/12) | New draft, head `2aa3a5779ea2770f69ce61972fa9ff9bacc9399f` | Awaiting review; frontend owner reports 17 tests, build and local API/browser QA passing. Not independently reviewed here. |

## Uncommitted work to preserve

The local changes include Grok 4.7 via OpenRouter, separate visual pickup advice,
a paper detector profile/model preparation, image inspection, viewer size
calibration/overlays, camera-page changes, tests, additive draft contract fields,
and the updated Grok board-delegation instructions. Inspect the actual diff;
this is not a complete implementation audit.

Key untracked implementation files include `vision/PAPER_TEST.md`,
`vision/inspect_image.py`, `vision/prepare_paper_model.py`, and
`vision/tests/test_inspect_image.py`, `test_openrouter_classification.py`,
`test_paper_profile.py` (the latter two are also under `vision/tests/`).
Preserve untracked `.agents/` and the pre-existing research notes
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
- zekihxh started `task/zekihxh/mission-http` from `b15053c`, preparing storage
  while the public wire awaits Alan's decision. Board request `6637338132`
  asks for canonical POST /api/missions request/response, replay status, error
  codes and queued-mission snapshot/SSE semantics. Frontend reply `6637374618`
  requests full `mission.updated` bodies. No canonical decision was made here.
- Frontend #12 has isolated Demo/Connected modes and read-only objects/alerts.
  Its optional loopback Vite helper keeps the operator token server-side.
  Mission/manual writes remain unavailable; team URL/token require local setup.
- Tezqx requested an uncalibrated 4x4 demo grid over 2x2 metres, 0.5 m cells
  (board `6637306081`, branch reported as `task/Tezqx/half-metre-grid`). A1 is
  bottom-left; pixel (855,423) at 1920x1080 maps to C2. Verify its published
  branch before using it. Demo labels are not MappingEvidence and must never
  pass metric stationarity/dispatch gates. No vision change was made for it here.
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
