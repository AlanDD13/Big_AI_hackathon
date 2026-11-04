# Dashboard — LcyShox

Own `frontend/**`: UI, module dependencies/lockfile, tests and API mocks.
Build map rendering, video, objects, mission status, text requests, notifications,
cancel and stop. Calculating map coordinates/routes belongs to `mapping/`.

First slice: show a fixture map, one object and one mission using local API mocks.
Done: selecting the object emits the agreed mission request, status changes are
visible, and unavailable backend/stop errors are shown honestly.

Read `AGENTS.md`, `docs/ONBOARDING.md` and `docs/CONTRACTS.md` before starting.
The current local draft and its commands are documented below.

## Local dashboard draft

This is an explicitly requested, frontend-only exploration on
`task/LcyShox/dashboard-prototype`. The data in
`src/domain.ts` is a local view model, not a proposed or frozen wire schema.
Demo mode uses local fixtures. Connected mode reads the backend snapshot and
SSE stream for objects and alerts, with a read-only object inspector. It does
not connect to a camera, Grok, mapping service or physical rover directly.
Live manual control remains unimplemented. No operator sign-in screen is added.
See [BACKEND_INTEGRATION.md](BACKEND_INTEGRATION.md) for local connection setup.
Connected → Fleet adds browser-local EP Core serial-based setup profiles. See
[FLEET_CONNECTIONS.md](FLEET_CONNECTIONS.md) for the verified STA/UDP topology
and the pending robot bridge API integration. Saving a setup never connects or
moves a robot.

Requires Node.js 24+ and npm. From `frontend/`:

```bash
npm ci
npm run dev
npm test
npm run build
```

On Windows PowerShell, use `npm.cmd` if script execution policy blocks `npm`.
Vite prints the local URL; its default is http://127.0.0.1:5173/.

### Try the draft

1. Open Overview and select a mapped object on the fixture map or in the object list.
2. Choose **New task**, a recovery object, and a destination; queue recovery.
3. Use **Next stage** or **Run simulation** (three seconds per stage).
4. Follow the status through navigation, pickup, delivery and return.
5. Try **Cancel**, **Stop rover**, and explicit **Re-arm**. Stopped missions
   remain stopped after re-arm; create a new mission to move again.
6. Turn off the mock backend in **Simulation settings**. New requests fail,
   progress pauses, and cancel/stop errors never claim confirmed execution.
7. Explore Missions, Fleet and Activity. Search objects, zoom the map and toggle
   the fixture route. Unknown-position objects cannot be dispatched.

Reload or **Reset simulation** clears this session. There is one fixture rover,
one initially completed sample mission, and at most one mission progressing at
a time. Other queued recoveries wait. Battery, route and pickup data are fixtures;
route drawing does not calculate navigation. The floor is an illustrative fixture
with approximate scale, not a calibrated physical map.

Scanning, inspection, tool delivery, natural-language input and cooperative fleets
are marked future previews. The text field does not parse or execute requests.
Grok interpretation and policy validation belong to the backend when integrated.

Before real integration, agree schemas and fixtures with Alan and the backend
owner, then replace the simulator with the agreed REST/SSE adapter. The draft
does not simulate actual HTTP, idempotency, SSE or reconnection behavior. A real
stop/cancel must wait for bridge confirmation; no local state is physical truth.

Source: `src/App.tsx` (views and controls), `src/WarehouseMap.tsx` (fixture map),
`src/domain.ts` (simulator and fixture models), `src/styles.css` (responsive UI).
Tests cover the single-rover queue, uncertain objects, duplicate reservations,
stop/re-arm, offline failures, and cancellation.

## Manual-control extension

The Manual view opens by default for the user-requested EP Core control draft.
See [MANUAL_CONTROL.md](MANUAL_CONTROL.md) for the official DJI research,
input conventions, limitations and future P3/P4/P5 boundary.

- First person: select an online fixture droid, Take control, then hold/drag the
  joystick or hold Space with WASD and Q/E. Release zeros local velocity.
- Overhead command: select a droid, click clear floor or enter east/north metres,
  Preview destination, then Take control and Move here. This uses a fixture
  planner only. Production camera calibration/routing belongs to mapping.
- Stationary-only arm/gripper controls are simulated in millimetres/Open/Closed.
  They are grouped inside the Joystick control widget in First person mode.
- An empty Main camera widget appears in Manual and Overview. It has no image,
  stream URL or simulated camera content. The existing first-person floor view
  remains a separate labelled simulation.
- Authority and speed share one panel. Simulation diagnostics and SDK input
  previews are collapsed under Simulation tools; coordinate entry is expandable.
- Switching droid/mode, leaving Manual, opening a modal, losing window focus,
  stale telemetry, offline state and stop release authority. Re-arm does not
  resume control. Stop confirmation is never claimed for an unavailable backend.
- Three illustrative droids are not hardware discovery or cooperative routing;
  the recovery workflow remains its separate single-rover fixture.

Manual source: `src/ManualControl.tsx`, `src/manual.ts`, `src/manual.css`.
Tests additionally cover caps/axis transforms, expiry/authority gates, screen
coordinates, obstacle-aware fixture routing and route completion.
