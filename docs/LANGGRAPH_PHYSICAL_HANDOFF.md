# LangGraph build and robot acceptance handoff

Alan owns `integration/recovery/**` and shared contract. Platon owns `robot/**`
implementation and supervised hardware tests. Zeki owns backend durable
reservation, adapter wiring and event persistence. Tezqx owns calibrated
localization/routes and association context. Review board issue #1 before work.

## Offline build

Actual LangGraph `StateGraph`, structured OpenRouter Grok 4.7 policy, backend
snapshot target reader, strict tool/phase validation, fresh observation after
model latency, atomic single-mission fixture reservation, action budget, no
ambiguous-action retry, command-bound pickup and placement evidence.
No physical bridge adapter or running backend integration is included.

From root:

```sh
uv run --extra recovery --extra test python -m integration.recovery
uv run --extra recovery --extra test pytest -q integration/recovery/tests
python -m compileall -q integration/recovery
```

Fixture flow completes in five acknowledged actions and eleven observations.
Tests cover wrong identity/revision, living/moving targets, stale/future data,
frame replay, bad tools, model latency, lost ACK, unknown stop, action budget,
failed grasp/place evidence and concurrent/replayed missions. Policy tests use
mock HTTP: no fresh Grok spend, shared DB or hardware action is claimed.
Verified result: 45 recovery tests; with shared board/contract checks,
65 tests and 7 subtests passed. Python compilation and diff whitespace checks
passed. The default CLI returned COMPLETE with five simulated actions.

PR #17 exact `76d70b7992ba47bebe475ff9fc3799a620bccc8e` independently passed
61 offline robot tests plus a silence-watchdog probe without further HTTP calls.
The four reported defects are addressed. This is separate PR code; review is
neither merge nor hardware acceptance.

## Platon: requested next milestone

1. With your human supervising a clear area and reachable local emergency stop,
   record head/firmware/config and startup/subscription/shutdown behavior. Keep
   one SDK owner; coordinate replacing the camera-only process first.
2. At a conservative tested chassis speed, measure silence-to-zero cutoff
   <=300ms without polling. Prove UNKNOWN persists until three fresh stationary
   samples; reject early rearm. Explicit rearm must not resume motion. Verify
   release/pickup arbitration blocks pickup during pending stop.
3. Implement/test a bounded pickup macro under your paths, using a known light
   object/profile and measured clearance/size. Reported usable jaw span is about
   90mm; conservative demo payload200g is not measured max lift. Floor clearance
   does not prove flat paper is graspable. Failed/unverified pickup must not
   trigger transport, and command ACK must not count as pickup evidence.
4. Provide independent pickup evidence; then test bounded transport, destination
   verification, place/release and independent placement evidence. Keep untested
   manipulation SIMULATED_ONLY. Record failures as well as passes.
5. Return branch/head, route/capability proposal, status/error fields, measured
   timings, known object/profile limits and logs/video. Agree
   `contracts/recovery-tools-v1.md` with P3 before implementing physical routes.

A physical adapter cannot derive READY/identity confirmation from Grok guesses,
repeated frames, uncalibrated demo-grid sectors or cached labels. Calibrated
route/pose and fresh target association remain necessary. Timeout/restart/loss
retains the durable rover slot until reconciled.

## Backend and perception follow-through

P3: trusted mission/target reader; durable command identity/reservation; pickup
authority/revision revalidation; unknown outcome reconciliation; graph events
through existing dashboard stream. `propose_collect` still only queues a mission.

Alan/Tezqx: connect rover detections to the same DB object, not merely the same
class, using fresh frame/geometry/pose evidence; reuse stored classification
after association. CLI uses labelled observations; it does not implement visual
association, calibrate maps or connect live cameras.
