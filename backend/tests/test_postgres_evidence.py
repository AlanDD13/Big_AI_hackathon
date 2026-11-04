import os
import unittest
from dataclasses import replace
from uuid import UUID, uuid4

from backend.collection import CollectionRequest, Route, admit_collection
from backend.postgres_collection import PostgresCollectionStore
from backend.stationarity import MetricSample, TrackContext


DATABASE_URL = os.environ.get("BACKEND_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "requires a migrated disposable PostgreSQL database")
class PostgresEvidenceTests(unittest.TestCase):
    def setUp(self):
        from backend.postgres_evidence import PostgresEvidenceStore

        self.scope = str(uuid4())
        self.producer = f"evidence-test-{self.scope}"
        self.context = TrackContext(
            f"camera-{self.scope}", "session", "geometry", "track", "map", "v1", "cal"
        )
        self.sample = MetricSample(
            self.context, 100.0, (1.25, 2.5), 0.01, True, True, True
        )
        self.store = PostgresEvidenceStore(self.database)

    def database(self):
        from psycopg import connect

        return connect(DATABASE_URL)

    def load(self, object_id):
        with self.database() as connection:
            return self.store.load(connection, object_id)

    def test_canonical_identity_and_exact_event_retry(self):
        first = self.store.record_observation(self.producer, "event-1", self.sample, frame_id=1)
        self.assertEqual(str(UUID(first)), first)
        initial = self.load(first)
        self.assertEqual(initial.samples, (self.sample,))

        self.assertEqual(
            self.store.record_observation(self.producer, "event-1", self.sample, frame_id=1),
            first,
        )
        self.assertEqual(self.load(first), initial)
        second_sample = replace(self.sample, received_at=101.0, point_m=(1.26, 2.5))
        self.assertEqual(
            self.store.record_observation(self.producer, "event-2", second_sample, frame_id=2),
            first,
        )
        evidence = self.load(first)
        self.assertEqual(evidence.samples, (self.sample, second_sample))
        self.assertGreater(evidence.revision, initial.revision)

    def test_conflicting_event_is_rejected_without_changing_evidence(self):
        object_id = self.store.record_observation(self.producer, "event-1", self.sample, frame_id=1)
        initial = self.load(object_id)
        with self.assertRaises(ValueError):
            self.store.record_observation(
                self.producer, "event-1", replace(self.sample, point_m=(9.0, 9.0)), frame_id=1
            )
        self.assertEqual(self.load(object_id), initial)

    def test_frame_retry_and_older_frame_do_not_advance_evidence(self):
        object_id = self.store.record_observation(self.producer, "event-2", self.sample, frame_id=2)
        initial = self.load(object_id)
        self.assertEqual(
            self.store.record_observation(self.producer, "retry", self.sample, frame_id=2),
            object_id,
        )
        self.assertEqual(self.load(object_id), initial)
        older = replace(self.sample, received_at=99.0, point_m=(3.0, 4.0))
        with self.assertRaisesRegex(ValueError, "older"):
            self.store.record_observation(self.producer, "older", older, frame_id=1)
        self.assertEqual(self.load(object_id), initial)

    def test_load_returns_samples_in_receipt_order_and_missing_object_is_none(self):
        self.assertIsNone(self.load(str(uuid4())))
        samples = tuple(replace(self.sample, received_at=100.0 + i) for i in range(3))
        object_ids = [
            self.store.record_observation(self.producer, f"event-{i}", sample, frame_id=i)
            for i, sample in enumerate(samples)
        ]
        self.assertEqual(len(set(object_ids)), 1)
        self.assertEqual(self.load(object_ids[0]).samples, samples)

    def test_stored_evidence_can_queue_collection(self):
        for frame_id in range(11):
            sample = replace(self.sample, received_at=100.0 + frame_id / 2)
            object_id = self.store.record_observation(
                self.producer, f"frame-{frame_id}", sample, frame_id=frame_id
            )

        with self.database() as connection:
            connection.execute(
                """UPDATE rover_backend.objects SET state = '{"pickupProfileId":"demo"}'
                   WHERE id = %s""",
                (object_id,),
            )
            connection.execute(
                """INSERT INTO rover_backend.classifications (object_id, status, data)
                   VALUES (%s, 'completed',
                           '{"category":"TRASH","subject_kind":"INANIMATE",
                             "carrying":"NO","confidence":"HIGH"}')""",
                (object_id,),
            )

        class FakeMapping:
            def plan(self, request, evidence):
                return Route("fixture-plan", "fixture-rover", 106.0)

        store = PostgresCollectionStore(self.database, self.store.load, lambda: 105.0)
        result = admit_collection(
            CollectionRequest(object_id, "fixture-zone", "manual"),
            scope=self.scope,
            key="collect",
            store=store,
            mapping=FakeMapping(),
            now=lambda: 105.0,
        )
        self.assertEqual(result.outcome, "created")
        with self.database() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT state, collection_plan ->> 'planId' FROM rover_backend.missions WHERE id = %s",
                    (result.mission_id,),
                ).fetchone(),
                ("queued", "fixture-plan"),
            )


if __name__ == "__main__":
    unittest.main()
