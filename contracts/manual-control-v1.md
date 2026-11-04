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
  in `NAVIGATING`, `ALIGNING`, `PICKING`, `HELD`, or `UNKNOWN` (and on P3
  `backend/`, any autonomous mission in `dispatching`, `running`, `holding`, or
  `unknown`), and with manual/bridge `STOPPED` (while stop is latched) or
  manual/bridge `UNKNOWN` (stop confirmation pending) or stale rover telemetry:
  - Active autonomous mission/command (`dispatching` / `running` / `holding` /
    `unknown` on P3, or `NAVIGATING` / `ALIGNING` / `PICKING` / `HELD` /
    `UNKNOWN` on an autonomous P4 command) -> `409 AUTONOMOUS_ACTIVE` when
    rejecting `POST /api/manual/lease` or `POST /api/manual/drive`
  - Lease already held by another generation/session -> `409 LEASE_HELD`
  - Manual/bridge stage is `UNKNOWN` (zero velocity requested with no speed ACK,
    awaiting 3 consecutive fresh stationary telemetry observations to confirm the
    chassis stopped) or rover link/telemetry is older than `0.5 s`:
    - `POST /api/manual/lease` and `POST /api/manual/drive` (and P4 `/manual/lease`,
      `/manual/drive`) return **`503 TELEMETRY_STALE`**
    - `POST /api/manual/rearm` (and P4 `/manual/rearm`) returns **`409 NOT_STOPPED`**
    - **Important (`#6636186879`, `#6636211835`):** Manual stage `UNKNOWN` is
      **not** autonomous mission state `unknown` (mission `unknown` stays
      `409 AUTONOMOUS_ACTIVE`). P3 tracks unconfirmed manual/bridge stops on the
      manual status stage (`stage: "UNKNOWN"`), not by mutating mission state.
  - Rover latched in `STOPPED` (after 3 fresh stationary observations confirm an
    operator stop, watchdog silence timeout, intent expiry, or telemetry fault):
    - `POST /api/manual/lease` and `POST /api/manual/drive` return **`409 STOP_LOCKED`**
    - `POST /api/manual/rearm` returns **`200 OK`** (`stage: "IDLE"`,
      `motion: false`, `rearmed: true`, without restoring any prior lease)
- Autonomous pickup/navigation commands (`POST /commands/*`) are likewise
  rejected (`409 MANUAL_LEASE_ACTIVE`) while a manual lease is active or manual
  stop confirmation is pending.
- `rearm` (`POST /api/manual/rearm` / `POST /manual/rearm`) is legal **only**
  after stationary telemetry has been confirmed (`stage: "STOPPED"`). While
  `stage: "UNKNOWN"` (or `IDLE` / `MANUAL_*`), `rearm` returns **`409 NOT_STOPPED`**.
  When confirmed `STOPPED`, `rearm` transitions `STOPPED -> IDLE`
  (`motion: false`, `rearmed: true`) without restoring any prior lease,
  authority, or motion. A new `POST /api/manual/lease` is required after `rearm`.

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
   - Immediately commands `drive_speed(x=0.0, y=0.0, z=0.0, timeout=0.25)` on P4
     and invalidates the lease. Because real EP Core `ProtoChassisSpeedMode`
     packets have no protocol ACK, P4 enters `UNKNOWN` when zero velocity is
     requested until **3 consecutive fresh (`<= 0.5 s`) stationary telemetry
     observations spanning at least 200 ms** confirm zero motion. Three fresh
     samples closer together than that do not confirm the stop. The immediate `200 OK` body is always
     `stage: "UNKNOWN"`, `stationaryConfirmed: false`, `stationarySamples: 0`,
     `motion: false`; callers poll `GET /api/manual/status` for the later
     `UNKNOWN -> IDLE` transition after 3 consecutive fresh stationary samples
    spanning at least 200 ms.
     If confirmation times out/fails, a clean release STAYS `UNKNOWN`
     (`503 TELEMETRY_STALE` on lease/drive, `409 NOT_STOPPED` on rearm) unless a
     later fault sample confirms stop, which latches `STOPPED`.
     ```json
     {
       "roverId": "rover-1",
       "released": true,
       "stage": "UNKNOWN",
       "zeroVelocityRequested": true,
       "stationaryConfirmed": false,
       "stationarySamples": 0,
       "motion": false
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
     autonomous command and commands zero velocity immediately. On real hardware,
     stage is `UNKNOWN` while awaiting 3 consecutive fresh stationary telemetry
     samples and then latches in `STOPPED` (`200 OK`).

5. **`POST /api/manual/rearm`**
   - Request:
     ```json
     {
       "roverId": "rover-1"
     }
     ```
   - Legal only when confirmed `STOPPED` (rejected with **`409 NOT_STOPPED`** while
     `stage: "UNKNOWN"` awaiting 3 stationary telemetry samples, or if already
     `IDLE`). Clears the `STOPPED` latch to `IDLE` (`motion: false`,
     `rearmed: true`) without resuming motion or restoring any previous lease
     (`200 OK`).

6. **`GET /api/manual/status?roverId=rover-1`**
   - Valid `stage` values:
     - `IDLE`: no active manual lease; chassis stationary and ready
     - `MANUAL_IDLE`: manual lease active; zero velocity currently commanded
     - `MANUAL_DRIVING`: manual lease active; non-zero velocity currently commanded
     - `UNKNOWN`: zero velocity requested (no speed ACK on EP Core); awaiting 3
       consecutive fresh (`<= 0.5 s`) stationary telemetry observations (`lease`
       and `drive` return **`503 TELEMETRY_STALE`**; `rearm` returns
       **`409 NOT_STOPPED`**)
     - `STOPPED`: stationary telemetry confirmed after an operator stop, watchdog
       silence timeout, intent expiry, or telemetry fault (`lease` and `drive`
       return **`409 STOP_LOCKED`**; `rearm` returns **`200 OK`** -> `stage: "IDLE"`,
       `motion: false`)
   - Returns current `roverId`, `bridgeBootId`, `stage`, active
     `leaseId`/`leaseGeneration` (or `null`), `lastSequence`, `appliedVelocity`,
     `telemetryFresh`, `poseReceivedAt`, `stationaryConfirmed`, and `caps`.

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
- **Independent bridge watchdog (`<= 300 ms`), telemetry freshness (`<= 0.5 s`),
  and truthful hardware stop/fault state machine (`#6636137847`)**:
  - The DJI SDK `timeout` parameter is only a host threading timer, not an
    independent physical fail-stop, and `ProtoChassisSpeedMode` is a PUSH
    message with no protocol ACK.
  - Telemetry age must be `<= 0.5 s` (`maxTelemetryAgeMs: 500`) to grant a lease
    or accept a drive command.
  - P4 (`robot/**`) runs an independent server-side watchdog with a `300 ms`
    input silence cutoff (`watchdogTimeoutMs: 300`) and `<= 500 ms` intent TTL
    (`maxIntentTtlMs: 500`).
  - Whenever zero velocity is requested (on explicit `stop`, watchdog `300 ms`
    silence timeout, intent expiry, stale telemetry `> 0.5 s`, or operator
    `release`), P4 commands `drive_speed(0, 0, 0, timeout=0.25)`, revokes active
    authority, and enters `stage: "UNKNOWN"` (`503 TELEMETRY_STALE` on
    `lease`/`drive` and `409 NOT_STOPPED` on `rearm`, distinct from P3's
    autonomous mission `unknown` state which is `409 AUTONOMOUS_ACTIVE`)
    until **3 consecutive fresh (`<= 0.5 s`) stationary telemetry observations
    spanning at least 200 ms** confirm the chassis has physically stopped
    (`stationarySamplesRequired: 3`, `stationarySpanMs: 200`).
  - **After any fault or explicit stop** (watchdog `> 300 ms` silence timeout,
    intent expiry, stale telemetry `> 0.5 s`, or `POST /stop`), once 3
    consecutive fresh stationary telemetry observations spanning at least 200 ms
    confirm zero motion,
    state **latches in `stage: "STOPPED"` (`409 STOP_LOCKED` on `lease`/`drive`)**.
    If stationary telemetry is not confirmed, state remains `stage: "UNKNOWN"`
    (`503 TELEMETRY_STALE` on `lease`/`drive`, `409 NOT_STOPPED` on `rearm`) and
    P3/P1 retain the rover slot and refuse new leases or motion commands.
  - **Explicit recovery (`rearm`)**: `POST /manual/rearm` (`POST /api/manual/rearm`)
    is legal only when confirmed `STOPPED` (rejected with `409 NOT_STOPPED`
    while `UNKNOWN`) and transitions `STOPPED -> IDLE` (`motion: false`,
    `rearmed: true`) without restoring any prior lease, authority, or motion.
  - **Clean voluntary operator `release`** (`POST /manual/release` with no fault):
    after 3 consecutive fresh stationary observations confirm zero motion, state
    transitions `UNKNOWN -> IDLE`. If confirmation times out or fails, state STAYS
    `UNKNOWN` (never latches `STOPPED` on timeout alone); only a later fault
    sample confirming stop latches `STOPPED`.

## 4. Overhead Click & Manipulation Stay Simulated in Slice 1

- **Overhead click-to-move (`SIMULATED_ONLY`)**: A click carries `cameraId`,
  `sessionId`, `geometryVersion`, `frameId`, and `pixel: [u, v]`, never raw
  `xMps`/`yMps` motor speeds. Live execution waits until P5/P4 route and
  calibration verification.
- **Arm & gripper (`SIMULATED_ONLY`)**: Excluded from live actuation in
  `MANUAL_CHASSIS_SLICE_1` until P4 hardware capability verification.

Normative fixture: `contracts/fixtures/manual-control.json`.
