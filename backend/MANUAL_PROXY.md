# Operator manual chassis forwarding

Backend owner: `zekihxh`. Consumes `manual-control-v1` plus the
`bridgeBootId-v1` fence agreed on board `6654683864` / `6654705493`.
P4 owns `robot/**` and the corresponding shared contract/fixture update.
The first tested peer head was `b6cb24e`; P4 subsequently published release
braking fix `2c6b6c9`. Consume the latest verified P4 manual-controls branch.

This slice is chassis-only. It does not dispatch queued collection missions,
perform navigation, collect objects, move the arm/gripper, or connect to DJI.
The complete detection-to-recycling workflow remains a separate agreement.

## Configuration and authority

Run one backend process, using the normal backend README startup instructions.
Configure these privately in that process's environment:

- `ROBOT_BRIDGE_URL`: fixed P4 HTTP(S) listener reachable from this backend.
  `ROVER_BRIDGE_URL` is the legacy fallback if the first variable is empty.
  There is no implicit localhost connection. Do not embed credentials, query
  strings or fragments in this URL.
- `PEER_TOKEN`: same server-side peer credential as P4, at least 16 characters.
  It is never accepted from or returned to the browser.
- `BACKEND_MANUAL_ROVER_ID`: fixed bridge rover, defaults to `rover-1`.
- `BACKEND_DATABASE_URL`: existing scoped backend runtime database, needed to
  check whether this rover has an autonomous mission in `dispatching`,
  `running`, `holding`, or `unknown`. Missing/unavailable storage blocks lease,
  drive and rearm. Status, release and priority stop do not wait for storage.
- Existing `OPERATOR_TOKEN` / cookie settings establish operator sessions.

Browser requests require the existing operator cookie. A lease is bound in
memory to that session, fixed rover, lease ID, generation and bridge boot.
Another session cannot drive or release it even if it knows the IDs. Any
authenticated operator can issue priority stop. Logout/expired-session requests
invalidate owned authority and attempt priority stop. P4's independent watchdog
must stop after input silence even if backend/browser/network disappears.

Bindings are not recovered on backend restart and are not suitable for multiple
workers. A new process must reconcile P4 state and obtain a fresh explicit lease;
it must not adopt a lease from a status response. P4 enforces one current lease
and the required boot fence, including after its own restart. No nonzero request
is retried or queued behind an in-flight request. There is no automatic rearm or
lease reacquisition.

Current backend HTTP has no autonomous dispatch worker. The existing database
read and P4's own manual/autonomous exclusion protect this rollout. Before any
new autonomous worker or multiple backend processes are enabled, add one shared,
atomic manual/autonomous admission authority; a read-before-forward check alone
is not that future transactional interlock. No migration is added by this slice.

## Routes and payloads

| Browser route | Bridge request |
| --- | --- |
| `POST /api/manual/lease` | `POST /manual/lease` |
| `POST /api/manual/drive` | `POST /manual/drive` |
| `POST /api/manual/release` | `POST /manual/release` |
| `POST /api/manual/stop` | **`POST /stop`** |
| `POST /api/manual/rearm` | `POST /manual/rearm` |
| `GET /api/manual/status?roverId=rover-1` | P4 health + same-rover manual status |

Lease requires exactly `roverId` and integer `requestedTtlMs` in `1..10000`.
Drive requires the canonical fields below. This example is a synthetic fixture;
live timestamps must be current and identity must come from the actual grant:

```json
{
  "roverId":"rover-1", "leaseId":"lease-1", "leaseGeneration":1,
  "bridgeBootId":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "sequence":1,
  "issuedAt":100.0, "expiresAt":100.2,
  "xMps":0.03, "yMps":0.0, "zDegps":0.0, "frame":"BODY"
}
```

Release requires `roverId`, `leaseId`, `leaseGeneration`, `bridgeBootId`, and
`reason` (`user_release`, `blur`, `tab_hidden`). Stop requires `roverId` and
`reason: "operator_stop"`; it deliberately has no boot or lease requirement.
Rearm requires `roverId` and the observed `bridgeBootId`.

Backend requires `manualBootFence: "bridgeBootId-v1"` in actual P4 health before
lease/rearm. Health, status and grant must refer to the same boot. Bound writes
retain the original boot; never replace it to make a stale request pass. Dynamic
granted caps can be lower than canonical `0.35/0.35/45` and are enforced. No input
is clamped upward or silently rewritten. Granted intent TTL is enforced as well
as the canonical 500 ms maximum. Successful drive ticks renew the local lease
TTL consistently with P4. Frontend proposes 100 ms input ticks / <=250 ms TTL;
check actual clocks and network latency before physical acceptance.

Status preserves real P4 state/evidence, adding `executionMode` and
`manualBootFence` from health only when boot-associated with that status.
`executionMode` is `simulator` or `physical`, not an end-to-end acceptance claim.
`backendStopLocked` reports a backend priority-stop latch. An observed `IDLE`
does not clear this latch; only a successful explicit, current rearm can do so.
If it remains true after an ambiguous/late rearm, request stop again and reconcile
confirmed `STOPPED` before explicitly rearming. No stationary evidence is invented.

Lease/drive reject active autonomous authority with `409 AUTONOMOUS_ACTIVE`.
Manual `UNKNOWN` is separate: lease/drive -> `503 TELEMETRY_STALE`, rearm ->
`409 NOT_STOPPED`. Confirmed stop locks new leases (`409 STOP_LOCKED`). Other
contract codes include `LEASE_HELD`, `LEASE_EXPIRED`, `STALE_INTENT`,
`BRIDGE_BOOT_MISMATCH`, `VALIDATION_ERROR` and `UNKNOWN_ROVER`. Upstream error
details/headers are not returned; public messages are generic. Unavailable,
unfenced, malformed or ambiguous bridge responses return `503 MANUAL_UNAVAILABLE`.
No response claims physical stop from a write acknowledgement alone.

Release and global stop bypass a pending drive/lease lock. A late or ambiguous
grant/rearm cannot clear a newer backend stop latch; backend reasserts priority
stop and retains uncertainty. Nonzero writes use a 250 ms total upstream budget
(200 ms HTTP operation timeout), no redirects, normal TLS verification, fixed
server-side peer auth, no environment HTTP proxy, and a 64 KiB response limit.
This network timeout is not P4's independent <=300 ms watchdog guarantee.

The verified-TLS HTTP client is initialized once during backend lifespan startup
and reused across requests, then closed on shutdown. Client/context construction
is outside the 250 ms transport budget because Windows initialization can exceed
that budget alone. Direct library users should call `start()`/`close()` around
their session; lazy setup is supported and drive expiry is rechecked afterwards.

## Offline checks

```powershell
python -m unittest backend.tests.test_manual_requests backend.tests.test_manual_proxy -v
```

Fake-upstream tests cover canonical validation, lower caps, sequence/expiry,
cross-session theft/release, autonomous conflict, lost ACK without resend,
restart/boot collision, logout/session expiry, sustained driving past initial
lease TTL, and priority stop versus delayed rearm. No SDK or real bridge is used.

To check actual P3-to-P4 wire compatibility, explicitly point the optional test
at a P4 checkout with its fake-output `robot/tests/test_bridge.py` helper:

```powershell
$env:BACKEND_P4_FIXTURE_ROOT = "<absolute path to P4 manual-controls checkout>"
python -m unittest backend.tests.test_manual_bridge_integration -v
```

That test starts only an ephemeral loopback HTTP server, asserts simulator mode,
and records velocity tuples in a Python list. It covers status, grant, drive,
release/UNKNOWN/IDLE, stop/STOPPED, and rearm with no resumed motion. Without the
variable it skips. Physical bench results remain P4's evidence; P1->P3->P4
supervised acceptance is still required after frontend is wired.

Verified with P4 `2c6b6c9`: the complete integrated backend suite passed all 93
tests with disposable PostgreSQL 18 using the scoped runtime role, including
the P4 fake-output HTTP lifecycle; all four SQL smoke scripts passed. No shared
database or physical bridge was used. Camera routes merged in main are covered
by the same run.
