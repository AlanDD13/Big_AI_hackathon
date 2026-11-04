# Backend and Grok — zekihxh

Own `backend/**`: API, mission policy/state, event stream, Grok adapter, storage
migrations, consumer clients, module dependencies, tests and local fakes.
Consume mapped observations/plans and dispatch missions to the bridge.
Coordinate shared wire-contract changes with AlanDD13.

First slice: consume a mapped observation, accept a request and advance a mission
using fake mapping/robot clients. Done: retries do not duplicate work, only one
mission is active, and UI status reflects failed/unknown execution outcomes.

Read `AGENTS.md`, `docs/ONBOARDING.md` and `docs/CONTRACTS.md` before starting.
Add actual install/run/test commands here when implementing the first slice.
