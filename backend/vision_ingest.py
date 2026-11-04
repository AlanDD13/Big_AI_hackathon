"""Validate vision.v1 events before they enter the in-memory projection.

The producer publishes the envelope described by vision-event.schema.json.
These routes do not call the robot and do not open a mission.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from backend.evidence import InvalidEvidence, parse_evidence


class InvalidVision(ValueError):
    """The body is not a vision.v1 event for this route."""


class VisionConflict(ValueError):
    """The event identity or sequence cannot be applied."""


_BOOT_RE = re.compile(r"^[a-f0-9]{32}$")
_EVENT_RE = re.compile(r"^[a-f0-9]{32}:[1-9][0-9]*$")
_ASCII_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_OBJECT_RE = re.compile(r"^[A-Za-z0-9_-]+:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+$")
_REASON_RE = re.compile(r"^[A-Z0-9_]{1,64}$")

_ENVELOPE_KEYS = {
    "schemaVersion",
    "producerBootId",
    "eventId",
    "sequence",
    "type",
    "timestamp",
    "payload",
}
_OBJECT_REQUIRED = {
    "objectId",
    "cameraId",
    "sessionId",
    "geometryVersion",
    "trackId",
    "frameId",
    "capturedAt",
    "lastSeenAt",
    "cameraKind",
    "bboxPx",
    "detectorLabel",
    "detectorScore",
    "motionState",
    "pixelStationarySeconds",
    "metricStationarySeconds",
    "status",
    "exclusionReason",
    "classificationStatus",
    "classification",
    "classificationProvider",
    "mapping",
    "potentialTarget",
    "revision",
    "entityVersion",
    "producerBootId",
    "eligibilityReason",
    "frameRef",
}
_OBJECT_OPTIONAL = {"visualPickupStatus", "visualPickupAdvisory", "demoSector"}
_STATUSES = {"OBSERVING", "STATIC_PENDING_REVIEW", "POTENTIAL_TARGET", "EXCLUDED", "LOST"}
_MOTION = {"OBSERVING", "MOVING", "STATIONARY", "EXCLUDED", "UNKNOWN"}
_CLASS_STATUS = {"PENDING", "COMPLETED", "UNAVAILABLE"}
_CATEGORIES = {"TRASH", "POTENTIALLY_LOST_PROPERTY", "OTHER", "UNCERTAIN"}
_SUBJECTS = {"INANIMATE", "PERSON", "ANIMAL", "UNCERTAIN"}
_CARRYING = {"YES", "NO", "UNCERTAIN"}
_CONFIDENCE = {"LOW", "MEDIUM", "HIGH"}
_PROVIDERS = {"fixture", "grok"}
_FORMS = {"FLAT_SHEET", "CRUMPLED", "OTHER", "UNCERTAIN"}
_SUITABILITY = {"POSSIBLE", "UNSUITABLE", "UNCERTAIN"}
_GRASP = {"LIKELY", "UNLIKELY", "UNKNOWN"}
_PICKUP_STATUS = {"PENDING", "COMPLETED", "UNAVAILABLE"}

DETECTIONS = frozenset({"object.observed"})
CLASSIFICATIONS = frozenset({"object.classified"})
VISION_EVENTS = frozenset({
    "object.updated",
    "object.removed",
    "alert.potential_target",
    "alert.target_revoked",
    "alert.detector_unavailable",
    "alert.camera_unavailable",
    "camera.connected",
})
ACCEPTED = DETECTIONS | CLASSIFICATIONS | VISION_EVENTS
_OBJECT_TYPES = DETECTIONS | frozenset({
    "object.classified",
    "object.updated",
    "object.removed",
    "alert.potential_target",
    "alert.target_revoked",
})


@dataclass(frozen=True)
class ParsedVisionEvent:
    canonical: str
    event_id: str
    producer_boot_id: str
    sequence: int
    event_type: str
    object_id: str | None
    entity_version: int | None
    revision: int | None
    projection: dict[str, object] | None
    alert_kind: str | None
    alert_reason: str | None
    alert_action: str | None
    camera_id: str | None


def parse_vision_event(payload: object, allowed_types: frozenset[str]) -> ParsedVisionEvent:
    """Return one canonical event or raise InvalidVision."""
    if not isinstance(payload, dict):
        raise InvalidVision("Vision event must be an object")
    try:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InvalidVision("Vision event must be finite JSON") from exc
    unknown = set(payload) - _ENVELOPE_KEYS
    missing = _ENVELOPE_KEYS - set(payload)
    if unknown or missing:
        raise InvalidVision("Vision event has missing or unknown fields")
    if payload["schemaVersion"] != "vision.v1":
        raise InvalidVision("schemaVersion must be vision.v1")
    boot_id = _boot(payload["producerBootId"], "producerBootId")
    sequence = _integer(payload["sequence"], "sequence", minimum=1)
    event_id = payload["eventId"]
    if not isinstance(event_id, str) or _EVENT_RE.fullmatch(event_id) is None:
        raise InvalidVision("eventId must be {producerBootId}:{sequence}")
    if event_id != f"{boot_id}:{sequence}":
        raise InvalidVision("eventId must be {producerBootId}:{sequence}")
    event_type = payload["type"]
    if not isinstance(event_type, str) or event_type not in allowed_types:
        raise InvalidVision("Event type is not accepted on this route")
    timestamp = _number(payload["timestamp"], "timestamp", minimum=0)
    if not isinstance(payload["payload"], dict):
        raise InvalidVision("payload must be an object")
    body = payload["payload"]
    if event_type in _OBJECT_TYPES:
        projection = _object_projection(event_type, body, boot_id, event_id, timestamp)
        kind = "target_revoked" if event_type == "alert.target_revoked" else None
        if event_type == "alert.potential_target":
            kind = "potential_target"
        reason = body.get("reason") if event_type in {"alert.target_revoked", "object.removed"} else None
        action = "HOLD_BEFORE_PICKUP" if event_type == "alert.target_revoked" else None
        return ParsedVisionEvent(
            canonical,
            event_id,
            boot_id,
            sequence,
            event_type,
            str(projection["objectId"]),
            int(projection["entityVersion"]),
            int(projection["revision"]),
            projection,
            kind,
            str(reason) if reason is not None else None,
            action,
            None,
        )
    return _notice(canonical, event_id, boot_id, sequence, event_type, body)


def _object_projection(
    event_type: str,
    body: Mapping[str, object],
    boot_id: str,
    event_id: str,
    timestamp: float,
) -> dict[str, object]:
    allowed = set(_OBJECT_REQUIRED | _OBJECT_OPTIONAL)
    if event_type in {"object.removed", "alert.target_revoked"}:
        allowed.add("reason")
    if event_type == "alert.target_revoked":
        allowed.add("action")
    unknown = set(body) - allowed
    missing = _OBJECT_REQUIRED - set(body)
    if event_type == "object.removed" and "reason" not in body:
        missing.add("reason")
    if event_type == "alert.target_revoked":
        if "reason" not in body:
            missing.add("reason")
        if "action" not in body:
            missing.add("action")
    if unknown or missing:
        raise InvalidVision("Object payload has missing or unknown fields")
    if event_type in {"object.observed", "object.classified", "object.updated", "alert.potential_target"}:
        if "reason" in body or "action" in body:
            raise InvalidVision("This event must not carry reason or action")

    camera_id = _ascii(body["cameraId"], "cameraId")
    session_id = _ascii(body["sessionId"], "sessionId")
    track_id = _ascii(body["trackId"], "trackId")
    object_id = body["objectId"]
    if not isinstance(object_id, str) or _OBJECT_RE.fullmatch(object_id) is None:
        raise InvalidVision("objectId must be cameraId:sessionId:trackId")
    if object_id != f"{camera_id}:{session_id}:{track_id}":
        raise InvalidVision("objectId must be cameraId:sessionId:trackId")
    producer_boot = _boot(body["producerBootId"], "producerBootId")
    if producer_boot != boot_id:
        raise InvalidVision("payload producerBootId must match the envelope")

    status = _enum(body["status"], "status", _STATUSES)
    classification_status = _enum(body["classificationStatus"], "classificationStatus", _CLASS_STATUS)
    classification = _classification(body["classification"])
    provider = body["classificationProvider"]
    if provider is not None and provider not in _PROVIDERS:
        raise InvalidVision("classificationProvider must be fixture or grok")
    if event_type == "object.classified":
        if classification is None or provider not in _PROVIDERS:
            raise InvalidVision(
                "object.classified requires category, description, confidence, and classificationProvider"
            )
        if classification["confidence"] not in _CONFIDENCE:
            raise InvalidVision("confidence must be LOW, MEDIUM, or HIGH")
    if (
        event_type == "object.observed"
        and classification_status == "PENDING"
        and status not in {"EXCLUDED", "LOST"}
    ):
        status = "OBSERVING"

    bbox = _bbox(body["bboxPx"])
    demo_sector = _demo_sector(body["demoSector"], bbox) if "demoSector" in body else None
    if not (
        classification_status == "COMPLETED"
        and classification is not None
        and classification["category"] == "TRASH"
        and classification["subject_kind"] == "INANIMATE"
        and classification["carrying"] == "NO"
        and classification["confidence"] in {"MEDIUM", "HIGH"}
    ):
        demo_sector = None

    mapping = _mapping(body["mapping"])
    advisory_present = "visualPickupAdvisory" in body
    status_present = "visualPickupStatus" in body
    advisory = _advisory(body["visualPickupAdvisory"]) if advisory_present else None
    visual_status = None
    if status_present:
        visual_status = _enum(body["visualPickupStatus"], "visualPickupStatus", _PICKUP_STATUS)

    if event_type == "alert.target_revoked":
        _reason(body["reason"], "reason")
        if body["action"] != "HOLD_BEFORE_PICKUP":
            raise InvalidVision("alert.target_revoked action must be HOLD_BEFORE_PICKUP")
    if event_type == "object.removed":
        _reason(body["reason"], "reason")

    floor_point = None if mapping is None else mapping["floorPointM"]
    position_error = None if mapping is None else mapping["positionErrorM"]
    received_at = timestamp if mapping is None else mapping["receivedAt"]
    # Qualitative confidence stays a label. It is never combined with detectorScore.
    grok_confidence = None if classification is None else classification["confidence"]
    return {
        "objectId": object_id,
        "eventId": event_id,
        "visionEventId": event_id,
        "producerBootId": boot_id,
        "entityVersion": _integer(body["entityVersion"], "entityVersion", minimum=1),
        "revision": _integer(body["revision"], "revision", minimum=0),
        "state": status,
        "status": status,
        "cameraId": camera_id,
        "sessionId": session_id,
        "geometryVersion": _ascii(body["geometryVersion"], "geometryVersion"),
        "trackId": track_id,
        "frameId": _integer(body["frameId"], "frameId", minimum=0),
        "capturedAt": _number(body["capturedAt"], "capturedAt", minimum=0),
        "receivedAt": received_at,
        "lastSeenAt": _number(body["lastSeenAt"], "lastSeenAt", minimum=0),
        "cameraKind": _enum(body["cameraKind"], "cameraKind", {"FIXED", "ROVER"}),
        "bboxPx": bbox,
        "demoSector": demo_sector,
        "detectorLabel": _text(body["detectorLabel"], "detectorLabel", 128),
        "detectorScore": _score(body["detectorScore"]),
        "grokConfidence": grok_confidence,
        "motionState": _enum(body["motionState"], "motionState", _MOTION),
        "pixelStationarySeconds": _number(body["pixelStationarySeconds"], "pixelStationarySeconds", minimum=0),
        "metricStationarySeconds": _number(
            body["metricStationarySeconds"], "metricStationarySeconds", minimum=0
        ),
        "exclusionReason": _optional_text(body["exclusionReason"], "exclusionReason", 64),
        "floorPointM": floor_point,
        "positionErrorM": position_error,
        "mapping": mapping,
        "classificationStatus": classification_status,
        "classification": classification,
        "classificationProvider": provider,
        "potentialTarget": _bool(body["potentialTarget"], "potentialTarget"),
        "dispatchEligible": False,
        "eligibilityReason": _optional_reason(body["eligibilityReason"]),
        "frameRef": _text(body["frameRef"], "frameRef", 512),
        "visualPickupStatus": visual_status,
        "visualPickupAdvisory": advisory,
        "evidenceUrl": None,
        "stationary": {
            "sampleCount": 0,
            "spanSeconds": _number(body["metricStationarySeconds"], "metricStationarySeconds", minimum=0),
            "eligible": False,
        },
        "_visualStatusPresent": status_present,
        "_visualAdvisoryPresent": advisory_present,
    }


def _notice(
    canonical: str,
    event_id: str,
    boot_id: str,
    sequence: int,
    event_type: str,
    body: Mapping[str, object],
) -> ParsedVisionEvent:
    if event_type == "camera.connected":
        _exact_keys(body, {"cameraId"})
        camera_id = _ascii(body["cameraId"], "cameraId")
        return ParsedVisionEvent(
            canonical, event_id, boot_id, sequence, event_type, None, None, None, None, None, None, None, camera_id
        )
    if event_type == "alert.detector_unavailable":
        _exact_keys(body, {"cameraId", "reason"})
        camera_id = _ascii(body["cameraId"], "cameraId")
        reason = _text(body["reason"], "reason", 300)
        return ParsedVisionEvent(
            canonical, event_id, boot_id, sequence, event_type, None, None, None, None,
            "detector_unavailable", reason, None, camera_id,
        )
    if event_type == "alert.camera_unavailable":
        allowed = {"reason", "cameraId"}
        if set(body) - allowed or "reason" not in body:
            raise InvalidVision("Camera alert payload has missing or unknown fields")
        camera_id = _ascii(body["cameraId"], "cameraId") if "cameraId" in body else None
        return ParsedVisionEvent(
            canonical, event_id, boot_id, sequence, event_type, None, None, None, None,
            "camera_unavailable", _reason(body["reason"], "reason"), None, camera_id,
        )
    raise InvalidVision("Event type is not accepted on this route")


def _classification(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidVision("classification must be an object or null")
    required = {"category", "subject_kind", "carrying", "confidence", "description", "reason"}
    if set(value) != required:
        raise InvalidVision("classification has missing or unknown fields")
    confidence = value["confidence"]
    if confidence not in _CONFIDENCE:
        raise InvalidVision("confidence must be LOW, MEDIUM, or HIGH")
    return {
        "category": _enum(value["category"], "category", _CATEGORIES),
        "subject_kind": _enum(value["subject_kind"], "subject_kind", _SUBJECTS),
        "carrying": _enum(value["carrying"], "carrying", _CARRYING),
        "confidence": confidence,
        "description": _text(value["description"], "description", 500),
        "reason": _text(value["reason"], "reason", 500),
    }


def _demo_sector(value: object, bbox: list[float]) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InvalidVision("demoSector must be an object")
    _exact_keys(value, {"mapId", "calibrated", "convention", "grid", "sector", "bboxCenterPx"})
    if (value["mapId"] != "demo-grid" or value["calibrated"] is not False
            or value["convention"] != "ROW_A_TOP" or value["grid"] != "convex-floor-0.5m"):
        raise InvalidVision("demoSector has invalid map metadata")
    sector = value["sector"]
    if not isinstance(sector, str) or re.fullmatch(r"[A-D][1-4]", sector) is None:
        raise InvalidVision("demoSector sector must be A1-D4")
    center = value["bboxCenterPx"]
    if not isinstance(center, list) or len(center) != 2:
        raise InvalidVision("demoSector bboxCenterPx must contain two numbers")
    center = [_number(item, "bboxCenterPx", minimum=0) for item in center]
    if any(abs(actual - expected) > 1 for actual, expected in zip(
        center, ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    )):
        raise InvalidVision("demoSector bboxCenterPx must match bboxPx center")
    return {
        "mapId": "demo-grid", "calibrated": False, "convention": "ROW_A_TOP",
        "grid": "convex-floor-0.5m", "sector": sector,
        "bboxCenterPx": center,
    }


def _mapping(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidVision("mapping must be an object or null")
    try:
        return parse_evidence(value)
    except InvalidEvidence as exc:
        raise InvalidVision(str(exc)) from exc


def _advisory(value: object) -> dict[str, object] | None:
    """Store a visual advisory with pickupVerified forced false.

    A missing advisory is represented by None: no assessment, and not graspable.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidVision("visualPickupAdvisory must be an object or null")
    required = {"object_form", "visual_suitability", "reason", "advisory", "pickupVerified", "sourceFrameId"}
    optional = {"robot_grasp_estimate", "estimate_confidence", "assumptions", "required_checks"}
    if set(value) - required - optional or required - set(value):
        raise InvalidVision("visualPickupAdvisory has missing or unknown fields")
    if not isinstance(value["advisory"], bool) or not isinstance(value["pickupVerified"], bool):
        raise InvalidVision("visualPickupAdvisory flags must be booleans")
    stored: dict[str, object] = {
        "object_form": _enum(value["object_form"], "object_form", _FORMS),
        "visual_suitability": _enum(value["visual_suitability"], "visual_suitability", _SUITABILITY),
        "reason": _text(value["reason"], "reason", 500),
        "sourceFrameId": _integer(value["sourceFrameId"], "sourceFrameId", minimum=0),
        "advisory": True,
        "pickupVerified": False,
    }
    if "robot_grasp_estimate" in value:
        stored["robot_grasp_estimate"] = _enum(value["robot_grasp_estimate"], "robot_grasp_estimate", _GRASP)
    if "estimate_confidence" in value:
        stored["estimate_confidence"] = _enum(value["estimate_confidence"], "estimate_confidence", _CONFIDENCE)
    if "assumptions" in value:
        stored["assumptions"] = _phrases(value["assumptions"], "assumptions")
    if "required_checks" in value:
        stored["required_checks"] = _phrases(value["required_checks"], "required_checks")
    return stored


def _phrases(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 5:
        raise InvalidVision(f"{name} must be a list of at most 5 strings")
    return [_text(item, name, 200) for item in value]


def _bbox(value: object) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        raise InvalidVision("bboxPx must contain four numbers")
    return [_number(item, "bboxPx") for item in value]


def _exact_keys(body: Mapping[str, object], keys: set[str]) -> None:
    if set(body) != keys:
        raise InvalidVision("Payload has missing or unknown fields")


def _boot(value: object, name: str) -> str:
    if not isinstance(value, str) or _BOOT_RE.fullmatch(value) is None:
        raise InvalidVision(f"{name} must be 32 lowercase hex characters")
    return value


def _ascii(value: object, name: str) -> str:
    if not isinstance(value, str) or _ASCII_RE.fullmatch(value) is None:
        raise InvalidVision(f"{name} must be a 1-128 character ASCII id")
    return value


def _enum(value: object, name: str, choices: set[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise InvalidVision(f"{name} is not an allowed value")
    return value


def _reason(value: object, name: str) -> str:
    if not isinstance(value, str) or _REASON_RE.fullmatch(value) is None:
        raise InvalidVision(f"{name} must be an uppercase reason code")
    return value


def _optional_reason(value: object) -> str | None:
    if value is None:
        return None
    return _reason(value, "eligibilityReason")


def _text(value: object, name: str, limit: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        raise InvalidVision(f"{name} must be 1-{limit} characters")
    return value


def _optional_text(value: object, name: str, limit: int) -> str | None:
    if value is None:
        return None
    return _text(value, name, limit)


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise InvalidVision(f"{name} must be a boolean")
    return value


def _integer(value: object, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise InvalidVision(f"{name} must be an integer >= {minimum}")
    return value


def _number(value: object, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidVision(f"{name} must be a finite number")
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        raise InvalidVision(f"{name} must be a finite number")
    if minimum is not None and number < minimum:
        raise InvalidVision(f"{name} must be >= {minimum}")
    if maximum is not None and number > maximum:
        raise InvalidVision(f"{name} must be <= {maximum}")
    return number


def _score(value: object) -> float:
    return _number(value, "detectorScore", minimum=0, maximum=1)
