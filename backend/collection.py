"""Coordinate local collection admission independently of HTTP and providers."""

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

from backend.mission_policy import Classification, collection_rejection
from backend.stationarity import MetricSample, evaluate_stationarity


@dataclass(frozen=True)
class CollectionRequest:
    object_id: str
    destination_zone_id: str
    source: str


@dataclass(frozen=True)
class CollectionEvidence:
    revision: int
    samples: tuple[MetricSample, ...]
    classification: Classification | None
    pickup_profile_id: str | None


@dataclass(frozen=True)
class Route:
    plan_id: str
    rover_id: str
    valid_until: float


@dataclass(frozen=True)
class Admission:
    outcome: str
    mission_id: str | None = None
    mission_state: str | None = None
    status_revision: int | None = None
    classification: Classification | None = None


class CollectionStore(Protocol):
    def replay(
        self, scope: str, key: str, request: CollectionRequest
    ) -> Admission | None:
        """Resolve an existing key, including a request conflict."""

    def evidence(self, object_id: str) -> CollectionEvidence | None: ...

    def reserve(
        self,
        scope: str,
        key: str,
        request: CollectionRequest,
        *,
        evidence_revision: int,
        route: Route,
    ) -> Admission:
        """Recheck freshness, revision and expiry under lock before enqueue."""


class MappingClient(Protocol):
    def plan(self, request: CollectionRequest, evidence: CollectionEvidence) -> Route | None:
        """Return a valid candidate route or no plan."""


def admit_collection(
    request: CollectionRequest,
    *,
    scope: str,
    key: str,
    store: CollectionStore,
    mapping: MappingClient,
    now: Callable[[], float],
) -> Admission:
    replay = store.replay(scope, key, request)
    if replay is not None:
        return replay

    evidence = store.evidence(request.object_id)
    if evidence is None:
        return Admission("object_not_found")

    stationary = evaluate_stationarity(evidence.samples, evaluated_at=now())
    rejection = collection_rejection(
        stationary, evidence.classification, pickup_profile_id=evidence.pickup_profile_id
    )
    if rejection is not None:
        return Admission(rejection.value)

    try:
        route = mapping.plan(request, evidence)
    except (TimeoutError, ConnectionError):
        return Admission("mapping_unavailable")
    if route is None or not isfinite(route.valid_until) or now() >= route.valid_until:
        return Admission("route_unavailable")

    return store.reserve(
        scope, key, request, evidence_revision=evidence.revision, route=route
    )
