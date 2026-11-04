"""Optional source adapters; only deployment configuration chooses their sources."""
import math
import sys
import time
from urllib.parse import urlsplit

import httpx

from . import FrameStore

class LocalCapture:
    """Read a configured OpenCV device index or video file one frame at a time."""
    def __init__(self, source, store: FrameStore, camera_id, session_id, kind='FIXED'):
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError('OpenCV is not installed') from exc
        self.cv2 = cv2
        self.capture = (cv2.VideoCapture(source, cv2.CAP_AVFOUNDATION)
                        if sys.platform == 'darwin' and type(source) is int and hasattr(cv2, 'CAP_AVFOUNDATION')
                        else cv2.VideoCapture(source))
        if not self.capture.isOpened():
            self.capture.release()
            raise RuntimeError('Camera source unavailable')
        self.store, self.camera_id, self.session_id, self.kind = store, camera_id, session_id, kind
        self.frame_id, self.geometry, self.size = 0, 0, None

    def read(self):
        ok, image = self.capture.read()
        acquired = time.time()
        if not ok:
            return None
        size = (image.shape[1], image.shape[0])
        if self.size is not None and size != self.size:
            self.geometry += 1
        self.size = size
        ok, encoded = self.cv2.imencode('.jpg', image)
        if not ok:
            raise ValueError('Unable to encode frame')
        frame = self.store.put(self.camera_id, self.session_id, str(self.geometry), self.frame_id, acquired, encoded.tobytes(), kind=self.kind, received_at=acquired, timestamp_basis='RECEIVE')
        self.frame_id += 1
        return frame

    def close(self):
        self.capture.release()

class RoverHTTPAdapter:
    """Pull source-identified frames from one deployment-configured bridge."""
    def __init__(self, base_url, store: FrameStore, camera_id, session_id=None, timeout=3, token=None):
        url = urlsplit(base_url)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.path not in ('', '/') or url.query or url.fragment:
            raise ValueError('Invalid configured bridge URL')
        if token is not None and (not isinstance(token, str) or not token or '\r' in token or '\n' in token):
            raise ValueError('Invalid bridge token')
        self.url = base_url.rstrip('/') + '/camera/frame'
        self.store, self.camera_id = store, camera_id
        self.timeout, self.token = timeout, token
        self._session, self._last_id = None, -1

    def read(self):
        headers = {'Authorization': f'Bearer {self.token}'} if self.token else {}
        with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
            with client.stream('GET', self.url, headers=headers) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError('Bridge frame unavailable')
                required = ('X-Camera-Id', 'X-Session-Id', 'X-Geometry-Version', 'X-Frame-Id', 'X-Captured-At', 'X-Timestamp-Basis')
                values = [response.headers.get(name) for name in required]
                if any(not value for value in values):
                    raise ValueError('Bridge metadata unavailable')
                camera_id, session_id, geometry, raw_id, raw_time, basis = values
                if camera_id != self.camera_id or not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 16 or basis not in ('SOURCE', 'RECEIVE'):
                    raise ValueError('Invalid bridge metadata')
                try:
                    frame_id, captured_at = int(raw_id), float(raw_time)
                except ValueError as exc:
                    raise ValueError('Invalid bridge metadata') from exc
                if not math.isfinite(captured_at) or captured_at < 0 or frame_id > 2**53 - 1:
                    raise ValueError('Invalid bridge metadata')
                if session_id == self._session:
                    if frame_id == self._last_id:
                        return None
                    if frame_id < self._last_id:
                        raise ValueError('Invalid bridge sequence')
                data = bytearray()
                for chunk in response.iter_bytes():
                    data.extend(chunk)
                    if len(data) > self.store.max_image_bytes:
                        raise ValueError('Invalid image')
                received_at = time.time()
        frame = self.store.put(camera_id, session_id, geometry, frame_id, captured_at, bytes(data), kind='ROVER', received_at=received_at, timestamp_basis=basis)
        self._session, self._last_id = session_id, frame_id
        return frame
