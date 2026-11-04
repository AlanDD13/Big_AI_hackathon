# Backend objects and alerts

Choose **Connected** in the header. **Demo** retains the original simulator.
Connected mode never substitutes fixture objects, robots or missions. It shows
backend objects and alerts, a selectable inspector, classification when supplied,
stationarity duration/sample count, mapping validity and exact-frame evidence.
`potentialTarget` and `dispatchEligible` are separate backend decisions. Missing
classification, coordinates and scores stay unknown. Dispatch remains disabled
even if an object becomes eligible: mission routes are not implemented yet.
The main camera panel remains empty.

## Local setup without a sign-in screen

1. Start `backend/` using its README. On loopback HTTP only, set
   `BACKEND_COOKIE_SECURE=0`; the backend defaults to secure HTTPS cookies.
2. Copy `frontend/.env.example` to `frontend/.env.local` (ignored by Git).
   Set `BACKEND_URL` to the backend origin, for example
   `http://127.0.0.1:8000`. Set `OPERATOR_TOKEN` to the same token configured
   on the backend (at least 16 characters). Never prefix a token with `VITE_`.
3. Run `npm.cmd run dev` from `frontend/`. Open the printed loopback URL and
   choose **Connected**. Restart Vite after changing server configuration.

The development server proxies `/api` to the configured backend. If a snapshot
returns 401, the browser calls the loopback-only `POST /__local/session` helper.
That helper supplies the operator token server-side to `POST /api/session` and
passes only its HttpOnly session cookie to the browser. It rejects non-loopback
callers, mismatched Origin/Host, and requests without the custom same-origin
header. It is absent from production builds and `vite preview`. Backend auth
and peer routes are unchanged. Do not expose this development server on a LAN.
No PEER_TOKEN, vision token, database credential or xAI key belongs in the UI.

For a deployed build, provision the existing same-origin backend session through
the host. This change intentionally supplies no production login or credential
management interface. A standalone HTML export supports Demo only; connected
mode requires the HTTP server and backend configuration above.

## Recovery and compatibility

The adapter reads `GET /api/snapshot` before opening native EventSource at
`/api/events?after=<encoded cursor>`. It handles `object.updated`,
`alert.potential_target`, `alert.target_revoked` and `snapshot_required`.
It replaces complete entity bodies, ignores stale revisions within a boot,
deduplicates alerts, and refetches on a boot change, replay gap or malformed event.
On transport loss it closes the old stream and refetches with backoff (1â€“15 s),
retaining the previous data with a stale warning. A 60-second snapshot refresh
also rechecks idle sessions. Requests time out after 10 seconds. Rejected session
setup stops automatic retries and offers manual retry after configuration.

Alert read markers are local, scoped to backend boot and alert ID. They reset
when the Connected view is closed. They are not backend acknowledgements.
Camera/detector notices may have `objectId: null`; the UI labels them by camera
instead of requiring an object selection, and subscribes to their stream events.
Evidence URLs must be same-origin `/api/evidence/<id>`; a missing/expired image
gets an unavailable placeholder. The UI never fetches camera/vision URLs.

Validated against `backend/app.py` and `backend/state.py` on main `9a237fd`,
including mission queue, vision ingest and vision persistence. Backend ingest
accepts peer events; the browser still reads only the projected snapshot/SSE.
Classification remains PENDING until its event arrives. Exact evidence frames
remain unavailable unless the backend supplies and serves their evidence URL.
Persistence requires BACKEND_DATABASE_URL in the backend process; this value
never belongs in the browser.

Full local database/restart/browser results and the safe repeatable test runner:
[FULL_STACK_VALIDATION.md](FULL_STACK_VALIDATION.md).
