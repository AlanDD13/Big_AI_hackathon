# LangGraph recovery slice

Status: executable **simulator only**, draft adapter interface. No physical enable
flag or HTTP robot adapter exists in this slice. It changes no running service.

From the repository root:

```sh
uv run --extra recovery --extra test python -m integration.recovery
uv run --extra recovery --extra test pytest -q integration/recovery/tests
```

The default CLI uses deterministic tool proposals and labelled sensor/evidence
fixtures; it needs no keys, database, cameras, network or robot. Expected result:
`COMPLETE` after approach, align, pickup, transport, place. This tests control flow,
not vision accuracy, grasping, navigation, or physical dynamics.

`python -m integration.recovery --grok` explicitly uses `OPENROUTER_API_KEY`,
OpenRouter `x-ai/grok-4.7`, low reasoning and one structured tool proposal at a
time. It still controls only the fixture. No key is loaded from another project.
Provider tests use `httpx.MockTransport`; a live Grok policy run is not part of
the offline acceptance evidence. The policy passes structured observations, not
camera images: vision/mapping must first produce the identity association.

`targets.read_target(session_configured_httpx_client, object_id)` reads the
existing authenticated backend `/api/snapshot` projection. It preserves the
stored classification and backend `dispatchEligible` decision. It does not
reclassify, write to the DB, sign in automatically or grant mission authority.
The CLI uses `fixture_mission()` instead; no live DB read is claimed.

The actual `StateGraph` observes, chooses a tool, validates and acts, then reads
again. Before acting it refreshes observations after the model call. Stable
target IDs/revisions, explicit identity evidence, stationary inanimate targets,
fresh frames/telemetry, phase prerequisites and backend eligibility are required.
Grok supplies action/object/destination only; no speeds, coordinates or arm
positions. Safety and bounded execution remain adapter responsibilities.

Pickup and placement are separate evidence gates bound to the command ID.
ACK is not success. Frame replay, camera-session changes, lost identity, living
obstacles, stale data, tool errors and budget exhaustion stop the loop. Timeout
after sending retains `UNKNOWN`, even if stopping later succeeds; it is never
automatically resent. No graph retries or durable checkpoint replay are enabled.

Each simulator accepts one mission in its lifetime through an atomic `begin`.
That also prevents concurrent wrappers from sharing its mutable state. A new
process/simulator forgets this reservation: physical integration requires the
backend's durable mission/command reservation and explicit reconciliation.
Events returned by the graph are local diagnostics; backend persistence/SSE and
dashboard notifications are not connected by this slice.

See [physical handoff](../../docs/LANGGRAPH_PHYSICAL_HANDOFF.md) and the
[draft adapter contract](../../contracts/recovery-tools-v1.md).
