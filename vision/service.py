"""Perception orchestration. All eligibility is evidence based and revocable."""
from __future__ import annotations

import asyncio
from collections import deque, OrderedDict
from dataclasses import dataclass, field
from io import BytesIO
import math
import time
import uuid

from PIL import Image, ImageDraw

from vision.cameras import FrameStore
from vision.camera_recovery import CameraRecovery, RouteBinding
from vision.classification import build_classifier_from_env
from vision.models import Event, MappingEvidence
from vision.perception import Tracker, YoloDetector
from vision.settings import Settings


@dataclass
class ObjectState:
    object_id: str
    camera_id: str
    session_id: str
    geometry_version: str
    track_id: str
    kind: str
    frame_id: int
    captured_at: float
    last_seen: float
    box: tuple
    label: str
    score: float
    pixel_static_s: float = 0
    motion_state: str = "OBSERVING"
    status: str = "OBSERVING"
    excluded_reason: str | None = None
    revision: int = 0
    entity_version: int = 0
    eligibility_reason: str | None = "NOT_ELIGIBLE"
    classification: dict | None = None
    classification_status: str = "PENDING"
    classification_attempt: int = -1
    classification_provider: str | None = None
    classification_model: str | None = None
    visual_pickup: dict | None = None
    visual_pickup_status: str = "PENDING"
    mapping: dict | None = None
    metric_anchor: tuple | None = None
    metric_start: float | None = None
    metric_last: float | None = None
    metric_count: int = 0
    metric_static_s: float = 0
    metric_last_frame: int = -1
    potential_target: bool = False
    # Set when alert.potential_target is published; cleared only by a real revocation.
    # Frame-pending suppression must not clear it, or the same episode alerts twice.
    alert_open_revision: int | None = None
    evidence_crop: bytes | None = None


class VisionService:
    def __init__(self, settings: Settings, detector=None, classifier=None):
        self.settings = settings
        self.now = settings.clock or time.time
        self.frames = FrameStore(max_cameras=max(1, len(settings.cameras)), clock=self.now)
        self.detector = detector or YoloDetector(settings.model, settings.device)
        if classifier is None:
            classifier = build_classifier_from_env()
        self.classifier = classifier
        self.tracker = Tracker(stationary_seconds=settings.stationary_seconds,
                               max_gap_seconds=settings.stale_seconds)
        self.objects: dict[str, ObjectState] = {}
        self.events: deque[Event] = deque(maxlen=512)
        self.subscribers: set[asyncio.Queue] = set()
        self.classification_tasks: set[asyncio.Task] = set()
        self.classification_task_cameras: dict[asyncio.Task, str] = {}
        self.grok_enabled = {camera_id: settings.automatic_classification for camera_id in settings.cameras}
        self.pending_frames = {}
        self.wake = asyncio.Event()
        self.sequence = 0
        self.boot_id = uuid.uuid4().hex
        self.detector_error: str | None = None
        self.publisher = None
        self.closed = False
        self._processed = {}
        self.confirmations = {}
        self.observations = OrderedDict()
        self.mapping_receipts = OrderedDict()
        self.active_confirmations = 0
        self.camera_online = {}
        binding = RouteBinding.load(settings.recovery_config_path) if settings.recovery_config_path else None
        if binding and binding.setup_confirmed and not settings.backend_url:
            raise ValueError('Automatic recovery requires the backend notification connection')
        self.recovery = CameraRecovery(binding, self._recovery_notice, clock=self.now,
            reference=lambda object_id: self.objects[object_id].evidence_crop if object_id in self.objects else None)

    def _recovery_notice(self, event_type, object_id, reason, step=None):
        state = self.objects.get(object_id)
        if state is not None:
            self._emit_object(event_type, state, reason=reason, **({'recoveryStep': step} if step else {}))

    def _update_recovery(self):
        objects = [self.object_payload(state) for state in self.objects.values()]
        for obj in objects:
            if not self.grok_enabled.get(obj['cameraId'], False):
                obj['classificationStatus'] = 'PENDING'
        self.recovery.update(objects)

    async def set_grok_enabled(self, camera_id, enabled):
        self.grok_enabled[camera_id] = enabled
        if not enabled:
            tasks = [task for task, camera in self.classification_task_cameras.items() if camera == camera_id]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            for state in self.objects.values():
                if state.camera_id == camera_id and state.classification_status == 'PENDING':
                    state.classification_attempt = -1
        self._update_recovery()

    def _classification_done(self, task):
        self.classification_tasks.discard(task)
        self.classification_task_cameras.pop(task, None)

    def emit(self, event_type, payload):
        self.sequence += 1
        event = Event(producer_boot_id=self.boot_id, event_id=f"{self.boot_id}:{self.sequence}",
                      sequence=self.sequence, type=event_type, timestamp=self.now(), payload=payload)
        self.events.append(event)
        for queue in tuple(self.subscribers):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)  # A slow consumer must refresh its snapshot.
            else:
                queue.put_nowait(event)
        if self.publisher:
            self.publisher.enqueue(event)
        return event

    def snapshot(self):
        for state in self.objects.values():
            self._evaluate(state)
        return [self.object_payload(state) for state in self.objects.values()]

    def object_payload(self, state):
        return {
            "objectId": state.object_id, "cameraId": state.camera_id,
            "sessionId": state.session_id, "geometryVersion": state.geometry_version,
            "trackId": state.track_id, "frameId": state.frame_id,
            "capturedAt": state.captured_at, "lastSeenAt": state.last_seen,
            "cameraKind": state.kind, "bboxPx": list(state.box),
            "detectorLabel": state.label, "detectorScore": state.score,
            "motionState": state.motion_state, "pixelStationarySeconds": state.pixel_static_s,
            "metricStationarySeconds": state.metric_static_s,
            "status": state.status, "exclusionReason": state.excluded_reason,
            "classificationStatus": state.classification_status,
            "classification": state.classification,
            "classificationProvider": state.classification_provider,
            "classificationModel": state.classification_model,
            "visualPickupAdvisory": state.visual_pickup,
            "visualPickupStatus": state.visual_pickup_status,
            "mapping": state.mapping, "potentialTarget": state.potential_target,
            "revision": state.revision, "entityVersion": max(1, state.entity_version),
            "producerBootId": self.boot_id, "eligibilityReason": state.eligibility_reason,
            "frameRef": f"/api/frames/{state.camera_id}/{state.session_id}/{state.frame_id}",
        }

    def _emit_object(self, event_type, state, **extra):
        state.entity_version += 1
        payload = self.object_payload(state)
        payload.update(extra)
        return self.emit(event_type, payload)

    def enqueue_frame(self, frame):
        if not self.camera_online.get(frame.camera_id):
            self.camera_online[frame.camera_id] = True
            self.emit("camera.connected", {"cameraId": frame.camera_id})
        for state in self.objects.values():
            if state.camera_id == frame.camera_id and (state.session_id, state.geometry_version) != (frame.session_id, frame.geometry_version) and state.status != "LOST":
                self.invalidate(state, "CAMERA_EPOCH_CHANGED")
                state.status = "LOST"
                self._emit_object("object.updated", state)
            elif state.camera_id == frame.camera_id and state.status != "LOST":
                before_target = state.potential_target
                self._evaluate(state)
                if state.potential_target != before_target:
                    self._emit_object("object.updated", state)
        self.pending_frames[frame.camera_id] = frame
        self.wake.set()

    async def worker(self):
        while not self.closed:
            if not self.pending_frames:
                self.wake.clear()
                await self.wake.wait()
                continue
            camera_id = next(iter(self.pending_frames))
            frame = self.pending_frames.pop(camera_id)
            try:
                await self.process_frame(frame)
            except Exception:
                self.detector_error = "Detector unavailable; check installation and model/device configuration"
                self.tracker.reset_camera(camera_id)
                for state in self.objects.values():
                    if state.camera_id == camera_id:
                        self.invalidate(state, "DETECTOR_UNAVAILABLE")
                self.emit("alert.detector_unavailable", {"cameraId": camera_id, "reason": self.detector_error})
            await asyncio.sleep(0)

    def _same_epoch(self, frame, exact_frame=False):
        latest = self.frames.latest(frame.camera_id)
        return (latest and (latest.session_id, latest.geometry_version) == (frame.session_id, frame.geometry_version)
                and (not exact_frame or latest.frame_id == frame.frame_id))

    async def process_frame(self, frame):
        if self.now() - frame.received_at > self.settings.stale_seconds or not self._same_epoch(frame):
            return
        context = (frame.session_id, frame.geometry_version, frame.frame_id)
        processed = self._processed.get(frame.camera_id)
        if processed and processed[:2] == context[:2] and context[2] <= processed[2]:
            return
        detections = await asyncio.to_thread(self.detector.detect, frame.jpeg)
        # A full-resolution tiled pass often takes longer than the one-second
        # evidence window. Publish the boxes on this exact frame anyway.
        # Eligibility still requires last_seen within stale_seconds, so a late
        # result cannot become a pickup target. A changed session or geometry
        # still drops the result.
        if not self._same_epoch(frame):
            return
        # Any malformed box invalidates that frame rather than accidentally hiding a person.
        if any(len(d.bbox) != 4 or not all(math.isfinite(v) for v in d.bbox)
               or d.bbox[0] < 0 or d.bbox[1] < 0 or d.bbox[2] > frame.width
               or d.bbox[3] > frame.height for d in detections):
            raise ValueError("Detector returned out-of-frame coordinates")
        updates = self.tracker.observe(frame.camera_id, frame.session_id, frame.geometry_version,
                                       frame.frame_id, frame.received_at, detections, frame.kind)
        self._processed[frame.camera_id] = context
        self.detector_error = None
        current = set()
        tracks = []
        for update in updates:
            key = f"{frame.camera_id}:{frame.session_id}:{update.track_id}"
            current.add(key)
            state = self.objects.get(key)
            if state is None:
                self._make_room()
                state = ObjectState(key, frame.camera_id, frame.session_id, frame.geometry_version,
                                    update.track_id, frame.kind, frame.frame_id, frame.captured_at,
                                    frame.received_at, update.detection.bbox,
                                    update.detection.label, update.detection.score)
                self.objects[key] = state
            if update.motion_state in {"MOVING", "EXCLUDED", "UNKNOWN"}:
                self.invalidate(state, update.excluded_reason or update.motion_state)
            state.frame_id, state.captured_at, state.last_seen = frame.frame_id, frame.captured_at, frame.received_at
            state.box, state.label, state.score = update.detection.bbox, update.detection.label, update.detection.score
            state.pixel_static_s, state.motion_state = update.stationary_for_s, update.motion_state
            state.excluded_reason = update.excluded_reason
            state.status = "EXCLUDED" if update.excluded_reason else "OBSERVING"
            observation_key = (frame.camera_id, frame.session_id, frame.geometry_version, frame.frame_id, state.track_id)
            self.observations[observation_key] = state.revision
            while len(self.observations) > 8192:
                self.observations.popitem(last=False)
            self._evaluate(state)
            self._emit_object("object.observed", state)
            tracks.append({"trackId": state.track_id, "objectId": key,
                           "detectorLabel": state.label, "detectorScore": state.score,
                           "bboxPx": list(state.box), "contactPointPx": update.detection.contact_point,
                           "motionState": state.motion_state, "pixelStationarySeconds": state.pixel_static_s,
                           "exclusionReason": state.excluded_reason})
            if (self.grok_enabled.get(state.camera_id, False) and
                    state.kind == "FIXED" and state.motion_state == "STATIONARY" and
                    not state.excluded_reason and state.classification_attempt != state.revision and
                    len(self.classification_tasks) < self.settings.max_pending_classifications):
                state.classification_attempt = state.revision
                state.classification_status = "PENDING"
                task = asyncio.create_task(self._classify(state.object_id, state.revision, frame))
                self.classification_tasks.add(task)
                self.classification_task_cameras[task] = state.camera_id
                task.add_done_callback(self._classification_done)
        for key, state in list(self.objects.items()):
            if state.camera_id == frame.camera_id and key not in current and state.status != "LOST":
                self.invalidate(state, "TRACK_LOST")
                state.status, state.motion_state = "LOST", "UNKNOWN"
                self._emit_object("object.updated", state)
        self.emit("frame.detections", {
            "cameraId": frame.camera_id, "sessionId": frame.session_id,
            "geometryVersion": frame.geometry_version, "frameId": frame.frame_id,
            "cameraKind": frame.kind, "capturedAt": frame.captured_at,
            "receivedAt": frame.received_at, "timestampBasis": frame.timestamp_basis,
            "width": frame.width, "height": frame.height, "tracks": tracks,
            "frameRef": f"/api/frames/{frame.camera_id}/{frame.session_id}/{frame.frame_id}",
        })
        self._update_recovery()
        if frame.kind == 'FIXED':
            self.recovery.observe_scene(frame.received_at,
                any(track['detectorLabel'].lower() == 'can' for track in tracks))

    def _make_room(self):
        if len(self.objects) >= self.settings.max_objects:
            oldest = min(self.objects.values(), key=lambda s: s.last_seen)
            self.invalidate(oldest, "RETENTION_LIMIT")
            del self.objects[oldest.object_id]
            self._emit_object("object.removed", oldest, reason="RETENTION_LIMIT")

    def invalidate(self, state, reason):
        alert_open = state.alert_open_revision is not None
        state.potential_target = False
        state.alert_open_revision = None
        state.metric_anchor = state.metric_start = state.metric_last = None
        state.metric_count, state.metric_static_s, state.metric_last_frame = 0, 0, -1
        state.revision += 1
        state.classification = None
        state.classification_model = None
        state.classification_status = "PENDING"
        state.visual_pickup = None
        state.visual_pickup_status = "PENDING"
        state.mapping = None
        state.eligibility_reason = reason
        if alert_open:
            state.status = "STATIC_PENDING_REVIEW"
            self._emit_object("alert.target_revoked", state, reason=reason,
                              action="HOLD_BEFORE_PICKUP")

    def _evaluate(self, state):
        result = state.classification
        latest = self.frames.latest(state.camera_id)
        same_epoch = bool(latest) and (latest.session_id, latest.geometry_version) == (state.session_id, state.geometry_version)
        current = same_epoch and latest.frame_id == state.frame_id
        frame_pending = same_epoch and latest.frame_id > state.frame_id
        eligible = bool(
            current and state.status != "LOST" and
            state.kind == "FIXED" and state.motion_state == "STATIONARY" and
            not state.excluded_reason and self.now() - state.last_seen <= self.settings.stale_seconds and
            state.metric_static_s >= self.settings.stationary_seconds and state.metric_count >= 10 and
            state.metric_last is not None and self.now() - state.metric_last <= self.settings.stale_seconds and
            result and result["subject_kind"] == "INANIMATE" and result["carrying"] == "NO" and
            result["category"] in {"TRASH", "POTENTIALLY_LOST_PROPERTY"} and result["confidence"] != "LOW"
        )
        state.potential_target = eligible
        state.eligibility_reason = None if eligible else ("FRAME_PENDING" if latest and not current else "NOT_ELIGIBLE")
        if state.status == "LOST":
            pass
        elif state.excluded_reason:
            state.status = "EXCLUDED"
        elif eligible:
            state.status = "POTENTIAL_TARGET"
        elif state.motion_state == "STATIONARY":
            state.status = "STATIC_PENDING_REVIEW"
        # A newer unprocessed frame removes dispatch availability for this episode
        # without a revocation alert. The same revision can become available again
        # without a second potential-target notification.
        if eligible and state.alert_open_revision != state.revision:
            self._emit_object("alert.potential_target", state)
            state.alert_open_revision = state.revision
        elif not eligible and not frame_pending and state.alert_open_revision is not None:
            state.alert_open_revision = None
            self._emit_object("alert.target_revoked", state, reason="ELIGIBILITY_CHANGED",
                              action="HOLD_BEFORE_PICKUP")

    @staticmethod
    def _images(frame, box):
        with Image.open(BytesIO(frame.jpeg)) as original:
            image = original.convert("RGB")
            x1, y1, x2, y2 = box
            crop = image.crop((max(0, int(x1)), max(0, int(y1)), min(image.width, math.ceil(x2)), min(image.height, math.ceil(y2))))
            context = image.copy()
            ImageDraw.Draw(context).rectangle(box, outline="red", width=3)
            output = []
            for item in (crop, context):
                item.thumbnail((960, 960))
                stream = BytesIO(); item.save(stream, "JPEG", quality=85); output.append(stream.getvalue())
            return output

    async def _classify(self, object_id, revision, frame):
        state = self.objects.get(object_id)
        if not state or state.revision != revision or not self.grok_enabled.get(state.camera_id, False):
            return
        crop, context = self._images(frame, state.box)
        async def pickup_advisory():
            method = getattr(self.classifier, "assess_pickup", None)
            # Keep a more salient neighbouring can out of a paper grasp check.
            # Full context is still used by classify for living/carrying checks;
            # pose, approach clearance and size remain independently unverified.
            return await method(crop, crop) if method else None
        try:
            joint = getattr(self.classifier, 'assess_object', None)
            if joint:
                try:
                    assessment = await joint(crop, context)
                    result, advisory = assessment.classification, assessment.pickup
                except Exception:
                    result, advisory = None, None
            else:
                result, advisory = await asyncio.gather(self.classifier.classify(crop, context),
                                                       pickup_advisory(), return_exceptions=True)
        except asyncio.CancelledError:
            state = self.objects.get(object_id)
            if state and state.revision == revision:
                state.classification_attempt = -1
            raise
        if isinstance(result, Exception):
            result = None
        if isinstance(advisory, Exception):
            advisory = None
        status = "COMPLETED" if result is not None else "UNAVAILABLE"
        state = self.objects.get(object_id)
        if (not state or not self.grok_enabled.get(state.camera_id, False) or
                state.revision != revision or state.status == "LOST" or
                self.now() - state.last_seen > self.settings.stale_seconds):
            return
        latest = self.frames.latest(state.camera_id)
        if not latest or (latest.session_id, latest.geometry_version) != (state.session_id, state.geometry_version):
            return
        state.classification = result.model_dump(mode="json") if result else None
        state.classification_status = status
        state.classification_provider = self.classifier.provider
        state.classification_model = ((getattr(self.classifier, 'last_model', None)
            or getattr(self.classifier, '_model', None)) if result and self.classifier.provider == 'grok' else None)
        state.visual_pickup_status = "COMPLETED" if advisory is not None else "UNAVAILABLE"
        state.visual_pickup = ({**advisory.model_dump(mode="json"), "advisory": True,
                                "pickupVerified": False, "sourceFrameId": frame.frame_id}
                               if advisory is not None else None)
        state.evidence_crop = crop
        self._evaluate(state)
        self._emit_object("object.classified", state)
        self._update_recovery()

    async def apply_mapping(self, evidence: MappingEvidence):
        key = f"{evidence.camera_id}:{evidence.session_id}:{evidence.track_id}"
        state = self.objects.get(key)
        frame = self.frames.get(evidence.camera_id, evidence.session_id, evidence.frame_id)
        if not state or state.status == "LOST" or not frame:
            raise ValueError("Unknown or expired observation")
        observation_key = (evidence.camera_id, evidence.session_id, evidence.geometry_version,
                           evidence.frame_id, evidence.track_id)
        receipt = self.mapping_receipts.get(observation_key)
        if receipt is not None:
            previous_evidence, receipt_revision = receipt
            latest = self.frames.latest(evidence.camera_id)
            if previous_evidence != evidence.model_dump(mode="json"):
                raise ValueError("Conflicting mapping retry")
            if (receipt_revision != state.revision or not latest or
                    (latest.session_id, latest.geometry_version) != (state.session_id, state.geometry_version)):
                raise ValueError("Mapping retry belongs to revoked evidence")
            self._evaluate(state)
            return self.object_payload(state)
        if self.observations.get(observation_key) != state.revision:
            raise ValueError("Track was not observed in this frame or its evidence was revoked")
        if (state.kind != "FIXED" or state.geometry_version != evidence.geometry_version or
                frame.geometry_version != evidence.geometry_version or
                evidence.captured_at != frame.captured_at or
                evidence.received_at != frame.received_at or
                self.now() - frame.received_at > self.settings.stale_seconds or
                evidence.frame_id <= state.metric_last_frame):
            raise ValueError("Stale or incompatible mapping evidence")
        valid = (evidence.valid and evidence.calibration_valid and evidence.position_error_m is not None
                 and evidence.position_error_m <= self.settings.max_position_error_m)
        previous = state.mapping
        map_changed = previous and any(previous[k] != getattr(evidence, attr) for k, attr in
            [("mapId", "map_id"), ("mapVersion", "map_version"), ("calibrationId", "calibration_id")])
        if not valid:
            self.invalidate(state, "INVALID_MAPPING")
            self._evaluate(state)
            self._emit_object("object.updated", state)
            self._remember_mapping(observation_key, evidence, state.revision)
            return self.object_payload(state)
        point = evidence.floor_point_m
        moved = state.metric_anchor is not None and math.dist(point, state.metric_anchor) > self.settings.max_movement_m
        gap = state.metric_last is not None and frame.received_at - state.metric_last > self.settings.stale_seconds
        if moved or gap or map_changed:
            self.invalidate(state, "TARGET_MOVED" if moved else "MAPPING_DISCONTINUITY")
        if state.metric_anchor is None:
            state.metric_anchor, state.metric_start = point, frame.received_at
        state.metric_count += 1
        state.metric_last, state.metric_last_frame = frame.received_at, evidence.frame_id
        state.metric_static_s = frame.received_at - state.metric_start
        state.mapping = evidence.model_dump(by_alias=True, mode="json")
        self._evaluate(state)
        self._emit_object("object.updated", state)
        self._remember_mapping(observation_key, evidence, state.revision)
        return self.object_payload(state)

    def _remember_mapping(self, key, evidence, revision):
        self.mapping_receipts[key] = (evidence.model_dump(mode="json"), revision)
        while len(self.mapping_receipts) > 8192:
            self.mapping_receipts.popitem(last=False)

    async def sweep(self):
        now = self.now()
        for camera_id, online in list(self.camera_online.items()):
            latest = self.frames.latest(camera_id)
            if online and (latest is None or now - latest.received_at > self.settings.stale_seconds):
                self.camera_online[camera_id] = False
                self.emit("alert.camera_unavailable", {"cameraId": camera_id, "reason": "NO_FRESH_FRAMES"})
        for state in list(self.objects.values()):
            if now - state.last_seen > self.settings.stale_seconds and state.status != "LOST":
                self.invalidate(state, "STALE_OBSERVATION")
                state.status, state.motion_state = "LOST", "UNKNOWN"
                self._emit_object("object.updated", state)
            elif state.potential_target and (state.metric_last is None or now - state.metric_last > self.settings.stale_seconds):
                self.invalidate(state, "STALE_MAPPING")
                self._evaluate(state)
                self._emit_object("object.updated", state)
        self._update_recovery()

    async def sweeper(self):
        while not self.closed:
            await self.sweep()
            await asyncio.sleep(.25)

    async def drain_classifications(self):
        if self.classification_tasks:
            await asyncio.gather(*tuple(self.classification_tasks))

    async def confirm_target(self, request):
        if self.active_confirmations >= 2:
            raise RuntimeError("Confirmation capacity reached")
        self.active_confirmations += 1
        try:
            return await self._confirm_target(request)
        finally:
            self.active_confirmations -= 1

    async def _confirm_target(self, request):
        state = self.objects.get(request.object_id)
        if state:
            self._evaluate(state)
        if not state or not state.potential_target or not state.evidence_crop:
            raise ValueError("Target is not currently eligible")
        frame = self.frames.latest(request.rover_camera_id)
        if not frame or frame.kind != "ROVER" or self.now() - frame.received_at > self.settings.stale_seconds:
            raise ValueError("Fresh rover frame unavailable")
        revision = state.revision
        candidates = [{"track_id": obj.track_id, "bbox": obj.box} for obj in self.objects.values()
                      if obj.camera_id == frame.camera_id and obj.session_id == frame.session_id and
                      obj.frame_id == frame.frame_id and not obj.excluded_reason and obj.status != "LOST"]
        if not candidates:
            return {"state": "NOT_VISIBLE", "candidateTrackId": None, "reason": "No current candidate", "missionId": request.mission_id}
        try:
            result = await self.classifier.confirm(state.evidence_crop, frame.jpeg, candidates)
        except Exception:
            return {"state": "UNCERTAIN", "candidateTrackId": None, "reason": "Classification unavailable", "missionId": request.mission_id}
        self._evaluate(state)
        latest = self.frames.latest(request.rover_camera_id)
        match = next((obj for obj in self.objects.values()
                      if obj.camera_id == frame.camera_id and obj.session_id == frame.session_id
                      and obj.track_id == result.candidate_track_id), None)
        if (state.revision != revision or not state.potential_target or not latest or
                (latest.session_id, latest.geometry_version) != (frame.session_id, frame.geometry_version) or
                self.now() - latest.received_at > self.settings.stale_seconds or
                (result.state.value == "CONFIRMED" and (not match or match.excluded_reason or
                 match.status == "LOST" or match.frame_id != latest.frame_id or self.now() - match.last_seen > self.settings.stale_seconds))):
            return {"state": "UNCERTAIN", "candidateTrackId": None, "reason": "Evidence expired during confirmation", "missionId": request.mission_id}
        return {"state": result.state.value, "candidateTrackId": result.candidate_track_id,
                "reason": result.reason, "missionId": request.mission_id,
                "cameraId": frame.camera_id, "referenceFrameId": frame.frame_id,
                "frameId": match.frame_id if match else latest.frame_id,
                "bboxPx": list(match.box) if match else None,
                "provider": self.classifier.provider}

    async def close(self):
        self.closed = True
        self.wake.set()
        for task in tuple(self.classification_tasks):
            task.cancel()
        if self.classification_tasks:
            await asyncio.gather(*tuple(self.classification_tasks), return_exceptions=True)
        await self.recovery.close()
