# P1 / P3 / P4 manual chassis integration

P4 bridge source: `robot/bridge.py`, existing PR17 branch. Canonical wire fields:
`contracts/manual-control-v1.md` and `contracts/fixtures/manual-control.json`.
This handoff is implementation/test guidance, not a sector-navigation contract.

## Current checkpoint

P4 provides authenticated HTTP manual endpoints, real coherent chassis telemetry,
an independent host watchdog, generation/sequence/expiry validation and explicit
stop/rearm state. Offline HTTP lifecycle tests cover drive, release -> UNKNOWN ->
IDLE, emergency stop -> UNKNOWN -> STOPPED, early rearm rejection and no-resume.
Physical bench tests previously measured .2650s silence-to-zero dispatch and fresh
stationary confirmation. These are P4 evidence, not P1-to-P3-to-P4 acceptance.
The separate direct Grok tool loop is NOT the manual bridge or its safety path.

On 2026-11-05, a real HTTP .05m/s release test exposed normal braking being
misclassified as UNEXPECTED_MOTION. Fix: clean release remains disarmed UNKNOWN
while zero is repeatedly requested during the existing1.5s confirmation window.
Nonquiet samples reset stationary evidence; persistent motion beyond that bound
faults and eventually latches STOPPED. Invalid samples and motion from already
STOPPED/idle states still fault immediately. Fault zero I/O executes outside the
observation lock, preventing a worker-lock false WRITE_TIMEOUT. Stationary
thresholds and the watchdog cutoff are unchanged.

The operator-requested .15m/s HTTP retest passed both short forward legs:
release returned UNKNOWN then IDLE with4 fresh stationary samples (about.57s
from release request); unfenced global stop returned UNKNOWN then STOPPED with4
samples, rejected early rearm and new lease while latched, and explicit rearm
sent no nonzero output. Final shutdown confirmed3 stationary samples and closed
SDK/server. The human explicitly confirmed both movements stopped and rover
stationary. Local report: robot/.local/manual-http-bench/1793872227769003000.json.
This is P4 HTTP physical acceptance only; no P1/P3 end-to-end pass is claimed.

At inspection, frontend ManualControl is a local simulator; backend has no manual
proxy routes. P1/P3 owners are requested on board6654563199. Their changes must
land/integrate before end-to-end physical success can be claimed.

## Operator-coordinated P4 startup

Only one SDK owner on the LAN. Stop Grok tools, camera-only processes and other
rover sessions before launch. Keep operator at the physical power switch. For a
first bench, configure PEER_TOKEN privately on P3/P4 and run from repository root:

```bash
robot/.venv/bin/python robot/bridge.py --mode physical --sn 3JKCK7W0030DJS \
  --allow-motion --host 0.0.0.0 --port 8004 \
  --max-x-mps 0.05 --max-y-mps 0.05 --max-yaw-degps 5
```

This starts DISARMED/STOPPED after confirmed startup telemetry; it does not
implicitly rearm. It includes camera capture, no manipulation/arm reset. Cap
arguments validate before connecting and cannot exceed canonical caps. Exceeding
a configured cap is rejected; a UI scaling bug is not silently clamped.
`GET /health` has additive diagnostic executionMode and manualCaps fields so a
simulator cannot be confused with the requested physical process. Existing
manual status also carries the actual caps. New health diagnostics are not
dispatch authority or backend capability negotiation.

The separately agreed restart fence IS required for motion admission:
`manualBootFence: "bridgeBootId-v1"` in health. Public drive/release/rearm JSON
must include the observed `bridgeBootId`; missing/invalid ->422 VALIDATION_ERROR,
different boot ->409 BRIDGE_BOOT_MISMATCH before mutation. Public release must
also match current rover/lease/generation atomically, else409 LEASE_EXPIRED.
Release response includes its boot. Stop remains unfenced/authenticated/global.
P3 confirmed the exact field/error contract on board6654705493. Never replace
an old request's boot with the newly observed boot to make it pass.

P3 sets ROBOT_BRIDGE_URL to P4's reachable LAN address:8004 and server-only peer
auth. Browser -> P3 uses its operator session cookie; browser never gets PEER_TOKEN.
P3 must not point to localhost on a different laptop. Report the actual listener
and health result; no specific current P4 LAN IP is assumed here.

## Existing P4 surface and key semantics

- POST /manual/lease, /manual/drive, /manual/release, /manual/rearm
- POST /stop (P3 /api/manual/stop must map to this, not /manual/stop)
- GET /manual/status?roverId=rover-1
- GET /health (public diagnostic); remaining endpoints require bearer auth.

Body axes: +x forward, +y right, +z clockwise deg/s. Limits for first bench:
0.05m/s,0.05m/s,5deg/s; UI must consume returned caps. Each nonzero intent has
strictly increasing sequence, matching boot/lease/generation, current issuedAt
and expiry <=500ms. Aim for100ms input cadence and <=250ms expiry; validate
P1/P3/P4 clock differences before hardware. Requests must not queue stale intent.
On key/joystick release send zero immediately. Releasing control/blur/tab-hidden
invalidates the lease and requests zero; stage remains UNKNOWN until three fresh
stationary samples spanning>=200ms. Fault/emergency stop latches STOPPED after
confirmation. Rearm requires confirmed STOPPED and never restores old movement
or a lease. UNKNOWN lease/drive ->503 TELEMETRY_STALE; UNKNOWN rearm ->409
NOT_STOPPED. A mission unknown remains separate and blocks manual takeover.

P1 must release on pointer cancel, lost capture, Space release, Escape, blur,
hidden tab, unmount, auth/transport fault and device change. No automatic rearm,
lease reacquisition or replay on reconnect. P3 must enforce session-bound lease
ownership and autonomous exclusion. Any lost write ACK must be reconciled,
never blindly retried. A software stop button is not an independent physical
emergency stop after host or network failure.

## Integration acceptance

1. Offline: expired auth, second operator/lease, autonomous-active conflict,
   wrong generation/boot, duplicate sequence, stale/expired intent and cap
   rejection produce no nonzero output. Verify proxies retain error/stage fields.
2. Connected UI stays explicitly simulated/unavailable until authenticated P3
   forwarding and correct physical bridge identity/status are established.
3. Supervised clear-floor bench: explicit rearm, acquire control, one short .05m/s
   forward hold; release input; verify zero plus stationary telemetry. Exercise
   joystick and keyboard release/blur. Record observed motion and stop separately
   from API acceptance.
4. Emergency stop while short drive active -> immediate zero, UNKNOWN pending
   confirmation, then STOPPED. UI blocks drive and early rearm; explicit rearm
   produces no movement and requires a new lease.
5. Drop intent traffic without status polls: watchdog zero dispatch<=300ms;
   loss/stale telemetry stays UNKNOWN without stationary proof. Host crash or
   network-loss physical stop is not guaranteed by the host watchdog.

Start recording/taught-sector routes only after this chain passes. This slice
does not authorize overhead clicks, autonomous routes, arm/gripper or pickup.

Offline P4 command: `robot/.venv/bin/python -m unittest discover -s robot/tests -p test_bridge.py -v`.
