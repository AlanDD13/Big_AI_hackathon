# Dashboard camera transport

Backend/Frontend agreement: board comments `6654424583` and `6654481593`.
This slice transports a configured source; it does not prove that a captured
image is current, identify an object, or authorize a mission. No live source
has been validated by these offline tests.

## Configuration

Set these only in the backend environment, then restart the backend process:

| Variable | Meaning |
| --- | --- |
| `BACKEND_CAMERA_URL` | Fixed HTTP(S) JPEG/MJPEG source URL reachable from the backend host. Empty disables the feed. No embedded username/password or fragment. |
| `BACKEND_CAMERA_AUTHORIZATION` | Optional upstream Authorization header value, such as a locally configured Basic or Bearer value. Not a dashboard or browser setting. |
| `BACKEND_CAMERA_CA_CERT` | Optional path to a trusted source CA certificate. Without it, normal system CA verification applies. TLS verification is never disabled. |

Do not put credentials in the URL/query or board comments. The backend does not
forward browser cookies, operator/peer authorization headers, or upstream
Set-Cookie/Location headers. It follows no upstream redirects and ignores
environment HTTP proxies for this fixed-source connection. A browser-supplied
`url` query parameter cannot select the upstream.

The current Mapping viewer at `ee7adc1` binds loopback and repeats cached JPEGs.
Its owner must publish a reachable configured source and real capture freshness
semantics. Starting a backend with configuration alone is not a live-camera pass.

## Browser contract

Both routes require the normal operator `session` cookie. A peer credential
does not substitute for that cookie. Missing/expired sessions return the normal
401 `UNAUTHORIZED` error envelope.

`GET /api/camera/status` returns, without contacting the camera:

```json
{"configured":true,"captureFreshness":"unverified"}
```

`configured` means syntactically usable source configuration, not reachable,
healthy or live. It is false when missing/invalid. Capture freshness is always
`unverified` in this slice. No capture timestamp, sequence, source ID, frame ID,
geometry version or media-type guess is fabricated. Frontend should show
"Source freshness unverified" rather than "Live" when showing these images.

`GET /api/camera/feed` returns validated `image/jpeg` or
`multipart/x-mixed-replace; boundary="<actual source boundary>"`. Responses use
`Cache-Control: no-store`. JPEG payloads are buffered within the size/deadline
limit; MJPEG is prefetched through the first JPEG-framed part before streaming.
This validates framing/markers and media, not image decoding or capture age;
the browser must handle decode failures. Other upstream headers are not relayed.

Unavailable/unconfigured sources, upstream auth failure, redirects, invalid
media/framing, exhausted connection capacity and pre-stream timeouts return:

```json
{"error":{"code":"CAMERA_UNAVAILABLE","message":"Camera feed is unavailable"}}
```

The status is 503 and source details are omitted. After headers/body start,
failure terminates the stream instead of appending JSON to an image. Frontend
must clear/cover retained images on failure and reconnect. Session validity is
checked again after opening and before each emitted stream chunk. Session
revocation, disconnect, cancellation, read failure and stream lifetime expiry
release source resources; a broken stream cannot later change its HTTP status
to 401, so a fresh request or status poll obtains the current auth result.

Transport bounds are 5 seconds to acquire the first complete framed image,
3 seconds per upstream connect/read/pool/write operation, 8 MiB per initial
image (8 KiB multipart preamble/header allowance), 60 seconds per MJPEG response,
and two active upstream responses per backend process. This initial slice does
not share one capture between browsers; a third active request fails with the
same generic 503. Point it at the owner's fan-out viewer, not an uncoordinated
second camera process. These limits are not motion/heartbeat settings. Proxy
response duration also is not camera freshness.

Frontend's proposed display policy is a 5-second first decoded-frame deadline,
reconnect delays 1/2/4/8 seconds capped at 10, clear retained imagery on failure,
and stop/re-authenticate on 401. A real capture-age/sequence signal is still
needed before applying the proposed 2-second age / 3-second stale policy.

## Offline verification

From the repository root, after installing `backend/requirements.txt`:

```powershell
python -m unittest backend.tests.test_camera_proxy -v
```

Tests use `httpx.MockTransport` and explicitly synthetic JPEG markers/multipart
parts. They exercise fixed-source auth isolation, JPEG/MJPEG forwarding, generic
failure sanitization, header/media checks, expiry during acquisition/streaming,
bounded acquisition/response size/lifetime/capacity and cleanup, including ASGI
send failure/cancellation before streaming begins. No camera, network service,
model, robot or database is contacted.

Live acceptance remains: P5 publishes a reachable real source and freshness
contract; frontend wires the agreed widget states; owners verify moving images,
capture-age behavior and disconnect/reconnect together on the target PCs.
