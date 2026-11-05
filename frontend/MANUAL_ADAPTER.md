# Connected manual adapter checkpoint

The Connected sidebar has a Manual view using the six merged backend routes in
backend/MANUAL_PROXY.md. It controls the explicitly selected backend rover ID;
local saved setup serials are not silently mapped onto a backend device.

Physical control defaults OFF for each Manual view. The operator may explicitly
opt in for a supervised session; the setting is not persisted, resets on view or
rover change, and grants no lease automatically. Drive/re-arm require an actual
physical boot-fenced status advertising caps <=0.05 m/s on each translation axis
and <=5 deg/s yaw. Higher caps keep drive blocked; authenticated priority stop
remains available after explicit opt-in. Simulator behavior is unchanged.
This agent does not activate the opt-in or send real commands without this user's
approval and a separately coordinated P3/P4 operator-ready window. No SDK,
overhead navigation or arm/gripper command is added. Physical acceptance remains
pending; automated opt-in tests use explicitly fake physical status/output only.

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

Verification:36 frontend tests and production build pass. Actual Windows browser
lifecycle passes against the backend and the robot owner's fake-output HTTP
server: status, explicit grant, held forward with nonzero tuples, release and
confirmed IDLE, global stop and confirmed STOPPED, explicit re-arm to IDLE without
restoring authority/motion. Held Space+W+Tab moving focus to a same-page control
also releases to IDLE. Tested lead focus patch e92e26c plus a narrow blur guard:
disabling the Re-arm button must not cancel its own pending re-arm request.
The guard retains held-key/workspace focus cleanup. Physical mode stays off by default; no real-device acceptance is claimed.

Backend fix278f0a3 (merged35a93bd) passes all four Windows client-lifetime and
actual fake-P4 HTTP tests. It resolves the original constructor budget failure
without extending the transport deadline. The fixture uses real request-time
wall clock, avoiding future issuedAt values from a quantized telemetry clock.
Initial failures and fixes are recorded in board6655485359/6655505511/6655731071.
Browser timing stalls can still expire an intent and latch STOPPED, as observed
during automation; no automatic reacquisition/replay occurs. This synthetic pass
is not a physical timing/network acceptance or shared-database admission test.

Repeatable isolated fixture (no DB, SDK, camera, model or live robot):

    python frontend/scripts/manual_browser_fixture.py

It uses the actual backend ManualProxy with a fixture-only autonomous_active
callback returning false and the robot owner's fake output server. This bypasses
DB admission only inside this clearly labelled local test fixture, never normal
backend startup. Generated credentials remain in memory. Open the printed URL,
select Connected then Manual. Write quit to the printed control file to clean up.

For an isolated exported backend branch, ROVR_BACKEND_FIXTURE_ROOT may point at
its source root; normal use tests the current checkout. The fixture never persists
operator/peer tokens or configures a physical bridge URL.
