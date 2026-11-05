# Camera widget integration

Connected Objects & alerts and Fleet use the backend operator cookie for the fixed
same-origin `/api/camera/status` and `/api/camera/feed` routes. Demo and review
fixtures keep their camera empty. Configure the source and its authentication in
the backend only; see `backend/CAMERA_PROXY.md` (camera proxy PR26).

The widget decodes JPEG snapshots and streamed MJPEG JPEG payloads, bounds its
buffer, clears imagery after stream failure, and reconnects at 1/2/4/8 seconds
capped at 10. First-frame acquisition/decode has a five-second deadline. Streams
renew after 55 seconds; single JPEG snapshots refresh after one second. A 401
status attempts the existing local session setup once, then requires retry.
Unsupported/absent proxy and unconfigured sources show explanatory empty states.

Every displayed image is labelled **Source freshness unverified**. Configuration,
received bytes and successful decoding do not prove capture time or movement.
A repeated cached source image remains unverified. There are no source frame IDs,
capture timestamps, calibrated coordinates or robot command authority in this
transport. A real source freshness contract is still required.

Verification: 27 frontend tests and production build pass. Browser tests used
exact backend camera-proxy head2f955f9 with a local synthetic 640x360 source:
MJPEG frames advanced, JPEG decoded/refreshed, source503 cleared the image and
recovered automatically, and invalid JPEG content was rejected/cleared. Tests
used generated local credentials, no database, camera, model or robot requests.
Real Tezqx camera acceptance remains pending a reachable configured source.
