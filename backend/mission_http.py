"""Request validation and local planner choices for the mission queue."""

from collections.abc import Callable
import re

from backend.collection import CollectionEvidence, CollectionRequest, Route
from backend.evidence import ID_RE


IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
MISSION_ERRORS = {
    "key_conflict": (409, "IDEMPOTENCY_CONFLICT", "This key belongs to a different mission request"),
    "object_not_found": (404, "OBJECT_NOT_FOUND", "The object was not found"),
    "object_reserved": (409, "EVIDENCE_CHANGED", "The object is already reserved"),
    "stationarity_required": (409, "STATIONARITY_REQUIRED", "Fresh stationary evidence is required"),
    "classification_required": (409, "CLASSIFICATION_REQUIRED", "A completed classification is required"),
    "classification_excluded": (409, "CLASSIFICATION_EXCLUDED", "The classification does not permit collection"),
    "pickup_profile_required": (409, "PICKUP_PROFILE_REQUIRED", "A pickup profile is required"),
    "evidence_changed": (409, "EVIDENCE_CHANGED", "Object evidence changed during planning"),
    "route_unavailable": (409, "ROUTE_UNAVAILABLE", "No usable route is available"),
    "mapping_unavailable": (503, "MAPPING_UNAVAILABLE", "The mapping service is unavailable"),
}


def parse_mission_request(payload: object) -> CollectionRequest:
    if not isinstance(payload, dict) or set(payload) != {"objectId", "destinationZoneId", "source"}:
        raise ValueError("Expected objectId, destinationZoneId and source")
    object_id = payload["objectId"]
    if not isinstance(object_id, str) or len(object_id) > 386:
        raise ValueError("objectId must contain camera, session and track IDs")
    parts = object_id.split(":")
    if len(parts) != 3 or not all(ID_RE.fullmatch(part) for part in parts):
        raise ValueError("objectId must contain camera, session and track IDs")
    zone = payload["destinationZoneId"]
    if not isinstance(zone, str) or ID_RE.fullmatch(zone) is None:
        raise ValueError("destinationZoneId must be a 1-128 character ASCII id")
    if payload["source"] != "manual":
        raise ValueError("source must be manual")
    return CollectionRequest(object_id, zone, "manual")


class UnavailableMapping:
    def plan(self, request: CollectionRequest, evidence: CollectionEvidence) -> Route:
        raise ConnectionError("No mapping service is configured")


class FixtureMapping:
    def __init__(self, now: Callable[[], float]) -> None:
        self._now = now

    def plan(self, request: CollectionRequest, evidence: CollectionEvidence) -> Route:
        return Route("fixture-queue-plan", "fixture-rover", self._now() + 30)
