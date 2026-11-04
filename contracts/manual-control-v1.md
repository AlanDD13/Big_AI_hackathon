# Manual control boundary — Temporary Live Manual Chassis Slice (`MANUAL_CHASSIS_SLICE_1`)

Status: **`MANUAL_CHASSIS_SLICE_1` open** on `task/AlanDD13/vision-pipeline` in
response to `@LcyShox`'s human request (`#6635201575`). This opens **chassis-only
precision driving** (`MANUAL_CHASSIS_SLICE_1`) while keeping **overhead
click-to-move** and **arm/gripper manipulation** `SIMULATED_ONLY` until P5/P4
capability verification. Vision does not gain live driving controls.

Owners:
- `LcyShox` (`frontend/**`): dashboard hold-to-drive joystick UI, release on
  `pointerup`/`blur`/`visibilitychange`, and explicit stop/re-arm controls.
- `zekihxh` (`backend/**`): operator session validation (`session` cookie),
  mission/lease mutual exclusion, and forwarding `/api/manual/*` to P4.
- `platonyesin-dev` (`robot/**`): **sole DJI SDK (`rmepcore`) owner**, bridge
  watchdog (`<= 300 ms`), priority stop, and chassis `drive_speed` execution. No
  second DJI SDK connection may be opened from `frontend/**` or `backend/**`.
- `Tezqx` (`mapping/**`): future pixel-to-map route planning for overhead click
  (stays `SIMULATED_ONLY` in this slice).

### Single Active DJI SDK Host & LAN Bridge Rule (`#6635211588`, `#6635248353`)

1. **One DJI SDK host at a time on the LAN (STA):** Only ONE machine on the
   router/LAN may open a DJI SDK (`rmepcore`) session at any time. While
   `@platonyesin-dev` runs step 3 connection-only diagnostics (`#6635211588`),
   no other machine may open a DJI SDK session.
2. **Sole P4 bridge ownership:** `@platonyesin-dev` (`robot/**`) remains sole
   owner of the P4 bridge implementation (`MANUAL_CHASSIS_SLICE_1` +
   `contracts/robot-simulator-v1.md`).
3. **Cross-machine LAN HTTP bridge (`ROBOT_BRIDGE_URL`):** Because `@LcyShox`'s
   Windows PC and `@platonyesin-dev`'s Mac are on the **same router/LAN (STA)**,
   `@LcyShox`'s Windows PC does **not** need to install the DJI SDK locally or
   open a second SDK session. Once `@platonyesin-dev` runs the P4 bridge bound
   to its LAN interface (`http://<p4-lan-ip>:8004`, configured via
   `ROBOT_BRIDGE_URL`), P3 (`backend/`) or `@LcyShox`'s local Vite dev proxy on
   the Windows PC connects over HTTP on the LAN (`ROBOT_BRIDGE_URL=http://<p4-lan-ip>:8004`
   with `PEER_TOKEN` in server/proxy env, never in browser code). If the team
   later chooses to run P4's `robot/` bridge process directly on the Windows
   host after P4 disconnects its Mac SDK session, it still runs P4's `robot/`
   bridge process and never connects to DJI from `frontend/**`.

Platform confirmed by `@LcyShox` (`#6634007814`): RoboMaster EP Core (`rmepcore`).
This contract defines the wire interface and safety gates; it does not claim
hardware validation before physical bench testing.

## 1. Authority & Mutual Exclusion

- One active authenticated manual lease per rover (`roverId`), identified by
  `(leaseId, leaseGeneration)`.
- A manual lease is **mutually exclusive** with any autonomous mission/command
  in `NAVIGATING`, `ALIGNING`, `PICKING`, `HELD`, or `UNKNOWN`, and with
  `STOPPED` (while stop is latched) or stale/unknown rover telemetry:
  - Active autonomous command -> `409 AUTONOMOUS_ACTIVE`
  - Lease already held by another generation/session -> `409 LEASE_HELD`
  - Rover latched in `STOPPED` -> `409 STOP_LOCKED`
  - Rover link/telemetry stale or unavailable -> `503 TELEMETRY_STALE`
- Autonomous pickup/navigation commands (`POST /commands/*`) are likewise
  rejected (`409 MANUAL_LEASE_ACTIVE`) while a manual lease is active.
- `rearm` clears the `STOPPED` latch back to `IDLE` (`motion: false`) and **never**
  resumes motion or restores a prior lease. A new `POST /api/manual/lease` is
  required after `rearm`.

## 2. Transport, Auth & Routes (`P1 -> P3 -> P4` and Bench Dev Proxy)

### Browser -> Backend (`P1 -> P3`)
Authenticated via the same-origin HttpOnly session cookie established at
`POST /api/session` (per `contracts/backend-stream-v1.md`). Expired or missing
session returns `401 Unauthorized`.

1. **`POST /api/manual/lease`**
   - Request:
     ```json
     {
       "roverId": "rover-1",
       "requestedTtlMs": 2000
     }
     ```
   - Response `200 OK`:
     ```json
     {
       "leaseId": "lease-1",
       "roverId": "rover-1",
       "leaseGeneration": 1,
       "issuedAt": 1793797200.0,
       "expiresAt": 1793797202.0,
       "bridgeBootId": "cccccccccccccccccccccccccccccccc",
       "mode": "MANUAL_CHASSIS_SLICE_1",
       "overheadNavigationMode": "SIMULATED_ONLY",
       "manipulationMode": "SIMULATED_ONLY",
       "caps": {
         "maxAbsXMps": 0.35,
         "maxAbsYMps": 0.35,
         "maxAbsZDegps": 45.0,
         "watchdogTimeoutMs": 300,
         "maxIntentTtlMs": 500
       }
     }
     ```

2. **`POST /api/manual/drive`**
   - Request (hold-to-drive tick):
     ```json
     {
       "roverId": "rover-1",
       "leaseId": "lease-1",
       "leaseGeneration": 1,
       "sequence": 1,
       "issuedAt": 1793797200.05,
       "expiresAt": 1793797200.25,
       "xMps": 0.2,
       "yMps": 0.0,
       "zDegps": 0.0,
       "frame": "BODY"
     }
     ```
   - Response `200 OK`:
     ```json
     {
       "roverId": "rover-1",
       "bridgeBootId": "cccccccccccccccccccccccccccccccc",
       "stage": "MANUAL_DRIVING",
       "leaseId": "lease-1",
       "leaseGeneration": 1,
       "lastSequence": 1,
       "appliedVelocity": {
         "xMps": 0.2,
         "yMps": 0.0,
         "zDegps": 0.0
       },
       "telemetryFresh": true,
       "poseReceivedAt": 1793797200.04,
       "watchdogTimeoutMs": 300
     }
     ```

3. **`POST /api/manual/release`**
   - Request:
     ```json
     {
       "roverId": "rover-1",
       "leaseId": "lease-1",
       "leaseGeneration": 1,
       "reason": "user_release"
     }
     ```
     (`reason` is `"user_release"`, `"blur"`, or `"tab_hidden"`.)
   - Immediately commands `drive_speed(x=0.0, y=0.0, z=0.0, timeout=0.25)` on P4,
     invalidates the lease, and returns `200 OK`:
     ```json
     {
       "roverId": "rover-1",
       "released": true,
       "stage": "IDLE",
       "zeroVelocityApplied": true
     }
     ```

4. **`POST /api/manual/stop`**
   - Request:
     ```json
     {
       "roverId": "rover-1",
       "reason": "operator_stop"
     }
     ```
   - Priority stop (`POST /stop` on P4): revokes any active manual lease or
     autonomous command, commands zero velocity immediately, and latches the
     rover in `STOPPED` (`200 OK`).

5. **`POST /api/manual/rearm`**
   - Request:
     ```json
     {
       "roverId": "rover-1"
     }
     ```
   - Clears the `STOPPED` latch to `IDLE` (`motion: false`, `rearmed: true`)
     without resuming motion or restoring any previous lease (`200 OK`, or
     `409 NOT_STOPPED`).

6. **`GET /api/manual/status?roverId=rover-1`**
   - Returns current `roverId`, `bridgeBootId`, `stage` (`IDLE`, `MANUAL_IDLE`,
     `MANUAL_DRIVING`, `STOPPED`), active `leaseId`/`leaseGeneration` (or `null`),
     `lastSequence`, `appliedVelocity`, `telemetryFresh`, `poseReceivedAt`, and
     `caps`.

### Backend -> Robot Bridge (`P3 -> P4`) & Local Bench Dev Proxy
- Base URL: `ROBOT_BRIDGE_URL` / `ROVER_BRIDGE_URL` (default `http://127.0.0.1:8004`
  or operator-configured bridge port), authenticated with
  `Authorization: Bearer <PEER_TOKEN>`.
- P4 mirror endpoints:
  - `POST /manual/lease`
  - `POST /manual/drive`
  - `POST /manual/release`
  - `POST /stop`
  - `POST /manual/rearm` (alongside `POST /commands/{commandId}/rearm`)
  - `GET /manual/status?roverId=rover-1`
- **Local bench testing before P3 manual proxy routes are wired**: `frontend/`'s
  Vite dev server (`vite.config.ts`) may proxy `/api/manual/*` to P4 `/manual/*`
  by injecting `Authorization: Bearer ${process.env.PEER_TOKEN}` inside the
  Node-side Vite proxy configuration. `PEER_TOKEN` must never be exposed in
  browser bundle code (`import.meta.env.VITE_*`).

## 3. Velocity Caps, Ordering & Independent Bridge Watchdog

- **Body-frame units (`rmepcore` `drive_speed`)**:
  - `xMps`: forward (+) / backward (-) in metres/second
  - `yMps`: right (+) / left (-) in metres/second
  - `zDegps`: clockwise/yaw rate in degrees/second
- **Conservative initial slice caps (fail-closed `422` on out-of-range)**:
  - `|xMps| <= 0.35` m/s
  - `|yMps| <= 0.35` m/s
  - `|zDegps| <= 45.0` deg/s
  - Out-of-range or non-finite values are rejected with `422 Unprocessable Entity`
    without moving motors so UI scaling bugs fail closed.
- **Strict sequence & intent expiry (`<= 500 ms`)**:
  - `sequence` must be a strictly increasing positive integer per
    `(leaseId, leaseGeneration)`.
  - Duplicate or lower `sequence`, mismatched `leaseId`/`leaseGeneration`, or
    `expiresAt - issuedAt > 0.5 s` / clock past `expiresAt` is rejected with
    `409 STALE_INTENT` or `409 LEASE_EXPIRED` and causes zero motion.
- **Independent bridge watchdog (`<= 300 ms`)**:
  - The DJI SDK `timeout` parameter is only a host threading timer, not an
    independent physical fail-stop.
  - P4 (`robot/**`) must run an independent server-side asyncio/thread watchdog
    with a `300 ms` silence cutoff: if no valid `POST /manual/drive` arrives
    within `300 ms` while a lease is active (or when `expiresAt` passes), P4
    immediately commands `drive_speed(0, 0, 0, timeout=0.25)`, expires the
    lease, and transitions to `IDLE` even if the browser's `release` packet was
    dropped.

## 4. Overhead Click & Manipulation Stay Simulated in Slice 1

- **Overhead click-to-move (`SIMULATED_ONLY`)**: A click carries `cameraId`,
  `sessionId`, `geometryVersion`, `frameId`, and `pixel: [u, v]`, never raw
  `xMps`/`yMps` motor speeds. Live execution waits until P5/P4 route and
  calibration verification.
- **Arm & gripper (`SIMULATED_ONLY`)**: Excluded from live actuation in
  `MANUAL_CHASSIS_SLICE_1` until P4 hardware capability verification.

Normative fixture: `contracts/fixtures/manual-control.json`.
