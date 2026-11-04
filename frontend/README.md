# Dashboard — LcyShox

Own `frontend/**`: UI, module dependencies/lockfile, tests and API mocks.
Build map rendering, video, objects, mission status, text requests, notifications,
cancel and stop. Calculating map coordinates/routes belongs to `mapping/`.

First slice: show a fixture map, one object and one mission using local API mocks.
Done: selecting the object emits the agreed mission request, status changes are
visible, and unavailable backend/stop errors are shown honestly.

Read `AGENTS.md`, `docs/ONBOARDING.md` and `docs/CONTRACTS.md` before starting.
Add actual install/run/test commands here when implementing the first slice.
