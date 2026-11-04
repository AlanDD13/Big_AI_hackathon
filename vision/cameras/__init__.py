"""Validated camera frames and bounded, in-memory retention."""
from collections import OrderedDict
from dataclasses import dataclass
import io
import math
import re
import threading
import time
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

_ID = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
_MAX_PIXELS = 16_000_000
_MAX_DIMENSION = 8192

@dataclass(frozen=True, slots=True)
class CameraFrame:
    camera_id: str
    session_id: str
    geometry_version: str
    frame_id: int
    captured_at: float
    received_at: float
    jpeg: bytes
    width: int
    height: int
    kind: str = 'FIXED'
    timestamp_basis: str = 'SOURCE'

class FrameStore:
    def __init__(self, max_cameras=16, max_bytes=64 * 1024 * 1024, ttl_s=60, max_image_bytes=4 * 1024 * 1024, clock=None):
        if any(not isinstance(n, (int, float)) or isinstance(n, bool) or not math.isfinite(n) or n <= 0 for n in (max_cameras, max_bytes, ttl_s, max_image_bytes)):
            raise ValueError('Invalid limits')
        self.max_cameras, self.max_bytes, self.ttl_s, self.max_image_bytes = max_cameras, max_bytes, ttl_s, max_image_bytes
        if clock is not None and not callable(clock):
            raise ValueError('Invalid clock')
        self._clock = clock if clock is not None else time.time
        self._frames = OrderedDict()
        self._states = {}
        self._bytes = 0
        self._lock = threading.RLock()

    def _now(self):
        return self._clock()

    def _prune(self, now):
        for key, frame in list(self._frames.items()):
            if now - frame.received_at > self.ttl_s:
                self._bytes -= len(frame.jpeg)
                del self._frames[key]

    def put(self, camera_id, session_id, geometry_version, frame_id, captured_at, jpeg, kind='FIXED', received_at=None, timestamp_basis='SOURCE'):
        if not all(isinstance(s, str) and _ID.fullmatch(s) for s in (camera_id, session_id, geometry_version)):
            raise ValueError('Invalid identifier')
        if timestamp_basis not in ('SOURCE', 'RECEIVE') or kind not in ('FIXED', 'ROVER') or type(frame_id) is not int or not 0 <= frame_id <= 2**53 - 1:
            raise ValueError('Invalid frame metadata')
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 for x in (captured_at, received_at if received_at is not None else 0)):
            raise ValueError('Invalid timestamp')
        if not isinstance(jpeg, bytes) or not jpeg or len(jpeg) > self.max_image_bytes:
            raise ValueError('Invalid image')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(jpeg)) as image:
                    if image.format != 'JPEG' or image.width > _MAX_DIMENSION or image.height > _MAX_DIMENSION or image.width * image.height > _MAX_PIXELS:
                        raise ValueError('Invalid image')
                    image = ImageOps.exif_transpose(image).convert('RGB')
                    width, height = image.size
                    out = io.BytesIO()
                    image.save(out, format='JPEG', quality=90)
                    canonical = out.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning, SyntaxError) as exc:
            raise ValueError('Invalid image') from exc
        if len(canonical) > self.max_image_bytes or len(canonical) > self.max_bytes:
            raise ValueError('Invalid image')
        now = self._now() if received_at is None else received_at
        with self._lock:
            state = self._states.get(camera_id)
            if state is None:
                if len(self._states) >= self.max_cameras or (frame_id != 0 and kind != 'ROVER'):
                    raise ValueError('Invalid camera or session')
                state = {'session': session_id, 'kind': kind, 'last': -1, 'retired': set(), 'geometry': geometry_version, 'size': (width, height), 'old_geometry': set()}
            else:
                if kind != state['kind'] or session_id in state['retired']:
                    raise ValueError('Invalid camera or session')
                if session_id != state['session']:
                    if (frame_id != 0 and kind != 'ROVER') or len(state['retired']) >= 64:
                        raise ValueError('Invalid camera or session')
                    state = dict(state, session=session_id, last=-1, retired=state['retired'] | {state['session']}, geometry=geometry_version, size=(width, height), old_geometry=set())
                elif frame_id <= state['last']:
                    raise ValueError('Invalid frame sequence')
                elif geometry_version == state['geometry'] and (width, height) != state['size']:
                    raise ValueError('Geometry changed')
                elif geometry_version != state['geometry']:
                    if geometry_version in state['old_geometry'] or len(state['old_geometry']) >= 64:
                        raise ValueError('Invalid geometry')
                    state = dict(state, geometry=geometry_version, size=(width, height), old_geometry=state['old_geometry'] | {state['geometry']})
            frame = CameraFrame(camera_id, session_id, geometry_version, frame_id, captured_at, now, canonical, width, height, kind, timestamp_basis)
            state['last'] = frame_id
            self._states[camera_id] = state
            self._prune(now)
            key = (camera_id, session_id, frame_id)
            self._frames[key] = frame
            self._bytes += len(canonical)
            while self._bytes > self.max_bytes:
                _, old = self._frames.popitem(last=False)
                self._bytes -= len(old.jpeg)
            return frame

    def latest(self, camera_id):
        with self._lock:
            self._prune(self._now())
            return next((frame for frame in reversed(self._frames.values()) if frame.camera_id == camera_id and frame.session_id == self._states[camera_id]['session']), None)

    def get(self, camera_id, session_id, frame_id):
        with self._lock:
            self._prune(self._now())
            return self._frames.get((camera_id, session_id, frame_id))

    def list_cameras(self):
        with self._lock:
            self._prune(self._now())
            return [{'camera_id': camera, 'kind': state['kind'], 'session_id': state['session'], 'latest_frame_id': state['last']} for camera, state in self._states.items()]
