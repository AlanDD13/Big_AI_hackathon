"""Preliminary image-space detections and conservative per-camera tracks.

Pixel stationarity does not establish physical stationarity, object eligibility,
or safe graspability. Contact points are bounding-box heuristics.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from itertools import islice
from dataclasses import dataclass
from io import BytesIO
import math
import os
import uuid


@dataclass(frozen=True)
class Detection:
    label: str
    score: float
    bbox: tuple[float, float, float, float]
    contact_point: tuple[float, float] | None = None


@dataclass(frozen=True)
class TrackUpdate:
    track_id: str
    detection: Detection
    motion_state: str
    stationary_for_s: float
    excluded_reason: str | None = None
    new: bool = False


# This list is a conservative screen, not a complete taxonomy of living things.
_LIVING = {'person', 'people', 'human', 'man', 'woman', 'child', 'baby', 'boy', 'girl',
           'dog', 'cat', 'bird', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra',
           'giraffe', 'animal', 'pet', 'rabbit', 'mouse', 'rat', 'deer', 'monkey',
           'goat', 'pig', 'chicken', 'duck', 'fish', 'turtle', 'insect'}


def _living(label):
    return bool(set(label.lower().replace('-', ' ').replace('_', ' ').split()) & _LIVING)


def _valid(d):
    try:
        x1, y1, x2, y2 = d.bbox
        return (isinstance(d.label, str) and bool(d.label.strip()) and
                math.isfinite(d.score) and 0 <= d.score <= 1 and
                all(math.isfinite(v) for v in d.bbox) and 0 <= x1 < x2 and 0 <= y1 < y2 and
                (d.contact_point is None or
                 (len(d.contact_point) == 2 and all(math.isfinite(v) for v in d.contact_point))))
    except (TypeError, ValueError, AttributeError):
        return False


def _overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _iou(a, b):
    w = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = w * h
    return intersection / ((a[2]-a[0])*(a[3]-a[1]) + (b[2]-b[0])*(b[3]-b[1]) - intersection)


class Tracker:
    def __init__(self, stationary_seconds=5, max_gap_seconds=1, max_cameras=64, max_detections=512):
        if not math.isfinite(stationary_seconds) or stationary_seconds <= 0 or not math.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
            raise ValueError('time thresholds must be positive and finite')
        if type(max_cameras) is not int or not 1 <= max_cameras <= 1024 or type(max_detections) is not int or not 1 <= max_detections <= 4096:
            raise ValueError('invalid capacity')
        self.max_cameras = max_cameras
        self.max_detections = max_detections
        self.stationary_seconds = stationary_seconds
        self.max_gap_seconds = max_gap_seconds
        self._cameras = OrderedDict()

    def reset_camera(self, camera_id):
        self._cameras.pop(camera_id, None)

    def observe(self, camera_id, session_id, geometry_version, frame_id, now, detections, source_kind='FIXED'):
        if not math.isfinite(now):
            self.reset_camera(camera_id)
            raise ValueError('timestamp must be finite')
        camera = self._cameras.get(camera_id)
        context = (session_id, geometry_version, source_kind)
        if camera is None or camera['context'] != context:
            camera = {'context': context, 'tracks': {}, 'time': None,
                      'frame': None}
            self._cameras[camera_id] = camera
            if len(self._cameras) > self.max_cameras:
                self._cameras.popitem(last=False)
        self._cameras.move_to_end(camera_id)
        # Replayed frames and timestamps cannot advance, or restore, a track.
        if (type(frame_id) is not int or frame_id < 0 or
                (camera['time'] is not None and now <= camera['time']) or
                (camera['frame'] is not None and frame_id <= camera['frame'])):
            camera['tracks'] = {}
            return []
        camera['frame'] = frame_id
        camera['time'] = now
        old = {k: v for k, v in camera['tracks'].items() if 0 < now - v['time'] <= self.max_gap_seconds}
        # Fail closed rather than truncating a batch that may contain a person.
        detections = list(islice(detections, self.max_detections + 1))
        if len(detections) > self.max_detections:
            camera['tracks'] = {}
            return []
        detections = [d for d in detections if _valid(d)]
        candidates = [[key for key, track in old.items() if track['detection'].label.lower() == d.label.lower() and _iou(track['detection'].bbox, d.bbox) >= .3] for d in detections]
        counts = Counter(key for group in candidates for key in group)
        updates, current = [], {}
        living = [d for d in detections if _living(d.label)]
        for d, options in zip(detections, candidates):
            key = options[0] if len(options) == 1 and counts[options[0]] == 1 else None
            new = key is None
            if new:
                key = uuid.uuid4().hex
                track = {'anchor': d.bbox, 'start': now, 'count': 1}
            else:
                track = old[key].copy()
                track['count'] += 1
            reason = 'living_subject' if _living(d.label) else ('overlaps_living_subject' if any(_overlap(d.bbox, subject.bbox) for subject in living) else None)
            anchor = track['anchor']
            tolerance = max(4.0, .05 * math.hypot(anchor[2]-anchor[0], anchor[3]-anchor[1]))
            moved = (math.hypot(anchor[0] - d.bbox[0], anchor[1] - d.bbox[1]) > tolerance or
                     math.hypot(anchor[2] - d.bbox[2], anchor[3] - d.bbox[3]) > tolerance)
            if moved or reason or source_kind != 'FIXED' or track.get('excluded'):
                track.update(anchor=d.bbox, start=now, count=1)
            elapsed = max(0.0, now - track['start'])
            if reason:
                state = 'EXCLUDED'
            elif source_kind != 'FIXED':
                state = 'UNKNOWN'
            elif moved:
                state = 'MOVING'
            elif elapsed >= self.stationary_seconds and track['count'] >= 10:
                state = 'STATIONARY'
            else:
                state = 'OBSERVING'
            track.update(detection=d, time=now, excluded=bool(reason))
            current[key] = track
            updates.append(TrackUpdate(key, d, state, elapsed, reason, new))
        camera.update(tracks=current)
        return updates


def resolve_device(requested: str | None = 'auto') -> str:
    """Resolve an inference device across Windows/Linux (CUDA/CPU) and macOS (MPS/CPU)."""
    req = (requested or 'auto').strip().lower()
    if req not in ('', 'auto', 'mps'):
        return req
    try:
        import torch
        if req == 'mps':
            if getattr(torch.backends, 'mps', None) is not None and torch.backends.mps.is_available():
                return 'mps'
        if torch.cuda.is_available():
            return 'cuda'
        if getattr(torch.backends, 'mps', None) is not None and torch.backends.mps.is_available():
            return 'mps'
    except Exception:
        pass
    return 'cpu'


_BACKGROUND = {'wall', 'floor', 'ceiling', 'table', 'desk', 'door', 'window', 'room', 'carpet', 'rug',
               'furniture', 'shelf', 'cabinet', 'curtain', 'sofa', 'couch', 'bed', 'chair', 'television',
               'tv', 'monitor', 'screen', 'laptop', 'computer', 'keyboard', 'building', 'tile', 'ground'}


def _env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _background(label):
    return bool(set(label.lower().replace('-', ' ').replace('_', ' ').split()) & _BACKGROUND)


def curate(detections, width, height, min_confidence, min_area_frac, max_area_frac, max_objects):
    """Thin out candidate objects. Living detections are never dropped, because they
    exclude overlapping objects and must stay visible to the safety filter."""
    kept, objects = [], []
    area = float(width * height) or 1.0
    for d in detections:
        if _living(d.label):
            kept.append(d)
            continue
        frac = (d.bbox[2] - d.bbox[0]) * (d.bbox[3] - d.bbox[1]) / area
        if (d.score >= min_confidence and min_area_frac <= frac <= max_area_frac
                and not _background(d.label)):
            objects.append(d)
    objects.sort(key=lambda d: d.score, reverse=True)
    chosen = []
    for d in objects:  # class-agnostic suppression of near-duplicate boxes
        if all(_iou(d.bbox, c.bbox) < .5 for c in chosen):
            chosen.append(d)
        if len(chosen) >= max_objects:
            break
    return kept + chosen


class YoloDetector:
    def __init__(self, model='yoloe-26s-seg-pf.pt', device='mps', confidence=.25,
                 min_confidence=None, min_area_frac=None, max_area_frac=None, max_objects=None):
        self.model_name = model
        self.device = resolve_device(device)
        self.confidence = confidence
        self.min_confidence = _env_float('VISION_MIN_CONFIDENCE', .45) if min_confidence is None else min_confidence
        self.min_area_frac = _env_float('VISION_MIN_AREA_FRAC', .002) if min_area_frac is None else min_area_frac
        self.max_area_frac = _env_float('VISION_MAX_AREA_FRAC', .4) if max_area_frac is None else max_area_frac
        self.max_objects = int(_env_float('VISION_MAX_OBJECTS', 6)) if max_objects is None else max_objects
        self._model = None

    def detect(self, jpeg: bytes) -> list[Detection]:
        from PIL import Image
        if self._model is None:
            from ultralytics import YOLOE
            self._model = YOLOE(self.model_name)
        with Image.open(BytesIO(jpeg)) as image:
            image = image.convert('RGB')
            size = image.size
            results = self._model.predict(source=image, device=self.device, conf=self.confidence, verbose=False)
        detections = []
        for result in results:
            for box in result.boxes:
                coords = tuple(float(v) for v in box.xyxy[0].tolist())
                label = result.names[int(box.cls[0])]
                d = Detection(label, float(box.conf[0]), coords, ((coords[0]+coords[2])/2, coords[3]))
                if _valid(d):
                    detections.append(d)
        return curate(detections, size[0], size[1], self.min_confidence, self.min_area_frac,
                      self.max_area_frac, self.max_objects)
