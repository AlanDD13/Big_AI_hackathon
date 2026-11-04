"""Rover camera capture, TOF sensor pairing, and GET /camera/frame HTTP service.

Implements the P4 camera side of contracts/robot-simulator-v1.md and
contracts/vision-v1.md so vision/cameras/adapters.py (RoverHTTPAdapter) can
pull live, source-identified JPEG frames with strict freshness guarantees.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import queue
import re
import secrets
import sys
import threading
import time
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DEFAULT_MAX_FRAME_AGE_S = 1.0
DEFAULT_JPEG_QUALITY = 85


@dataclass(frozen=True)
class CapturedFrame:
    camera_id: str
    session_id: str
    geometry_version: int
    frame_id: int
    captured_at: float
    monotonic_at: float
    width: int
    height: int
    jpeg_bytes: bytes
    timestamp_basis: str = "RECEIVE"
    distance_mm_slot0: int | None = None

    def response_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "image/jpeg",
            "Content-Length": str(len(self.jpeg_bytes)),
            "Cache-Control": "no-store",
            "X-Camera-Id": self.camera_id,
            "X-Session-Id": self.session_id,
            "X-Geometry-Version": str(self.geometry_version),
            "X-Frame-Id": str(self.frame_id),
            "X-Captured-At": f"{self.captured_at:.6f}",
            "X-Timestamp-Basis": self.timestamp_basis,
        }
        if self.distance_mm_slot0 is not None:
            headers["X-Distance-Mm-Slot0"] = str(self.distance_mm_slot0)
        return headers


class RoverFrameBuffer:
    """Thread-safe latest-frame store with session, geometry, and freshness tracking."""

    def __init__(
        self,
        camera_id: str = "rover-1",
        *,
        session_id: str | None = None,
        max_age_s: float = DEFAULT_MAX_FRAME_AGE_S,
        jpeg_quality: int = DEFAULT_JPEG_QUALITY,
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(camera_id, str) or not ID_RE.match(camera_id):
            raise ValueError(f"Invalid camera_id: {camera_id!r}")
        if not math.isfinite(max_age_s) or max_age_s <= 0:
            raise ValueError("max_age_s must be positive and finite")
        if not isinstance(jpeg_quality, int) or not (1 <= jpeg_quality <= 100):
            raise ValueError("jpeg_quality must be an int in 1..100")

        self.camera_id = camera_id
        self.max_age_s = float(max_age_s)
        self.jpeg_quality = jpeg_quality
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._lock = threading.Lock()

        self._session_id = ""
        self._geometry_version = 1
        self._next_frame_id = 0
        self._last_size: tuple[int, int] | None = None
        self._latest: CapturedFrame | None = None
        self._stream_active = False
        self._last_error: str | None = "Camera stream not started"
        self._distance_slots_mm: tuple[int, int, int, int] | None = None
        self._distance_updated_at: float | None = None
        self.new_session(session_id)

    def new_session(self, session_id: str | None = None) -> str:
        """Start or restart a camera capture session with a fresh session ID."""
        if session_id is None:
            session_id = f"rovcam-{secrets.token_hex(8)}"
        if not isinstance(session_id, str) or not ID_RE.match(session_id):
            raise ValueError(f"Invalid session_id: {session_id!r}")
        with self._lock:
            self._session_id = session_id
            self._geometry_version = 1
            self._next_frame_id = 0
            self._last_size = None
            self._latest = None
            self._stream_active = True
            self._last_error = None
            return self._session_id

    def mark_unavailable(self, reason: str = "Camera stream stopped") -> None:
        """Mark the camera stream inactive so subsequent reads fail closed with 503."""
        with self._lock:
            self._stream_active = False
            self._last_error = reason

    def update_distance_slots(
        self,
        slots_mm: tuple[int, int, int, int] | list[int],
        *,
        monotonic_at: float | None = None,
    ) -> None:
        """Record latest 4-slot infrared TOF distance readings in millimetres."""
        if len(slots_mm) < 4:
            return
        parsed = (int(slots_mm[0]), int(slots_mm[1]), int(slots_mm[2]), int(slots_mm[3]))
        now_mono = self._monotonic_clock() if monotonic_at is None else float(monotonic_at)
        with self._lock:
            self._distance_slots_mm = parsed
            self._distance_updated_at = now_mono

    def put_jpeg_frame(
        self,
        jpeg_bytes: bytes,
        width: int,
        height: int,
        *,
        captured_at: float | None = None,
        monotonic_at: float | None = None,
        timestamp_basis: str = "RECEIVE",
        distance_mm_slot0: int | None = None,
    ) -> CapturedFrame:
        if not isinstance(jpeg_bytes, (bytes, bytearray)) or len(jpeg_bytes) < 4:
            raise ValueError("jpeg_bytes must be non-empty JPEG data")
        if jpeg_bytes[0] != 0xFF or jpeg_bytes[1] != 0xD8:
            raise ValueError("jpeg_bytes missing JPEG SOI marker")
        if width <= 0 or height <= 0:
            raise ValueError("width and height must be positive")
        if timestamp_basis not in ("SOURCE", "RECEIVE"):
            raise ValueError("timestamp_basis must be SOURCE or RECEIVE")

        cap_time = self._wall_clock() if captured_at is None else float(captured_at)
        mono_time = self._monotonic_clock() if monotonic_at is None else float(monotonic_at)
        if not math.isfinite(cap_time) or cap_time < 0:
            raise ValueError("captured_at must be non-negative and finite")
        if not math.isfinite(mono_time):
            raise ValueError("monotonic_at must be finite")

        with self._lock:
            size = (int(width), int(height))
            if self._last_size is not None and size != self._last_size:
                self._geometry_version += 1
            self._last_size = size
            if distance_mm_slot0 is None and self._distance_slots_mm is not None:
                distance_mm_slot0 = self._distance_slots_mm[0]
            frame = CapturedFrame(
                camera_id=self.camera_id,
                session_id=self._session_id,
                geometry_version=self._geometry_version,
                frame_id=self._next_frame_id,
                captured_at=cap_time,
                monotonic_at=mono_time,
                width=size[0],
                height=size[1],
                jpeg_bytes=bytes(jpeg_bytes),
                timestamp_basis=timestamp_basis,
                distance_mm_slot0=distance_mm_slot0,
            )
            self._next_frame_id += 1
            self._latest = frame
            self._stream_active = True
            self._last_error = None
            return frame

    def put_bgr_frame(
        self,
        image,
        *,
        captured_at: float | None = None,
        monotonic_at: float | None = None,
        distance_mm_slot0: int | None = None,
    ) -> CapturedFrame:
        """Encode a BGR numpy array from OpenCV/SDK into JPEG and store it."""
        import cv2

        if image is None or not hasattr(image, "shape") or len(image.shape) < 2:
            raise ValueError("Invalid BGR image array")
        height, width = int(image.shape[0]), int(image.shape[1])
        if width <= 0 or height <= 0:
            raise ValueError("Empty BGR image array")
        cap_time = self._wall_clock() if captured_at is None else float(captured_at)
        mono_time = self._monotonic_clock() if monotonic_at is None else float(monotonic_at)
        ok, encoded = cv2.imencode(
            ".jpg",
            image,
            [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
        )
        if not ok:
            raise ValueError("cv2.imencode failed to encode BGR frame")
        return self.put_jpeg_frame(
            encoded.tobytes(),
            width,
            height,
            captured_at=cap_time,
            monotonic_at=mono_time,
            timestamp_basis="RECEIVE",
            distance_mm_slot0=distance_mm_slot0,
        )

    def get_fresh_frame(
        self,
        *,
        now_monotonic: float | None = None,
    ) -> tuple[CapturedFrame | None, str | None]:
        """Return (frame, None) if a fresh frame exists, or (None, reason) for 503."""
        now_mono = self._monotonic_clock() if now_monotonic is None else float(now_monotonic)
        with self._lock:
            if not self._stream_active:
                return None, self._last_error or "Camera stream is not active"
            frame = self._latest
            if frame is None:
                return None, self._last_error or "No camera frame captured yet"
            age_s = now_mono - frame.monotonic_at
            if age_s < -0.001 or age_s > self.max_age_s:
                return None, f"Newest frame is stale ({age_s:.3f}s > {self.max_age_s:.3f}s)"
            return frame, None

    def health_snapshot(self, *, now_monotonic: float | None = None) -> dict[str, object]:
        now_mono = self._monotonic_clock() if now_monotonic is None else float(now_monotonic)
        with self._lock:
            frame = self._latest
            age_s = (now_mono - frame.monotonic_at) if frame is not None else None
            fresh = bool(
                self._stream_active
                and frame is not None
                and age_s is not None
                and -0.001 <= age_s <= self.max_age_s
            )
            dist_age_s = (
                (now_mono - self._distance_updated_at)
                if self._distance_updated_at is not None
                else None
            )
            return {
                "status": "ok" if fresh else "degraded",
                "cameraId": self.camera_id,
                "sessionId": self._session_id,
                "geometryVersion": self._geometry_version,
                "frameId": frame.frame_id if frame is not None else None,
                "frameFresh": fresh,
                "frameAgeMs": round(age_s * 1000.0, 1) if age_s is not None else None,
                "resolution": [frame.width, frame.height] if frame is not None else None,
                "streamActive": self._stream_active,
                "lastError": self._last_error,
                "distanceMmSlots": list(self._distance_slots_mm) if self._distance_slots_mm else None,
                "distanceMmSlot0": self._distance_slots_mm[0] if self._distance_slots_mm else None,
                "distanceAgeMs": round(dist_age_s * 1000.0, 1) if dist_age_s is not None else None,
            }


class EPStreamCapture:
    """Background worker that reads EP Core camera frames and TOF distance telemetry."""

    def __init__(
        self,
        rover,
        buffer: RoverFrameBuffer,
        *,
        resolution: str = "360p",
        subscribe_distance: bool = True,
        distance_freq: int = 10,
        read_timeout_s: float = 0.5,
    ) -> None:
        self.rover = rover
        self.buffer = buffer
        self.resolution = resolution
        self.subscribe_distance = subscribe_distance
        self.distance_freq = distance_freq
        self.read_timeout_s = read_timeout_s
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._distance_subscribed = False
        self._video_started = False
        self.frames_captured = 0
        self.read_timeouts = 0

    def _on_distance(self, msg) -> None:
        try:
            self.buffer.update_distance_slots(msg)
        except Exception:
            pass

    def start(self, *, wait_first_frame_s: float = 5.0) -> str:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("EPStreamCapture is already running")
        self._stop_event.clear()
        session_id = self.buffer.new_session()

        if self.subscribe_distance:
            try:
                if self.rover.sensor.sub_distance(freq=self.distance_freq, callback=self._on_distance):
                    self._distance_subscribed = True
            except Exception:
                self._distance_subscribed = False

        if not self.rover.camera.start_video_stream(display=False, resolution=self.resolution):
            self.buffer.mark_unavailable("Failed to start rover video stream")
            raise RuntimeError("rover.camera.start_video_stream returned False")
        self._video_started = True

        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"ep-camera-{self.buffer.camera_id}",
            daemon=True,
        )
        self._thread.start()

        if wait_first_frame_s > 0:
            deadline = time.monotonic() + wait_first_frame_s
            while time.monotonic() < deadline:
                frame, _ = self.buffer.get_fresh_frame()
                if frame is not None:
                    break
                if not self._thread.is_alive():
                    break
                time.sleep(0.03)
            else:
                self.close()
                raise RuntimeError(f"Timed out waiting {wait_first_frame_s}s for first rover camera frame")
        return session_id

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                image = self.rover.camera.read_cv2_image(
                    timeout=self.read_timeout_s,
                    strategy="newest",
                )
            except queue.Empty:
                self.read_timeouts += 1
                continue
            except Exception as exc:
                if not self._stop_event.is_set():
                    self.buffer.mark_unavailable(f"Camera read failed: {exc}")
                break

            if image is None or not getattr(image, "size", 0):
                self.read_timeouts += 1
                continue

            if self._stop_event.is_set():
                break

            try:
                self.buffer.put_bgr_frame(image)
                self.frames_captured += 1
            except Exception as exc:
                self.buffer.mark_unavailable(f"Frame encode failed: {exc}")

    def close(self) -> None:
        self._stop_event.set()
        self.buffer.mark_unavailable("Camera stream closed")
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self.buffer.mark_unavailable("Camera stream closed")
        if self._video_started:
            try:
                self.rover.camera.stop_video_stream()
            except Exception:
                pass
            self._video_started = False
        if self._distance_subscribed:
            try:
                self.rover.sensor.unsub_distance()
            except Exception:
                pass
            self._distance_subscribed = False


def verify_bearer_header(auth_header: str | None, expected_token: str | None) -> tuple[bool, int, str, str]:
    """Validate Authorization: Bearer <PEER_TOKEN> in constant time."""
    if not expected_token:
        return False, 503, "AUTH_NOT_CONFIGURED", "Server PEER_TOKEN is not configured"
    if not auth_header or not auth_header.startswith("Bearer "):
        return False, 401, "UNAUTHORIZED", "Valid bearer token required"
    provided = auth_header[len("Bearer "):]
    if not provided or not hmac.compare_digest(provided.encode("utf-8"), expected_token.encode("utf-8")):
        return False, 401, "UNAUTHORIZED", "Valid bearer token required"
    return True, 200, "", ""


class CameraHTTPServer:
    """Threaded HTTP server exposing GET /camera/frame and GET /health."""

    def __init__(
        self,
        buffer: RoverFrameBuffer,
        *,
        host: str = "127.0.0.1",
        port: int = 8004,
        peer_token: str | None = None,
    ) -> None:
        self.buffer = buffer
        self.host = host
        self.port = port
        self.peer_token = peer_token if peer_token is not None else os.getenv("PEER_TOKEN")
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _make_handler(self):
        parent = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args) -> None:
                pass

            def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path == "/health":
                    self._send_json(200, parent.buffer.health_snapshot())
                    return

                if path == "/camera/frame":
                    ok, status, code, msg = verify_bearer_header(
                        self.headers.get("Authorization"),
                        parent.peer_token,
                    )
                    if not ok:
                        self._send_json(status, {"error": {"code": code, "message": msg}})
                        return

                    frame, reason = parent.buffer.get_fresh_frame()
                    if frame is None:
                        self._send_json(
                            503,
                            {
                                "error": {
                                    "code": "STALE_FRAME",
                                    "message": reason or "Camera frame unavailable",
                                }
                            },
                        )
                        return

                    self.send_response(200)
                    for header_name, header_val in frame.response_headers().items():
                        self.send_header(header_name, header_val)
                    self.end_headers()
                    self.wfile.write(frame.jpeg_bytes)
                    return

                self._send_json(404, {"error": {"code": "NOT_FOUND", "message": f"Unknown route {path}"}})

        return Handler

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            return f"http://{self.host}:{self.port}"
        addr, bound_port = self._httpd.server_address[:2]
        return f"http://{addr}:{bound_port}"

    def start(self) -> str:
        if self._httpd is not None:
            raise RuntimeError("CameraHTTPServer already started")
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="rover-camera-http",
            daemon=True,
        )
        self._thread.start()
        return self.base_url

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None


def poll_frame_once(base_url: str, peer_token: str | None, timeout_s: float = 3.0) -> dict[str, object]:
    """Fetch GET /camera/frame once and validate all contract headers and JPEG SOI."""
    url = base_url.rstrip("/") + "/camera/frame"
    headers = {"Authorization": f"Bearer {peer_token}"} if peer_token else {}
    req = Request(url, headers=headers, method="GET")
    t0 = time.monotonic()
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
            latency_ms = round((time.monotonic() - t0) * 1000.0, 2)
            required = (
                "X-Camera-Id",
                "X-Session-Id",
                "X-Geometry-Version",
                "X-Frame-Id",
                "X-Captured-At",
                "X-Timestamp-Basis",
            )
            meta = {name: resp.headers.get(name) for name in required}
            if any(not v for v in meta.values()):
                raise ValueError(f"Missing required headers: {meta}")
            frame_id = int(meta["X-Frame-Id"])
            captured_at = float(meta["X-Captured-At"])
            is_jpeg = len(body) >= 4 and body[0] == 0xFF and body[1] == 0xD8
            return {
                "http_status": resp.status,
                "latency_ms": latency_ms,
                "bytes_len": len(body),
                "is_jpeg": is_jpeg,
                "camera_id": meta["X-Camera-Id"],
                "session_id": meta["X-Session-Id"],
                "geometry_version": meta["X-Geometry-Version"],
                "frame_id": frame_id,
                "captured_at": captured_at,
                "timestamp_basis": meta["X-Timestamp-Basis"],
                "distance_mm_slot0": resp.headers.get("X-Distance-Mm-Slot0"),
                "raw_bytes": body,
            }
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_body)
        except Exception:
            parsed = {"raw": err_body}
        return {
            "http_status": exc.code,
            "error": parsed,
        }


def run_live_camera_check(
    sn: str,
    *,
    camera_id: str = "rover-1",
    resolution: str = "360p",
    duration_s: float = 4.0,
) -> int:
    """Connect to the physical EP Core, stream frames + TOF distance, and verify GET /camera/frame."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_connection import rover_session

    out_dir = Path(__file__).resolve().parent / ".local" / "camera-check"
    out_dir.mkdir(parents=True, exist_ok=True)
    test_token = os.getenv("PEER_TOKEN") or f"ep-cam-test-{secrets.token_hex(8)}"

    buffer = RoverFrameBuffer(camera_id=camera_id, max_age_s=1.0)
    server = CameraHTTPServer(buffer, host="127.0.0.1", port=0, peer_token=test_token)
    base_url = server.start()

    report: dict[str, object] = {
        "serial": sn,
        "camera_id": camera_id,
        "resolution": resolution,
        "status": "FAILED",
    }

    try:
        # 1. Verify 503 STALE_FRAME before stream starts
        pre_stream = poll_frame_once(base_url, test_token)
        # 2. Verify 401 UNAUTHORIZED with wrong token
        unauth = poll_frame_once(base_url, "wrong-token")

        with rover_session(sn) as (rover, identity):
            report["identity"] = identity
            capture = EPStreamCapture(rover, buffer, resolution=resolution, subscribe_distance=True)
            try:
                session_id = capture.start(wait_first_frame_s=5.0)
                report["session_id"] = session_id

                polled_frames = []
                last_jpeg = b""
                t_start = time.monotonic()
                while time.monotonic() - t_start < duration_s:
                    res = poll_frame_once(base_url, test_token)
                    if res.get("http_status") == 200:
                        last_jpeg = res.pop("raw_bytes", b"")
                        polled_frames.append(res)
                    time.sleep(0.15)

                health_during = buffer.health_snapshot()
                total_captured = capture.frames_captured
                elapsed_s = max(0.001, time.monotonic() - t_start)
            finally:
                capture.close()

            # 3. Verify 503 STALE_FRAME immediately after stream closes
            post_close = poll_frame_once(base_url, test_token)

        if last_jpeg:
            snap_file = out_dir / "latest_rover_frame.jpg"
            snap_file.write_bytes(last_jpeg)
            report["saved_jpeg"] = str(snap_file)

        frame_ids = [f["frame_id"] for f in polled_frames]
        monotonic_ids = frame_ids == sorted(frame_ids) and len(set(frame_ids)) >= 2
        all_jpeg = bool(polled_frames) and all(f["is_jpeg"] for f in polled_frames)

        report["checks"] = {
            "pre_stream_503": pre_stream.get("http_status") == 503,
            "unauthorized_401": unauth.get("http_status") == 401,
            "frames_polled_200": len(polled_frames),
            "frames_captured_total": total_captured,
            "approx_capture_fps": round(total_captured / elapsed_s, 1),
            "monotonic_frame_ids": monotonic_ids,
            "first_frame_id": frame_ids[0] if frame_ids else None,
            "last_frame_id": frame_ids[-1] if frame_ids else None,
            "all_valid_jpeg": all_jpeg,
            "post_close_503": post_close.get("http_status") == 503,
        }
        report["health_during_stream"] = health_during
        report["sample_frames"] = polled_frames[:3] + (polled_frames[-2:] if len(polled_frames) > 3 else [])

        if (
            report["checks"]["pre_stream_503"]
            and report["checks"]["unauthorized_401"]
            and monotonic_ids
            and all_jpeg
            and report["checks"]["post_close_503"]
        ):
            report["status"] = "PASSED"
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        server.close()
        (out_dir / "camera_check_report.json").write_text(json.dumps(report, indent=2))

    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["status"] == "PASSED" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sn", required=True, help="14-character robot serial number")
    parser.add_argument("--camera-id", default="rover-1")
    parser.add_argument("--resolution", choices=("360p", "720p"), default="360p")
    parser.add_argument("--duration", type=float, default=4.0, help="Stream check duration in seconds")
    parser.add_argument("--serve", action="store_true", help="Serve GET /camera/frame continuously until interrupted")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8004)
    args = parser.parse_args()

    if not args.serve:
        return run_live_camera_check(
            args.sn,
            camera_id=args.camera_id,
            resolution=args.resolution,
            duration_s=args.duration,
        )

    peer_token = os.getenv("PEER_TOKEN")
    if not peer_token:
        raise SystemExit("PEER_TOKEN environment variable is required with --serve")

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from check_connection import rover_session

    buffer = RoverFrameBuffer(camera_id=args.camera_id, max_age_s=1.0)
    server = CameraHTTPServer(buffer, host=args.host, port=args.port, peer_token=peer_token)
    base_url = server.start()
    try:
        with rover_session(args.sn) as (rover, identity):
            capture = EPStreamCapture(rover, buffer, resolution=args.resolution, subscribe_distance=True)
            try:
                session_id = capture.start(wait_first_frame_s=5.0)
                print(
                    json.dumps(
                        {
                            "status": "SERVING",
                            "baseUrl": base_url,
                            "cameraId": args.camera_id,
                            "sessionId": session_id,
                            "identity": identity,
                        }
                    ),
                    flush=True,
                )
                while True:
                    time.sleep(0.5)
            except KeyboardInterrupt:
                pass
            finally:
                capture.close()
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
