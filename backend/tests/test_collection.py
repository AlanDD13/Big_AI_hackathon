from dataclasses import replace
import unittest
from unittest.mock import patch

from backend.collection import (
    Admission,
    CollectionEvidence,
    CollectionRequest,
    Route,
    admit_collection,
)
from backend.mission_policy import Classification
from backend.stationarity import MetricSample, TrackContext


class FakeStore:
    def __init__(self, evidence):
        self.current_evidence = evidence
        self.requests = {}
        self.reservations = []

    def replay(self, scope, key, request):
        stored = self.requests.get((scope, key))
        if stored is None:
            return None
        original_request, mission_id = stored
        if original_request != request:
            return Admission("key_conflict")
        return Admission("replayed", mission_id)

    def evidence(self, object_id):
        return self.current_evidence

    def reserve(self, scope, key, request, *, evidence_revision, route):
        if evidence_revision != self.current_evidence.revision:
            return Admission("evidence_changed")
        self.reservations.append((request, route))
        self.requests[(scope, key)] = (request, "mission-1")
        return Admission("created", "mission-1")


class FakeMapping:
    def __init__(self):
        self.route = Route("plan-1", "rover-1", 6.0)
        self.calls = 0

    def plan(self, request, evidence):
        self.calls += 1
        return self.route


class CollectionTests(unittest.TestCase):
    def setUp(self):
        context = TrackContext("cam", "session", "geometry", "track", "map", "v1", "cal")
        samples = tuple(
            MetricSample(context, index / 2, (0.0, 0.0), 0.01, True, True, True)
            for index in range(11)
        )
        self.store = FakeStore(
            CollectionEvidence(
                1,
                samples,
                Classification("COMPLETED", "TRASH", "INANIMATE", "NO", "HIGH"),
                "demo",
            )
        )
        self.mapping = FakeMapping()
        self.request = CollectionRequest("object-1", "zone-1", "manual")

    def admit(self, *, request=None, now=5.0):
        return admit_collection(
            request or self.request,
            scope="operator",
            key="intent",
            store=self.store,
            mapping=self.mapping,
            now=lambda: now,
        )

    def test_eligible_observation_queues_once_and_replay_skips_stale_evidence(self):
        self.assertEqual(self.admit(), Admission("created", "mission-1"))
        self.assertEqual(self.admit(now=30.0), Admission("replayed", "mission-1"))
        changed = CollectionRequest("object-1", "zone-2", "manual")
        self.assertEqual(self.admit(request=changed), Admission("key_conflict"))
        self.assertEqual(self.mapping.calls, 1)
        self.assertEqual(len(self.store.reservations), 1)

    def test_stale_evidence_never_reaches_mapping(self):
        self.assertEqual(self.admit(now=6.1), Admission("stationarity_required"))
        self.assertEqual(self.mapping.calls, 0)
        self.assertEqual(self.store.reservations, [])

    def test_expired_route_never_reaches_reservation(self):
        self.mapping.route = Route("plan-1", "rover-1", 5.0)
        self.assertEqual(self.admit(), Admission("route_unavailable"))
        self.assertEqual(self.store.reservations, [])

    def test_evidence_changed_during_planning_cannot_be_reserved(self):
        def plan_after_revision_change(request, evidence):
            self.store.current_evidence = replace(evidence, revision=2)
            return self.mapping.route

        with patch.object(self.mapping, "plan", side_effect=plan_after_revision_change):
            self.assertEqual(self.admit(), Admission("evidence_changed"))
        self.assertEqual(self.store.reservations, [])
