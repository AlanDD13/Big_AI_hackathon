"""Operator-visible backend state, cursors, and durable events.

This process is the source of the dashboard snapshot. A restart changes
backendBootId and drops cursors, which forces clients to take a new snapshot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
import json
import secrets
import threading
from typing import Mapping
from uuid import uuid4

from backend.evidence import EvidenceConflict, object_id_for, sample_for
from backend.postgres_projection import ObservationSource
from backend.stationarity import MetricSample, evaluate_stationarity
from backend.vision_ingest import ParsedVisionEvent, VisionConflict


CURSOR_TTL_SECONDS = 15 * 60
SESSION_TTL_SECONDS = 8 * 60 * 60


@dataclass(frozen=True)
class RecordResult:
    object_id: str
    backend_revision: int
    replayed: bool


@dataclass(frozen=True)
class VisionIngestResult:
    object_id: str | None
    backend_revision: int | None
    replayed: bool
    discarded: bool


@dataclass(frozen=True)
class StreamItem:
    event: str
    event_id: str | None
    data: dict[str, object]


class BackendState:
    def __init__(
        self,
        *,
        boot_id: str | None = None,
        now: Callable[[], float] | None = None,
        token_factory: Callable[[], str] | None = None,
        observation_log: object | None = None,
    ) -> None:
        if boot_id is None:
            boot_id = secrets.token_hex(16)
        if len(boot_id) != 32 or any(character not in "0123456789abcdef" for character in boot_id):
            raise ValueError("backendBootId must be 32 lowercase hex characters")
        self.boot_id = boot_id
        self._now = now or (lambda: __import__("time").time())
        self._token = token_factory or (lambda: secrets.token_urlsafe(18))
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._sessions: dict[str, float] = {}
        self._seq = 0
        self._events: list[dict[str, object]] = []
        self._cursors: dict[str, tuple[str, int, float]] = {}
        self._cursor_by_position: dict[tuple[str, int], str] = {}
        self._vision_events: dict[str, str] = {}
        self._frames: dict[tuple[str, str, str, str, int], str] = {}
        self._latest_frame: dict[tuple[str, str, str, str], int] = {}
        self._samples: dict[str, list[MetricSample]] = {}
        self._evidence: dict[str, dict[str, object]] = {}
        self._objects: dict[str, dict[str, object]] = {}
        self._missions: dict[str, dict[str, object]] = {}
        self._mission_status_revisions: dict[str, int] = {}
        self._classifications: dict[str, tuple[str, dict[str, object]]] = {}
        self._alerts: dict[str, dict[str, object]] = {}
        self._potential: dict[str, bool] = {}
        self._revisions: dict[tuple[str, str], int] = {}
        self._observation_log = observation_log
        self._ingested: dict[str, tuple[str, str | None, int | None]] = {}
        self._current_vision_boot: str | None = None
        self._stale_vision_boots: set[str] = set()
        self._vision_sequence: dict[str, int] = {}
        self._gapped_vision_boots: set[str] = set()
        self._vision_entity_version: dict[tuple[str, str], int] = {}
        self._vision_alerts: set[tuple[str, int, str]] = set()

    def open_session(self) -> str:
        session_id = self._token()
        with self._lock:
            self._sessions[session_id] = self._now() + SESSION_TTL_SECONDS
        return session_id

    def close_session(self, session_id: str | None) -> None:
        if session_id:
            with self._lock:
                self._sessions.pop(session_id, None)

    def session_open(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        with self._lock:
            return self._session_open_unlocked(session_id)

    def _session_open_unlocked(self, session_id: str) -> bool:
        expires_at = self._sessions.get(session_id)
        return expires_at is not None and self._now() < expires_at

    def record_observation(
        self,
        evidence: Mapping[str, object],
        *,
        vision_event_id: str | None = None,
        producer_boot_id: str | None = None,
        entity_version: int | None = None,
        vision_revision: int | None = None,
        persist: bool = True,
    ) -> RecordResult:
        canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"), allow_nan=False)
        object_id = object_id_for(evidence)
        frame_key = (
            str(evidence["cameraId"]),
            str(evidence["sessionId"]),
            str(evidence["geometryVersion"]),
            str(evidence["trackId"]),
            int(evidence["frameId"]),
        )
        track_key = frame_key[:4]
        source: ObservationSource = {
            "vision_event_id": vision_event_id,
            "producer_boot_id": producer_boot_id,
            "entity_version": entity_version,
            "vision_revision": vision_revision,
        }
        with self._lock:
            if vision_event_id is not None:
                previous = self._vision_events.get(vision_event_id)
                if previous is not None:
                    if previous != canonical:
                        raise EvidenceConflict("Vision event ID was reused with different evidence")
                    return RecordResult(object_id, int(self._objects[object_id]["backendRevision"]), True)

            stored = self._frames.get(frame_key)
            if stored is not None and stored != canonical:
                raise EvidenceConflict("Frame was reused with different evidence")

            samples = self._samples.get(object_id)
            sample = sample_for(evidence)
            if stored is None:
                latest = self._latest_frame.get(track_key)
                if latest is not None and int(evidence["frameId"]) < latest:
                    raise EvidenceConflict("Frame is older than stored evidence for this track")
                if samples and sample.received_at < samples[-1].received_at:
                    raise EvidenceConflict("Observation receipt time is older than stored evidence")
            if persist and self._observation_log is not None and (stored is None or vision_event_id is not None):
                try:
                    self._observation_log.save(evidence, **source)
                except ValueError as exc:
                    raise EvidenceConflict(str(exc)) from exc

            if vision_event_id is not None:
                self._vision_events[vision_event_id] = canonical
            if stored is not None:
                return RecordResult(object_id, int(self._objects[object_id]["backendRevision"]), True)

            self._samples.setdefault(object_id, []).append(sample)
            self._frames[frame_key] = canonical
            self._latest_frame[track_key] = int(evidence["frameId"])
            self._evidence[object_id] = dict(evidence)
            self._publish_object_unlocked(
                object_id,
                vision_event_id=vision_event_id,
                producer_boot_id=producer_boot_id,
                entity_version=entity_version,
                vision_revision=vision_revision,
            )
            return RecordResult(object_id, int(self._objects[object_id]["backendRevision"]), False)

    def record_classification(self, object_id: str, status: str, data: Mapping[str, object]) -> None:
        """Restore trusted classification data for an already projected object."""
        with self._lock:
            if object_id not in self._objects:
                return
            projection = (status.upper(), dict(data))
            if self._classifications.get(object_id) == projection:
                return
            self._classifications[object_id] = projection
            body = dict(self._objects[object_id])
            revision = self._next_revision_unlocked("object", object_id)
            body.update(
                backendRevision=revision,
                classificationStatus=status.upper(),
                classification=dict(data),
                classificationProvider=data.get("provider"),
            )
            self._objects[object_id] = body
            self._append_unlocked("object.updated", "object", revision, None, body)

    def record_mission(
        self,
        mission_id: str,
        object_id: str,
        destination_zone_id: str,
        source: str,
        state: str = "QUEUED",
        status_revision: int | None = None,
    ) -> dict[str, object]:
        """Project an admitted mission once, including concurrent admission replays.

        Admission results may arrive out of order. A persisted status revision
        allows a replay to refresh the snapshot without publishing a duplicate.
        """
        with self._lock:
            if mission_id in self._missions:
                prior = self._mission_status_revisions.get(mission_id, -1)
                if status_revision is not None and status_revision > prior:
                    self._missions[mission_id] = {
                        **self._missions[mission_id],
                        "state": state.upper(),
                        "backendRevision": self._next_revision_unlocked("mission", mission_id),
                    }
                    self._mission_status_revisions[mission_id] = status_revision
                return dict(self._missions[mission_id])
            revision = self._next_revision_unlocked("mission", mission_id)
            body = {
                "missionId": mission_id,
                "objectId": object_id,
                "destinationZoneId": destination_zone_id,
                "source": source,
                "state": state.upper(),
                "backendBootId": self.boot_id,
                "backendRevision": revision,
            }
            self._missions[mission_id] = body
            if status_revision is not None:
                self._mission_status_revisions[mission_id] = status_revision
            self._append_unlocked("mission.updated", "mission", revision, None, body)
            return dict(body)

    def ingest_vision(self, event: ParsedVisionEvent) -> VisionIngestResult:
        """Project one vision event. Replays and stale boots do not publish again.

        potentialTarget is stored for the dashboard. dispatchEligible stays false,
        and this path does not open a mission.
        """
        with self._lock:
            seen = self._ingested.get(event.event_id)
            if seen is not None:
                if seen[0] != event.canonical:
                    raise VisionConflict("Vision event ID was reused with a different body")
                return VisionIngestResult(seen[1], seen[2], True, False)

            if event.producer_boot_id in self._stale_vision_boots:
                self._ingested[event.event_id] = (event.canonical, event.object_id, None)
                return VisionIngestResult(event.object_id, None, False, True)

            if (
                self._current_vision_boot is not None
                and event.producer_boot_id != self._current_vision_boot
            ):
                self._stale_vision_boots.add(self._current_vision_boot)
            self._current_vision_boot = event.producer_boot_id

            if event.producer_boot_id in self._gapped_vision_boots:
                raise VisionConflict("Vision sequence gap was not applied")
            last_sequence = self._vision_sequence.get(event.producer_boot_id)
            if last_sequence is not None and event.sequence > last_sequence + 1:
                self._gapped_vision_boots.add(event.producer_boot_id)
                raise VisionConflict("Vision sequence gap was not applied")
            if last_sequence is not None and event.sequence <= last_sequence:
                self._ingested[event.event_id] = (event.canonical, event.object_id, None)
                return VisionIngestResult(event.object_id, None, False, True)

            if event.projection is None:
                return self._ingest_notice_unlocked(event)

            assert event.object_id is not None
            assert event.entity_version is not None
            version_key = (event.object_id, event.producer_boot_id)
            last_version = self._vision_entity_version.get(version_key)
            if last_version is not None and event.entity_version <= last_version:
                self._vision_sequence[event.producer_boot_id] = event.sequence
                current = self._objects.get(event.object_id)
                revision = None if current is None else int(current["backendRevision"])
                self._ingested[event.event_id] = (event.canonical, event.object_id, revision)
                return VisionIngestResult(event.object_id, revision, False, True)

            body = self._vision_body_unlocked(event)
            self._vision_sequence[event.producer_boot_id] = event.sequence
            self._vision_entity_version[version_key] = event.entity_version
            self._objects[event.object_id] = body
            self._append_unlocked("object.updated", "object", int(body["backendRevision"]), event.event_id, body)
            alert_revision = self._vision_alert_unlocked(event, body)
            published = alert_revision if alert_revision is not None else int(body["backendRevision"])
            self._ingested[event.event_id] = (event.canonical, event.object_id, published)
            return VisionIngestResult(event.object_id, published, False, False)

    def _vision_body_unlocked(self, event: ParsedVisionEvent) -> dict[str, object]:
        assert event.projection is not None
        assert event.object_id is not None
        previous = self._objects.get(event.object_id)
        body = dict(event.projection)
        if not body.pop("_visualStatusPresent") and previous is not None:
            body["visualPickupStatus"] = previous.get("visualPickupStatus")
        if not body.pop("_visualAdvisoryPresent") and previous is not None:
            body["visualPickupAdvisory"] = previous.get("visualPickupAdvisory")
        body.pop("_visualStatusPresent", None)
        body.pop("_visualAdvisoryPresent", None)
        revision = self._next_revision_unlocked("object", event.object_id)
        body["backendBootId"] = self.boot_id
        body["backendRevision"] = revision
        body["dispatchEligible"] = False
        advisory = body.get("visualPickupAdvisory")
        if isinstance(advisory, dict):
            advisory["pickupVerified"] = False
            advisory["advisory"] = True
        return body

    def _vision_alert_unlocked(self, event: ParsedVisionEvent, body: dict[str, object]) -> int | None:
        if event.alert_kind not in {"potential_target", "target_revoked"}:
            return None
        assert event.object_id is not None
        assert event.revision is not None
        key = (event.object_id, event.revision, event.alert_kind)
        if key in self._vision_alerts:
            return None
        self._vision_alerts.add(key)
        action = "HOLD_BEFORE_PICKUP" if event.alert_kind == "target_revoked" else None
        return self._store_alert_unlocked(
            event.alert_kind,
            event.object_id,
            None if event.alert_kind == "potential_target" else event.alert_reason,
            action,
            body,
            event.event_id,
        )

    def _ingest_notice_unlocked(self, event: ParsedVisionEvent) -> VisionIngestResult:
        self._vision_sequence[event.producer_boot_id] = event.sequence
        revision = None
        if event.alert_kind is not None:
            revision = self._store_alert_unlocked(
                event.alert_kind,
                None,
                event.alert_reason,
                None,
                None,
                event.event_id,
                event.camera_id,
            )
        self._ingested[event.event_id] = (event.canonical, None, revision)
        return VisionIngestResult(None, revision, False, False)

    def _store_alert_unlocked(
        self,
        kind: str,
        object_id: str | None,
        reason: str | None,
        action: str | None,
        object_body: dict[str, object] | None,
        vision_event_id: str,
        camera_id: str | None = None,
    ) -> int:
        alert_id = str(uuid4())
        revision = self._next_revision_unlocked("alert", alert_id)
        source = object_body or {}
        alert = {
            "alertId": alert_id,
            "type": f"alert.{kind}",
            "kind": kind,
            "eventId": vision_event_id,
            "visionEventId": vision_event_id,
            "objectId": object_id,
            "cameraId": camera_id if camera_id is not None else source.get("cameraId"),
            "backendBootId": self.boot_id,
            "backendRevision": revision,
            "producerBootId": source.get("producerBootId"),
            "entityVersion": source.get("entityVersion"),
            "revision": source.get("revision"),
            "reason": reason,
            "action": action,
            "acknowledged": False,
            "createdAt": self._now(),
            "object": None if object_body is None else dict(object_body),
        }
        self._alerts[alert_id] = alert
        self._append_unlocked(f"alert.{kind}", "alert", revision, vision_event_id, alert)
        return revision

    def snapshot(self, session_id: str) -> dict[str, object]:
        with self._lock:
            self._refresh_unlocked()
            return {
                "schemaVersion": "backend.v1",
                "backendBootId": self.boot_id,
                "cursor": self._cursor_unlocked(session_id, self._seq),
                "objects": [dict(item) for item in self._objects.values()],
                "missions": [dict(item) for item in self._missions.values()],
                "robots": [],
                "alerts": [dict(item) for item in self._alerts.values()],
            }

    def follow(self, session_id: str, cursor: str | None, *, idle_timeout: float | None) -> Iterator[StreamItem]:
        """Replay events strictly after a session cursor, then wait for new ones."""
        with self._lock:
            position = self._cursor_position_unlocked(session_id, cursor)
            deadline = None if idle_timeout is None else self._now() + idle_timeout
        if position is None:
            yield StreamItem("snapshot_required", None, {"reason": "REPLAY_UNAVAILABLE"})
            return
        while True:
            with self._lock:
                if not self._session_open_unlocked(session_id):
                    return
                items: list[StreamItem] = []
                while self._seq > position:
                    position += 1
                    event = self._events[position - 1]
                    event_id = self._cursor_unlocked(session_id, position)
                    public = dict(event["public"])
                    public["id"] = event_id
                    items.append(StreamItem(str(public["type"]), event_id, public))
                if not items:
                    if idle_timeout == 0:
                        return
                    remaining = None if deadline is None else deadline - self._now()
                    if remaining is not None and remaining <= 0:
                        return
                    timeout = 0.25 if remaining is None else min(0.25, remaining)
                    self._changed.wait(timeout=timeout)
                    continue
            for item in items:
                yield item
            if idle_timeout == 0:
                return

    def _refresh_unlocked(self) -> None:
        for object_id in list(self._evidence):
            self._publish_object_unlocked(
                object_id,
                vision_event_id=None,
                producer_boot_id=self._objects[object_id].get("producerBootId"),
                entity_version=self._objects[object_id].get("entityVersion"),
                vision_revision=self._objects[object_id].get("revision"),
                refresh=True,
            )

    def _publish_object_unlocked(
        self,
        object_id: str,
        *,
        vision_event_id: str | None,
        producer_boot_id: object,
        entity_version: object,
        vision_revision: object,
        refresh: bool = False,
    ) -> None:
        evidence = self._evidence[object_id]
        stationary = evaluate_stationarity(self._samples[object_id], evaluated_at=self._now())
        potential = stationary.eligible
        previous = self._potential.get(object_id)
        if refresh and previous is potential:
            return
        reason = evidence["invalidReason"] or ("stationary" if potential else "stationarity_required")
        revision = self._next_revision_unlocked("object", object_id)
        classification = self._classifications.get(object_id)
        body = {
            "objectId": object_id,
            "backendBootId": self.boot_id,
            "backendRevision": revision,
            "eventId": vision_event_id,
            "visionEventId": vision_event_id,
            "producerBootId": producer_boot_id,
            "entityVersion": entity_version,
            "revision": vision_revision,
            "state": "POTENTIAL_TARGET" if potential else "OBSERVING",
            "status": "POTENTIAL_TARGET" if potential else "OBSERVING",
            "cameraId": evidence["cameraId"],
            "sessionId": evidence["sessionId"],
            "geometryVersion": evidence["geometryVersion"],
            "trackId": evidence["trackId"],
            "frameId": evidence["frameId"],
            "capturedAt": evidence["capturedAt"],
            "receivedAt": evidence["receivedAt"],
            "lastSeenAt": evidence["receivedAt"],
            "cameraKind": "FIXED" if evidence["invalidReason"] != "rover_confirmation_only" else "ROVER",
            "bboxPx": None,
            "detectorLabel": None,
            "detectorScore": None,
            "grokConfidence": None,
            "motionState": "STATIONARY" if potential else "OBSERVING",
            "metricStationarySeconds": stationary.span_seconds,
            "floorPointM": evidence["floorPointM"],
            "positionErrorM": evidence["positionErrorM"],
            "mapping": {
                "cameraId": evidence["cameraId"],
                "sessionId": evidence["sessionId"],
                "geometryVersion": evidence["geometryVersion"],
                "frameId": evidence["frameId"],
                "trackId": evidence["trackId"],
                "mapId": evidence["mapId"],
                "mapVersion": evidence["mapVersion"],
                "calibrationId": evidence["calibrationId"],
                "calibrationValid": evidence["calibrationValid"],
                "capturedAt": evidence["capturedAt"],
                "receivedAt": evidence["receivedAt"],
                "floorPointM": evidence["floorPointM"],
                "positionErrorM": evidence["positionErrorM"],
                "valid": evidence["valid"],
                "invalidReason": evidence["invalidReason"],
            },
            "classificationStatus": classification[0] if classification else "PENDING",
            "classification": dict(classification[1]) if classification else None,
            "classificationProvider": classification[1].get("provider") if classification else None,
            "potentialTarget": potential,
            "dispatchEligible": False,
            "eligibilityReason": None if potential else reason,
            "evidenceUrl": None,
            "stationary": {
                "sampleCount": stationary.sample_count,
                "spanSeconds": stationary.span_seconds,
                "eligible": potential,
            },
        }
        self._objects[object_id] = body
        self._append_unlocked(
            "object.updated",
            "object",
            revision,
            vision_event_id,
            body,
        )
        if potential and previous is not True:
            self._append_alert_unlocked(object_id, "potential_target", "stationary", None, body)
        elif previous is True and not potential:
            self._append_alert_unlocked(
                object_id, "target_revoked", str(reason), "HOLD_BEFORE_PICKUP", body
            )
        self._potential[object_id] = potential

    def _append_alert_unlocked(
        self,
        object_id: str,
        kind: str,
        reason: str,
        action: str | None,
        object_body: dict[str, object],
    ) -> None:
        alert_id = str(uuid4())
        revision = self._next_revision_unlocked("alert", alert_id)
        alert = {
            "alertId": alert_id,
            "type": f"alert.{kind}",
            "kind": kind,
            "eventId": object_body.get("eventId"),
            "visionEventId": object_body.get("visionEventId"),
            "objectId": object_id,
            "backendBootId": self.boot_id,
            "backendRevision": revision,
            "producerBootId": object_body.get("producerBootId"),
            "entityVersion": object_body.get("entityVersion"),
            "revision": object_body.get("revision"),
            "reason": None if kind == "potential_target" else reason,
            "action": action,
            "acknowledged": False,
            "createdAt": self._now(),
            "object": dict(object_body),
        }
        self._alerts[alert_id] = alert
        self._append_unlocked(f"alert.{kind}", "alert", revision, None, alert)

    def _append_unlocked(
        self,
        event_type: str,
        entity: str,
        revision: int,
        vision_event_id: str | None,
        body: dict[str, object],
    ) -> None:
        self._seq += 1
        self._events.append(
            {
                "recordedAt": self._now(),
                "public": {
                    "type": event_type,
                    "backendBootId": self.boot_id,
                    "entity": entity,
                    "backendRevision": revision,
                    "eventId": vision_event_id,
                    "visionEventId": vision_event_id,
                    "body": body,
                },
            }
        )
        self._changed.notify_all()

    def _next_revision_unlocked(self, entity: str, entity_id: str) -> int:
        key = (entity, entity_id)
        revision = self._revisions.get(key, 0) + 1
        self._revisions[key] = revision
        return revision

    def _cursor_unlocked(self, session_id: str, seq: int) -> str:
        position = (session_id, seq)
        existing = self._cursor_by_position.get(position)
        if existing is not None:
            return existing
        token = self._token()
        named_at = self._event_time(seq)
        self._cursor_by_position[position] = token
        self._cursors[token] = (session_id, seq, named_at)
        return token

    def _event_time(self, seq: int) -> float:
        if seq == 0:
            return self._now()
        return float(self._events[seq - 1]["recordedAt"])

    def _cursor_position_unlocked(self, session_id: str, cursor: str | None) -> int | None:
        if cursor is None:
            return None
        found = self._cursors.get(cursor)
        if found is None:
            return None
        owner, seq, named_at = found
        if owner != session_id or self._now() > named_at + CURSOR_TTL_SECONDS:
            return None
        return seq
