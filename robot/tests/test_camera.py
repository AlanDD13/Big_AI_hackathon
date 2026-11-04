from __future__ import annotations

import queue
import sys
import unittest
from pathlib import Path
from urllib.request import urlopen
import json

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from camera import (
    CameraHTTPServer,
    EPStreamCapture,
    RoverFrameBuffer,
    poll_frame_once,
)


FAKE_JPEG_A = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
FAKE_JPEG_B = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x02\x00\x02\x00\x00\xff\xd9"


class FakeClock:
    def __init__(self, wall: float = 1793818000.0, mono: float = 100.0) -> None:
        self.wall = wall
        self.mono = mono

    def advance(self, dt: float) -> None:
        self.wall += dt
        self.mono += dt


class FakeRoverCamera:
    def __init__(self, frames: list[object]) -> None:
        self._queue: queue.Queue[object] = queue.Queue()
        for item in frames:
            self._queue.put(item)
        self.started = False
        self.stopped = False

    def start_video_stream(self, display: bool = False, resolution: str = "360p") -> bool:
        self.started = True
        return True

    def read_cv2_image(self, timeout: float = 0.5, strategy: str = "newest"):
        item = self._queue.get(timeout=0.02)
        if isinstance(item, BaseException):
            raise item
        return item

    def stop_video_stream(self) -> bool:
        self.stopped = True
        return True


class FakeRoverSensor:
    def __init__(self, distance_slots: list[int] | None = None) -> None:
        self.distance_slots = distance_slots or [1850, 0, 0, 0]
        self.subscribed = False
        self.unsubscribed = False

    def sub_distance(self, freq: int = 10, callback=None) -> bool:
        self.subscribed = True
        if callback is not None:
            callback(self.distance_slots)
        return True

    def unsub_distance(self) -> bool:
        self.unsubscribed = True
        return True


class FakeRover:
    def __init__(self, frames: list[object], distance_slots: list[int] | None = None) -> None:
        self.camera = FakeRoverCamera(frames)
        self.sensor = FakeRoverSensor(distance_slots)


class CameraTests(unittest.TestCase):
    def test_buffer_session_geometry_and_freshness(self) -> None:
        clock = FakeClock()
        buf = RoverFrameBuffer(
            "rover-1",
            session_id="sess-1",
            max_age_s=1.0,
            wall_clock=lambda: clock.wall,
            monotonic_clock=lambda: clock.mono,
        )

        # Before any frame, must fail closed
        frame, reason = buf.get_fresh_frame()
        self.assertIsNone(frame)
        self.assertIn("No camera frame", reason or "")

        buf.update_distance_slots([1420, 0, 0, 0])
        f0 = buf.put_jpeg_frame(FAKE_JPEG_A, 640, 360)
        self.assertEqual(f0.camera_id, "rover-1")
        self.assertEqual(f0.session_id, "sess-1")
        self.assertEqual(f0.geometry_version, 1)
        self.assertEqual(f0.frame_id, 0)
        self.assertEqual(f0.distance_mm_slot0, 1420)

        # Repeated read within 1.0 s returns identical frame_id and bytes
        clock.advance(0.4)
        cur, err = buf.get_fresh_frame()
        self.assertIsNone(err)
        self.assertEqual(cur, f0)
        self.assertEqual(cur.jpeg_bytes, FAKE_JPEG_A)

        # Same resolution increments frame_id, keeps geometry_version=1
        f1 = buf.put_jpeg_frame(FAKE_JPEG_B, 640, 360)
        self.assertEqual(f1.frame_id, 1)
        self.assertEqual(f1.geometry_version, 1)

        # Resolution change increments geometry_version to 2
        f2 = buf.put_jpeg_frame(FAKE_JPEG_A, 1280, 720)
        self.assertEqual(f2.frame_id, 2)
        self.assertEqual(f2.geometry_version, 2)

        # Past 1.0 s max_age_s -> stale (None)
        clock.advance(1.05)
        stale_frame, stale_reason = buf.get_fresh_frame()
        self.assertIsNone(stale_frame)
        self.assertIn("stale", stale_reason or "")

        # Restarting session changes session_id and resets frame_id/geometry_version
        new_sid = buf.new_session("sess-2")
        self.assertEqual(new_sid, "sess-2")
        f_new = buf.put_jpeg_frame(FAKE_JPEG_A, 640, 360)
        self.assertEqual(f_new.session_id, "sess-2")
        self.assertEqual(f_new.frame_id, 0)
        self.assertEqual(f_new.geometry_version, 1)

    def test_http_server_auth_headers_and_stale_503(self) -> None:
        clock = FakeClock()
        buf = RoverFrameBuffer(
            "rover-1",
            session_id="session-http",
            max_age_s=1.0,
            wall_clock=lambda: clock.wall,
            monotonic_clock=lambda: clock.mono,
        )
        token = "peer-secret-token-123"
        server = CameraHTTPServer(buf, host="127.0.0.1", port=0, peer_token=token)
        base_url = server.start()
        self.addCleanup(server.close)

        # 1. No frame yet -> 503 STALE_FRAME
        r_empty = poll_frame_once(base_url, token)
        self.assertEqual(r_empty["http_status"], 503)
        self.assertEqual(r_empty["error"]["error"]["code"], "STALE_FRAME")

        # 2. Store a BGR frame via OpenCV encode
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        img[:, :] = (30, 120, 200)
        buf.update_distance_slots([1975, 0, 0, 0])
        buf.put_bgr_frame(img)

        # 3. Wrong or missing token -> 401 UNAUTHORIZED
        r_unauth = poll_frame_once(base_url, "wrong-token")
        self.assertEqual(r_unauth["http_status"], 401)
        self.assertEqual(r_unauth["error"]["error"]["code"], "UNAUTHORIZED")

        r_missing = poll_frame_once(base_url, None)
        self.assertEqual(r_missing["http_status"], 401)

        # 4. Valid token -> 200 OK with all 6 required headers + valid JPEG
        r_ok = poll_frame_once(base_url, token)
        self.assertEqual(r_ok["http_status"], 200)
        self.assertTrue(r_ok["is_jpeg"])
        self.assertEqual(r_ok["camera_id"], "rover-1")
        self.assertEqual(r_ok["session_id"], "session-http")
        self.assertEqual(r_ok["geometry_version"], "1")
        self.assertEqual(r_ok["frame_id"], 0)
        self.assertEqual(r_ok["timestamp_basis"], "RECEIVE")
        self.assertEqual(r_ok["distance_mm_slot0"], "1975")

        # Repeated poll before next frame returns exact same frame_id and bytes
        r_repeat = poll_frame_once(base_url, token)
        self.assertEqual(r_repeat["http_status"], 200)
        self.assertEqual(r_repeat["frame_id"], 0)
        self.assertEqual(r_repeat["raw_bytes"], r_ok["raw_bytes"])

        # 5. Public /health endpoint
        with urlopen(base_url + "/health", timeout=2.0) as hresp:
            health = json.loads(hresp.read().decode("utf-8"))
        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["frameFresh"])
        self.assertEqual(health["distanceMmSlot0"], 1975)

        # 6. Advance clock past 1.0 s -> 503 STALE_FRAME (never serves stale HTTP 200)
        clock.advance(1.2)
        r_stale = poll_frame_once(base_url, token)
        self.assertEqual(r_stale["http_status"], 503)
        self.assertEqual(r_stale["error"]["error"]["code"], "STALE_FRAME")

    def test_ep_stream_capture_lifecycle_and_fail_closed_close(self) -> None:
        img1 = np.zeros((360, 640, 3), dtype=np.uint8)
        img2 = np.ones((360, 640, 3), dtype=np.uint8) * 128
        rover = FakeRover([img1, queue.Empty(), img2], distance_slots=[1610, 0, 0, 0])
        buf = RoverFrameBuffer("rover-1", max_age_s=1.0)

        cap = EPStreamCapture(rover, buf, read_timeout_s=0.02)
        session_id = cap.start(wait_first_frame_s=1.0)
        self.assertTrue(session_id.startswith("rovcam-"))
        self.assertTrue(rover.camera.started)
        self.assertTrue(rover.sensor.subscribed)

        frame, err = buf.get_fresh_frame()
        self.assertIsNotNone(frame)
        self.assertIsNone(err)
        self.assertEqual(frame.distance_mm_slot0, 1610)

        cap.close()
        self.assertTrue(rover.camera.stopped)
        self.assertTrue(rover.sensor.unsubscribed)
        after_frame, after_reason = buf.get_fresh_frame()
        self.assertIsNone(after_frame)
        self.assertIn("closed", (after_reason or "").lower())


if __name__ == "__main__":
    unittest.main()
