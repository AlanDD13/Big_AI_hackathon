"""Validate one mapping observation before it enters backend state."""

from __future__ import annotations

import re
from math import isfinite
from typing import Mapping

from backend.stationarity import MetricSample, TrackContext


ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
INVALID_REASONS = {
    "uncalibrated",
    "missing_contact",
    "frame_unavailable",
    "stale",
    "out_of_area",
    "rover_confirmation_only",
    "unknown",
}
REQUIRED = {
    "cameraId",
    "sessionId",
    "geometryVersion",
    "frameId",
    "trackId",
    "mapId",
    "mapVersion",
    "calibrationId",
    "calibrationValid",
    "capturedAt",
    "receivedAt",
    "valid",
    "invalidReason",
}
OPTIONAL = {"floorPointM", "positionErrorM"}


class InvalidEvidence(ValueError):
    """The body is not the mapping evidence contract."""


class EvidenceConflict(ValueError):
    """The frame or event identity was reused with a different result."""


def _id(payload: Mapping[str, object], name: str) -> str:
    value = payload[name]
    if not isinstance(value, str) or ID_RE.fullmatch(value) is None:
        raise InvalidEvidence(f"{name} must be a 1-128 character ASCII id")
    return value


def _finite_number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidEvidence(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError:
        raise InvalidEvidence(f"{name} must be a finite number") from None
    if not isfinite(number):
        raise InvalidEvidence(f"{name} must be a finite number")
    return number


def parse_evidence(payload: object) -> dict[str, object]:
    """Return a canonical mapping observation or raise InvalidEvidence."""
    if not isinstance(payload, dict):
        raise InvalidEvidence("Mapping evidence must be an object")
    unknown = set(payload) - REQUIRED - OPTIONAL
    missing = REQUIRED - set(payload)
    if unknown or missing:
        raise InvalidEvidence("Mapping evidence has missing or unknown fields")

    parsed: dict[str, object] = {name: _id(payload, name) for name in (
        "cameraId", "sessionId", "geometryVersion", "trackId", "mapId", "mapVersion", "calibrationId"
    )}
    frame_id = payload["frameId"]
    if isinstance(frame_id, bool) or not isinstance(frame_id, int) or frame_id < 0:
        raise InvalidEvidence("frameId must be a nonnegative integer")
    parsed["frameId"] = frame_id

    for name in ("capturedAt", "receivedAt"):
        number = _finite_number(payload[name], name)
        if number < 0:
            raise InvalidEvidence(f"{name} must be zero or greater")
        parsed[name] = number

    if not isinstance(payload["calibrationValid"], bool):
        raise InvalidEvidence("calibrationValid must be a boolean")
    if not isinstance(payload["valid"], bool):
        raise InvalidEvidence("valid must be a boolean")
    parsed["calibrationValid"] = payload["calibrationValid"]
    parsed["valid"] = payload["valid"]

    point = payload.get("floorPointM", None)
    error = payload.get("positionErrorM", None)
    reason = payload["invalidReason"]
    if payload["valid"]:
        if payload["calibrationValid"] is not True:
            raise InvalidEvidence("A valid observation requires calibrationValid true")
        if (
            not isinstance(point, list)
            or len(point) != 2
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in point)
        ):
            raise InvalidEvidence("A valid observation requires a two-number floor point")
        point_m = (_finite_number(point[0], "floorPointM"), _finite_number(point[1], "floorPointM"))
        if error is None:
            raise InvalidEvidence("A valid observation requires positionErrorM")
        error_m = _finite_number(error, "positionErrorM")
        if error_m < 0:
            raise InvalidEvidence("positionErrorM must be zero or greater")
        if reason is not None:
            raise InvalidEvidence("A valid observation requires invalidReason null")
        parsed["floorPointM"] = [point_m[0], point_m[1]]
        parsed["positionErrorM"] = error_m
        parsed["invalidReason"] = None
    else:
        if point is not None or error is not None:
            raise InvalidEvidence("An invalid observation requires a null point and error")
        if not isinstance(reason, str) or reason not in INVALID_REASONS:
            raise InvalidEvidence("An invalid observation requires a known invalidReason")
        parsed["floorPointM"] = None
        parsed["positionErrorM"] = None
        parsed["invalidReason"] = reason
    return parsed


def object_id_for(evidence: Mapping[str, object]) -> str:
    return f"{evidence['cameraId']}:{evidence['sessionId']}:{evidence['trackId']}"


def sample_for(evidence: Mapping[str, object]) -> MetricSample:
    point = evidence["floorPointM"]
    error = evidence["positionErrorM"]
    return MetricSample(
        TrackContext(
            str(evidence["cameraId"]),
            str(evidence["sessionId"]),
            str(evidence["geometryVersion"]),
            str(evidence["trackId"]),
            str(evidence["mapId"]),
            str(evidence["mapVersion"]),
            str(evidence["calibrationId"]),
        ),
        received_at=float(evidence["receivedAt"]),
        point_m=None if point is None else (float(point[0]), float(point[1])),
        position_error_m=None if error is None else float(error),
        is_fixed_camera=evidence["invalidReason"] != "rover_confirmation_only",
        position_valid=bool(evidence["valid"]),
        calibration_valid=bool(evidence["calibrationValid"]),
    )
