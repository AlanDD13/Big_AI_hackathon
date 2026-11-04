# Board resolution checkpoint — 2026-11-04

This is a snapshot, not a substitute for reading live board issue #1. Read the
board before tasks, subagent creation, commits and integration boundaries.

## Addressed

- TLS test failure reproduced on main `7fb18c6`; fix published as
  [PR21](https://github.com/AlanDD13/Big_AI_hackathon/pull/21), head `60549a5`.
  Only `vision/tests/test_cctv_monitor.py` changes; certificate verification is
  asserted. Vision plus root tests: **163 passed, 7 subtests passed**. Not merged.
- Backend recovery review accepted. `propose_collect` remains queue-only;
  `dispatchEligible` stays false. No physical adapter/routes or LangGraph-step
  SSE have been connected. [PR19](https://github.com/AlanDD13/Big_AI_hackathon/pull/19)
  remains draft; GitHub CLI reports MERGEABLE at `5730da7`.
- [PR18](https://github.com/AlanDD13/Big_AI_hackathon/pull/18) is merged as
  `7fb18c6`; its dashboard fix does not depend on weakening vision TLS.

## Active owner work, not completed by triage

- Alan's `task/AlanDD13/live-demo-grid` is already integrating Tezqx's mapping
  lineage, iPad/PC overlays and a proposed A-top/bottom-centre/bbox-centre demo
  convention. Main's published contract still uses A-bottom/bottom-left/C2.
  Publish paired contract/fixtures/tests before human merge. Do not duplicate
  that active task or silently mix conventions. Both remain uncalibrated demo
  labels; backend observation rejection and physical navigation gates stand.
- Latest adaptive-geometry request to Tezqx is
  [comment6641663439](https://github.com/AlanDD13/Big_AI_hackathon/issues/1#issuecomment-6641663439):
  separate DEMO_IMAGE_GRID from a measured perspective FLOOR_GRID; request pure
  projection/overlay helpers and geometry invalidation tests. Tezqx's feasibility,
  helper signatures and required floor measurements remain pending. An arbitrary
  floor mask is not metric calibration; retain demo fallback until references
  and geometry association are valid. This is a proposal, not a frozen API.
- PC viewer read access must be explicitly documented/tested by vision
  integration. Existing PEER_TOKEN does not authorize master-only live_view
  routes; a mapping merge alone does not display a grid. Do not share full env
  files or master/model credentials. The changed iPad view needs a new floor
  outline; old saved geometry is not validated by scaling it.
- Platon: PR17 `76d70b7` remains open/unmerged. Four fixes reviewed offline,
  61 tests plus independent watchdog probe passed. Physical acceptance is still
  outstanding; preserve the camera-only server, one SDK owner, confirmed stop,
  guarded rearm and SIMULATED_ONLY manipulation until tested.

## Cursor continuation

Use [CURSOR_LANGGRAPH_HANDOFF.md](CURSOR_LANGGRAPH_HANDOFF.md) for offline graph
tests. Review the live board and current PR heads before extending it. No paid
Grok calls, physical operations, shared DB writes or merges were performed by
this board-resolution task. Existing dirty checkout work was preserved.
