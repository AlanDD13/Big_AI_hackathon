"""Offline proxy tests: all upstream media and credentials are labelled fakes."""
import asyncio
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.camera_proxy import CameraConfig, CameraProxy, CameraStreamingResponse, CameraUnavailable
from backend.tests.test_api import OPERATOR, PEER


JPEG = b"\xff\xd8fixture-image\xff\xd9"
PART = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + JPEG + b"\r\n"
SOURCE = "https://camera.invalid/stream"
AUTH = "Bearer fixture-upstream-secret"


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks, *, error=None, wait=False):
        self.chunks = chunks
        self.error = error
        self.wait = wait
        self.closed = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error
        if self.wait:
            await asyncio.sleep(30)

    async def aclose(self):
        self.closed += 1


def proxy(handler, **config):
    return CameraProxy(CameraConfig(url=SOURCE, authorization=AUTH, **config),
                       transport=httpx.MockTransport(handler))


class CameraHttpTests(unittest.TestCase):
    def client(self, camera=None, **kwargs):
        return TestClient(create_app(operator_token=OPERATOR, peer_token=PEER,
                                     cookie_secure=False, camera_proxy=camera, **kwargs))

    def login(self, client):
        self.assertEqual(client.post("/api/session", json={"token": OPERATOR}).status_code, 204)

    def test_session_and_disabled_source(self):
        with self.client() as client:
            self.assertEqual(client.get("/api/camera/feed").status_code, 401)
            self.assertEqual(client.get("/api/camera/status").status_code, 401)
            self.assertEqual(client.get("/api/camera/feed", headers={"Authorization": f"Bearer {PEER}"}).status_code, 401)
            self.login(client)
            response = client.get("/api/camera/status")
            self.assertEqual(response.json(), {"configured": False, "captureFreshness": "unverified"})
            self.assertEqual(response.headers["cache-control"], "no-store")
            response = client.get("/api/camera/feed")
            self.assertEqual((response.status_code, response.json()["error"]["code"]),
                             (503, "CAMERA_UNAVAILABLE"))

    def test_jpeg_fixed_source_credentials_and_no_cache(self):
        requests = []
        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers={"Content-Type": "image/jpeg", "Set-Cookie": "upstream=private"}, content=JPEG)
        with self.client(proxy(handler)) as client:
            self.login(client)
            status = client.get("/api/camera/status")
            self.assertEqual(status.json(), {"configured": True, "captureFreshness": "unverified"})
            self.assertEqual(requests, [])  # Configuration status is not an upstream probe.
            response = client.get("/api/camera/feed?url=https://ignored.invalid/",
                                  headers={"Authorization": "Bearer wrong", "X-Private": "not-forwarded"})
            self.assertEqual(response.content, JPEG)
            self.assertEqual(response.headers["content-type"], "image/jpeg")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("set-cookie", response.headers)
            self.assertEqual(len(requests), 1)
            self.assertEqual(str(requests[0].url), SOURCE)
            self.assertEqual(requests[0].headers["authorization"], AUTH)
            self.assertNotIn("cookie", requests[0].headers)
            self.assertNotIn("x-private", requests[0].headers)

    def test_multipart_forwards_real_boundary_and_closes_on_read_failure(self):
        stream = Chunks([PART[:12], PART[12:], PART], error=httpx.ReadError("private-source-detail"))
        camera = proxy(lambda _: httpx.Response(200, headers={"Content-Type": 'multipart/x-mixed-replace; boundary="frame"'}, stream=stream))
        with self.client(camera) as client:
            self.login(client)
            response = client.get("/api/camera/feed")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, PART * 2)
            self.assertIn('boundary="frame"', response.headers["content-type"])
            self.assertNotIn(b"private-source-detail", response.content)
            self.assertEqual(camera._active, 0)
            self.assertEqual(stream.closed, 1)

    def test_unusable_sources_return_generic_error_and_release(self):
        cases = [
            (302, {"Content-Type": "image/jpeg", "Location": "https://redirect.invalid/"}, [JPEG]),
            (401, {}, [b"private error with credentials"]),
            (200, {"Content-Type": "text/html"}, [b"<html>login</html>"]),
            (200, {"Content-Type": "image/jpeg"}, [b"not a jpeg"]),
            (200, {"Content-Type": "image/jpeg"}, []),
            (200, {"Content-Type": "image/jpeg", "Content-Encoding": "gzip"}, [JPEG]),
            (200, {"Content-Type": "multipart/x-mixed-replace"}, [PART]),
            (200, {"Content-Type": "multipart/x-mixed-replace; boundary=frame"}, [b"wrong-boundary"]),
        ]
        for status, headers, chunks in cases:
            with self.subTest(status=status, headers=headers, chunks=chunks):
                stream = Chunks(chunks)
                camera = proxy(lambda _: httpx.Response(status, headers=headers, stream=stream))
                with self.client(camera) as client:
                    self.login(client)
                    response = client.get("/api/camera/feed")
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json()["error"]["code"], "CAMERA_UNAVAILABLE")
                    self.assertNotIn(SOURCE, response.text)
                    self.assertNotIn(AUTH, response.text)
                    self.assertNotIn("private", response.text)
                    self.assertEqual(camera._active, 0)
                    self.assertEqual(stream.closed, 1)

    def test_session_expiry_while_opening_returns_401_and_closes(self):
        clock = {"now": 1000.0}
        stream = Chunks([JPEG])
        def handler(_):
            clock["now"] += 8 * 3600
            return httpx.Response(200, headers={"Content-Type": "image/jpeg"}, stream=stream)
        camera = proxy(handler)
        with self.client(camera, now=lambda: clock["now"]) as client:
            self.login(client)
            self.assertEqual(client.get("/api/camera/feed").status_code, 401)
        self.assertEqual(camera._active, 0)
        self.assertEqual(stream.closed, 1)


class CameraResourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_frame_timeout_releases_source(self):
        stream = Chunks([], wait=True)
        camera = proxy(lambda _: httpx.Response(200, headers={"Content-Type": "image/jpeg"}, stream=stream))
        with patch("backend.camera_proxy.FIRST_FRAME_SECONDS", 0.02):
            with self.assertRaises(CameraUnavailable):
                await camera.open()
        self.assertEqual(camera._active, 0)
        self.assertEqual(stream.closed, 1)

    async def test_source_limit_and_idempotent_cleanup(self):
        camera = proxy(lambda _: httpx.Response(200, headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"}, stream=Chunks([PART])))
        feeds = [await camera.open(), await camera.open()]
        with self.assertRaises(CameraUnavailable):
            await camera.open()
        await feeds[0].close()
        await feeds[0].close()
        self.assertEqual(camera._active, 1)
        third = await camera.open()
        await feeds[1].close()
        await third.close()
        self.assertEqual(camera._active, 0)

    async def test_revoked_stream_session_sends_no_more_bytes(self):
        stream = Chunks([PART, PART])
        camera = proxy(lambda _: httpx.Response(200, headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"}, stream=stream))
        feed = await camera.open()
        allowed = {"value": True}
        body = feed.stream(lambda: allowed["value"])
        self.assertEqual(await anext(body), PART)
        allowed["value"] = False
        with self.assertRaises(StopAsyncIteration):
            await anext(body)
        self.assertEqual(camera._active, 0)
        self.assertEqual(stream.closed, 1)

    async def test_stream_total_deadline_cleans_up(self):
        stream = Chunks([PART], wait=True)
        camera = proxy(lambda _: httpx.Response(200, headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"}, stream=stream))
        feed = await camera.open()
        with patch("backend.camera_proxy.STREAM_SECONDS", 0.02):
            self.assertEqual([chunk async for chunk in feed.stream(lambda: True)], [PART])
        self.assertEqual(stream.closed, 1)
        self.assertEqual(camera._active, 0)

    async def test_bounded_frame_and_bad_source_configuration(self):
        camera = proxy(lambda _: httpx.Response(200, headers={"Content-Type": "image/jpeg"}, content=JPEG))
        with patch("backend.camera_proxy.MAX_FRAME_BYTES", 4):
            with self.assertRaises(CameraUnavailable):
                await camera.open()
        self.assertEqual(camera._active, 0)
        for url in ["", "file:///private", "https://user:password@camera.invalid/", "https://camera.invalid/#fragment",
                    "http://camera.invalid:bad/", "http://camera.invalid:99999/"]:
            self.assertFalse(CameraConfig(url).configured)
        with self.assertRaises(CameraUnavailable):
            await proxy(lambda _: None, ca_cert="nonexistent-fixture-ca.pem").open()

    async def test_failed_or_cancelled_initial_asgi_send_releases_feed(self):
        from starlette.requests import ClientDisconnect

        for cancelled in [False, True]:
            with self.subTest(cancelled=cancelled):
                stream = Chunks([PART])
                camera = proxy(lambda _: httpx.Response(200, headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"}, stream=stream))
                feed = await camera.open()
                response = CameraStreamingResponse(feed, lambda: True, headers={})
                async def receive():
                    return {"type": "http.disconnect"}
                async def send(_):
                    if cancelled:
                        await asyncio.sleep(30)
                    raise OSError("fixture client disconnected before headers")
                expected = TimeoutError if cancelled else ClientDisconnect
                with self.assertRaises(expected):
                    async with asyncio.timeout(0.02):
                        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
                self.assertEqual(stream.closed, 1)
                self.assertEqual(camera._active, 0)


if __name__ == "__main__":
    unittest.main()
