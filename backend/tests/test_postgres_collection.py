import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

from backend.collection import (
    Admission,
    CollectionEvidence,
    CollectionRequest,
    Route,
    admit_collection,
)
from backend.mission_policy import Classification
from backend.stationarity import MetricSample, TrackContext


DATABASE_URL = os.environ.get("BACKEND_TEST_DATABASE_URL")


class FakeMapping:
    def __init__(self, route):
        self.route = route
        self.calls = 0
        self.before_return = None

    def plan(self, request, evidence):
        self.calls += 1
        if self.before_return is not None:
            self.before_return()
        return self.route


@unittest.skipUnless(DATABASE_URL, "requires a migrated disposable PostgreSQL database")
class PostgresCollectionTests(unittest.TestCase):
    def setUp(self):
        from backend.postgres_collection import PostgresCollectionStore

        self.object_id = str(uuid4())
        self.request_scope = f"test-collection-{self.object_id}"
        self.request = CollectionRequest(self.object_id, "fixture-zone", "manual")
        self.route = Route("fixture-plan", str(uuid4()), 8.0)
        self.mapping = FakeMapping(self.route)
        self.clock = 5.0
        self.evidence_reads = []
        context = TrackContext(
            "test-camera", "test-session", "geometry", self.object_id, "map", "v1", "cal"
        )
        self.samples = tuple(
            MetricSample(context, frame / 2, (0.0, 0.0), 0.01, True, True, True)
            for frame in range(11)
        )
        self.store = PostgresCollectionStore(self.database, self.load_evidence, self.now)
        with self.database() as connection:
            connection.execute(
                """INSERT INTO rover_backend.objects (id, camera_id, session_id, track_id, state)
                   VALUES (%s, 'test-camera', 'test-session', %s, '{}')""",
                (self.object_id, self.object_id),
            )

    def database(self):
        from psycopg import connect

        return connect(DATABASE_URL)

    def now(self):
        return self.clock

    def load_evidence(self, connection, object_id):
        self.evidence_reads.append(str(object_id))
        row = connection.execute(
            "SELECT revision FROM rover_backend.objects WHERE id = %s", (object_id,)
        ).fetchone()
        if row is None:
            return None
        return CollectionEvidence(
            row[0],
            self.samples,
            Classification("COMPLETED", "TRASH", "INANIMATE", "NO", "HIGH"),
            "test-profile",
        )

    def admit(self, request=None, *, key="fixture-intent"):
        return admit_collection(
            request or self.request,
            scope=self.request_scope,
            key=key,
            store=self.store,
            mapping=self.mapping,
            now=self.now,
        )

    def missions(self):
        with self.database() as connection:
            return connection.execute(
                """SELECT id::text, state, rover_id, command_id, collection_plan
                   FROM rover_backend.missions WHERE request_scope = %s""",
                (self.request_scope,),
            ).fetchall()

    def test_persists_route_and_replays_before_checking_stale_evidence(self):
        first = self.admit()
        self.assertEqual(first.outcome, "created")
        self.assertEqual(first.mission_state, "queued")
        self.assertIsNotNone(first.mission_id)
        expected = [
            (
                first.mission_id,
                "queued",
                self.route.rover_id,
                None,
                {"planId": self.route.plan_id, "validUntil": self.route.valid_until},
            )
        ]
        self.assertEqual(self.missions(), expected)
        evidence_reads_before_replay = list(self.evidence_reads)

        self.clock = 30.0
        self.assertEqual(self.admit(), Admission("replayed", first.mission_id, "queued", 0))
        conflicting = replace(self.request, destination_zone_id="other-zone")
        self.assertEqual(self.admit(conflicting), Admission("key_conflict"))
        self.assertEqual(self.mapping.calls, 1)
        self.assertEqual(self.evidence_reads, evidence_reads_before_replay)
        self.assertEqual(self.missions(), expected)

    def test_resolves_dashboard_id_and_replays_current_state(self):
        wire_id = f"test-camera:test-session:{self.object_id}"
        wire_request = replace(self.request, object_id=wire_id)

        first = self.admit(wire_request)
        self.assertEqual(first.outcome, "created")
        self.assertEqual(first.mission_state, "queued")
        self.assertEqual(self.evidence_reads, [self.object_id, self.object_id])
        self.assertEqual(self.admit(), Admission("key_conflict"))

        with self.database() as connection:
            connection.execute(
                "UPDATE rover_backend.missions SET state = 'cancelled' WHERE id = %s",
                (first.mission_id,),
            )
            stored_object_id, stored_request_object_id = connection.execute(
                """SELECT object_id::text, request_data ->> 'object_id'
                   FROM rover_backend.missions WHERE id = %s""",
                (first.mission_id,),
            ).fetchone()
        self.assertEqual(stored_object_id, self.object_id)
        self.assertEqual(stored_request_object_id, wire_id)
        self.assertEqual(self.admit(wire_request), Admission("replayed", first.mission_id, "cancelled", 1))
        self.assertEqual(self.mapping.calls, 1)

    def test_unknown_dashboard_object_never_reaches_planner(self):
        request = replace(self.request, object_id="unknown-camera:unknown-session:unknown-track")
        self.assertEqual(self.admit(request), Admission("object_not_found"))
        self.assertEqual(self.mapping.calls, 0)
        self.assertEqual(self.missions(), [])

    def test_two_requests_for_same_dashboard_object_reserve_once(self):
        request = replace(
            self.request, object_id=f"test-camera:test-session:{self.object_id}"
        )
        planned = Barrier(2, timeout=5)
        self.mapping.before_return = planned.wait

        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(self.admit, request, key="first-intent")
            second = workers.submit(self.admit, request, key="second-intent")
            results = [first.result(timeout=10), second.result(timeout=10)]

        self.assertCountEqual(
            [result.outcome for result in results], ["created", "object_reserved"]
        )
        stored = self.missions()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0][1], "queued")
        self.assertIsNone(stored[0][3])

    def test_revision_change_during_mapping_prevents_reservation(self):
        def advance_object_revision():
            with self.database() as connection:
                connection.execute(
                    "UPDATE rover_backend.objects SET revision = revision + 1 WHERE id = %s",
                    (self.object_id,),
                )

        self.mapping.before_return = advance_object_revision
        self.assertEqual(self.admit(), Admission("evidence_changed"))
        self.assertEqual(self.mapping.calls, 1)
        self.assertEqual(self.missions(), [])

    def test_evidence_aging_during_mapping_prevents_reservation(self):
        def age_evidence():
            self.clock = 6.25

        self.mapping.before_return = age_evidence
        self.assertEqual(self.admit(), Admission("stationarity_required"))
        self.assertEqual(self.mapping.calls, 1)
        self.assertEqual(self.missions(), [])


if __name__ == "__main__":
    unittest.main()
