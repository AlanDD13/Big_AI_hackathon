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
from backend.stationarity import MetricSample, evaluate_stationarity


CURSOR_TTL_SECONDS = 15 * 60
SESSION_TTL_SECONDS = 8 * 60 * 60


@dataclass(frozen=True)
class RecordResult:
    object_id: str
    backend_revision: int
    replayed: bool


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
        self._alerts: dict[str, dict[str, object]] = {}
        self._potential: dict[str, bool] = {}
        self._revisions: dict[tuple[str, str], int] = {}

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
        with self._lock:
            if vision_event_id is not None:
                previous = self._vision_events.get(vision_event_id)
                if previous is not None:
                    if previous != canonical:
                        raise EvidenceConflict("Vision event ID was reused with different evidence")
                    return RecordResult(object_id, int(self._objects[object_id]["backendRevision"]), True)

            stored = self._frames.get(frame_key)
            if stored is not None:
                if stored != canonical:
                    raise EvidenceConflict("Frame was reused with different evidence")
                if vision_event_id is not None:
                    self._vision_events[vision_event_id] = canonical
                return RecordResult(object_id, int(self._objects[object_id]["backendRevision"]), True)

            latest = self._latest_frame.get(track_key)
            if latest is not None and int(evidence["frameId"]) < latest:
                raise EvidenceConflict("Frame is older than stored evidence for this track")

            samples = self._samples.setdefault(object_id, [])
            sample = sample_for(evidence)
            if samples and sample.received_at < samples[-1].received_at:
                raise EvidenceConflict("Observation receipt time is older than stored evidence")
            samples.append(sample)
            self._frames[frame_key] = canonical
            self._latest_frame[track_key] = int(evidence["frameId"])
            self._evidence[object_id] = dict(evidence)
            if vision_event_id is not None:
                self._vision_events[vision_event_id] = canonical
            self._publish_object_unlocked(
                object_id,
                vision_event_id=vision_event_id,
                producer_boot_id=producer_boot_id,
                entity_version=entity_version,
                vision_revision=vision_revision,
            )
            return RecordResult(object_id, int(self._objects[object_id]["backendRevision"]), False)

    def snapshot(self, session_id: str) -> dict[str, object]:
        with self._lock:
            self._refresh_unlocked()
            return {
                "schemaVersion": "backend.v1",
                "backendBootId": self.boot_id,
                "cursor": self._cursor_unlocked(session_id, self._seq),
                "objects": [dict(item) for item in self._objects.values()],
                "missions": [],
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
                vision_revision=self._objects[object_id].get("visionRevision"),
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
        body = {
            "objectId": object_id,
            "state": "OBSERVING",
            "cameraId": evidence["cameraId"],
            "sessionId": evidence["sessionId"],
            "geometryVersion": evidence["geometryVersion"],
            "trackId": evidence["trackId"],
            "frameId": evidence["frameId"],
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
            "detectorScore": None,
            "grokConfidence": None,
            "stationary": {
                "sampleCount": stationary.sample_count,
                "spanSeconds": stationary.span_seconds,
                "eligible": potential,
            },
            "potentialTarget": potential,
            "dispatchEligible": False,
            "eligibilityReason": reason,
            "backendRevision": revision,
            "producerBootId": producer_boot_id,
            "entityVersion": entity_version,
            "visionRevision": vision_revision,
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
            "objectId": object_id,
            "kind": kind,
            "reason": reason,
            "action": action,
            "backendRevision": revision,
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
