"""Postgres admission with object locking and a second policy check."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
import json
from math import isfinite
from typing import TYPE_CHECKING

from backend.collection import Admission, CollectionEvidence, CollectionRequest, Route
from backend.mission_policy import collection_rejection
from backend.stationarity import evaluate_stationarity

if TYPE_CHECKING:
    from psycopg import Connection


class PostgresCollectionStore:
    def __init__(
        self,
        connect: Callable[[], Connection],
        load_evidence: Callable[[Connection, str], CollectionEvidence | None],
        now: Callable[[], float],
    ) -> None:
        """Load backend-owned evidence on the provided connection without network I/O."""
        self._connect = connect
        self._load_evidence = load_evidence
        self._now = now

    @staticmethod
    def _replay(
        connection: Connection, scope: str, key: str, request: CollectionRequest
    ) -> Admission | None:
        row = connection.execute(
            """SELECT id, request_data = %s::jsonb
               FROM rover_backend.missions
               WHERE request_scope = %s AND idempotency_key = %s""",
            (json.dumps(asdict(request)), scope, key),
        ).fetchone()
        if row is None:
            return None
        mission_id, matches = row
        return Admission("replayed", str(mission_id)) if matches else Admission("key_conflict")

    def replay(self, scope: str, key: str, request: CollectionRequest) -> Admission | None:
        with self._connect() as connection:
            return self._replay(connection, scope, key, request)

    def evidence(self, object_id: str) -> CollectionEvidence | None:
        with self._connect() as connection:
            return self._load_evidence(connection, object_id)

    def reserve(
        self,
        scope: str,
        key: str,
        request: CollectionRequest,
        *,
        evidence_revision: int,
        route: Route,
    ) -> Admission:
        with self._connect() as connection, connection.transaction():
            # Writers of object evidence take the same row lock before updates.
            locked = connection.execute(
                "SELECT id FROM rover_backend.objects WHERE id = %s FOR UPDATE",
                (request.object_id,),
            ).fetchone()
            replay = self._replay(connection, scope, key, request)
            if replay is not None:
                return replay
            if locked is None:
                return Admission("object_not_found")

            evidence = self._load_evidence(connection, request.object_id)
            if evidence is None or evidence.revision != evidence_revision:
                return Admission("evidence_changed")

            evaluated_at = self._now()
            rejection = collection_rejection(
                evaluate_stationarity(evidence.samples, evaluated_at=evaluated_at),
                evidence.classification,
                pickup_profile_id=evidence.pickup_profile_id,
            )
            if rejection is not None:
                return Admission(rejection.value)
            if not isfinite(route.valid_until) or evaluated_at >= route.valid_until:
                return Admission("route_unavailable")

            outcome, mission_id, _ = connection.execute(
                "SELECT * FROM rover_backend.enqueue_mission(%s, %s, %s, %s, %s::jsonb)",
                (
                    request.object_id,
                    route.rover_id,
                    scope,
                    key,
                    json.dumps(asdict(request)),
                ),
            ).fetchone()
            if outcome == "created":
                connection.execute(
                    "UPDATE rover_backend.missions SET collection_plan = %s::jsonb WHERE id = %s",
                    (
                        json.dumps({"planId": route.plan_id, "validUntil": route.valid_until}),
                        mission_id,
                    ),
                )
            return Admission(outcome, str(mission_id) if mission_id is not None else None)
