# Connected manual adapter checkpoint

The Connected sidebar has a Manual view using the six merged backend routes in
backend/MANUAL_PROXY.md. It controls the explicitly selected backend rover ID;
local saved setup serials are not silently mapped onto a backend device.

This checkpoint admits **executionMode=simulator only**. Physical mode shows
its status but every write is disabled. It does not start a DJI session or
provide overhead navigation or arm/gripper commands. A separate human-approved,
operator-supervised window is required before enabling physical writes.

- Explicit lease grant binds rover/lease/generation/bridge boot and lower caps.
- Held direction buttons or focused Space+WASD/QE input produce 100 ms ticks,
  <=250 ms intent TTL (or the lower grant), and conservative .15 m/s /15 deg/s
  maxima reduced further by the granted caps.
- No nonzero request is retried or queued. A lost/ambiguous ACK, expired local
  lease, stale telemetry or changed authority clears input and requests stop.
- Pointer release/cancel, Space release, Escape, blur, hidden tab, view change
  and unmount revoke local control. Release and stop bypass a pending drive.
- UNKNOWN is unconfirmed. A write ACK is not stationary evidence. Status never
  adopts a lease. Re-arm and acquisition are explicit and restore no movement.
- There is no clock correction: grant clock skew >200 ms blocks control. Timing
  must be checked on target hosts before physical acceptance.

Verification:35 frontend tests and production build pass, including lower caps,
boot change, lost ACK/no replay, priority release, late grant/ACK, stale telemetry,
lease expiry, UNKNOWN and explicit re-arm. Actual browser acceptance against
main47d89ae backend and P4 fake HTTP server is initially BLOCKED: backend returns
503 MANUAL_UNAVAILABLE on its first status. Owner test reproduces on Windows:

    BACKEND_P4_FIXTURE_ROOT=<repository checkout>
    python -m unittest backend.tests.test_manual_bridge_integration -v

The first status assertion fails (503 expected200). Local initialization of a
fresh httpx.AsyncClient(trust_env=False) measured460/301/252 ms, exceeding the
backend250 ms enclosing budget before a request. Reported to backend owner in
board6655485359/6655505511; no backend file or deadline was changed here.
Zero nonzero fake outputs were produced before this blocker. This is NOT a
passing browser-to-P4 lifecycle or physical acceptance claim.

Repeatable isolated fixture (no DB, SDK, camera, model or live robot):

    python frontend/scripts/manual_browser_fixture.py

It uses the actual backend ManualProxy with a fixture-only autonomous_active
callback returning false and the robot owner's fake output server. This bypasses
DB admission only inside this clearly labelled local test fixture, never normal
backend startup. Generated credentials remain in memory. Open the printed URL,
select Connected then Manual. Write quit to the printed control file to clean up.
