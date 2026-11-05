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

    def test_http_restart_replays_wire_payload_and_retains_event_alias(self):
        from fastapi.testclient import TestClient

        from backend.app import create_app
        from backend.postgres_projection import PostgresObservationLog

        def client():
            return TestClient(create_app(
                operator_token="operator-test-token", peer_token="mapping-test-token",
                cookie_secure=False, now=lambda: 101.0,
                observation_log=PostgresObservationLog(self.database),
            ))

        body = {
            "cameraId": self.context.camera_id, "sessionId": "session", "trackId": "track",
            "geometryVersion": "geometry", "frameId": 1, "mapId": "map",
            "mapVersion": "v1", "calibrationId": "cal", "calibrationValid": True,
            "capturedAt": 99.0, "receivedAt": 100.0, "valid": False, "invalidReason": "stale",
        }
        headers = {
            "Authorization": "Bearer mapping-test-token", "X-Vision-Event-Id": self.producer,
            "X-Producer-Boot-Id": "producer-boot", "X-Entity-Version": "2", "X-Vision-Revision": "3",
        }
        alias_headers = {**headers, "X-Vision-Event-Id": f"{self.producer}-alias"}
        with client() as before:
            self.assertEqual(before.post("/api/observations", json=body, headers=headers).status_code, 200)
            self.assertTrue(before.post("/api/observations", json=body, headers=alias_headers).json()["replayed"])
        with client() as after:
            self.assertEqual(after.post("/api/session", json={"token": "operator-test-token"}).status_code, 204)
            projected = next(
                item for item in after.get("/api/snapshot").json()["objects"]
                if item["cameraId"] == self.context.camera_id
            )
            self.assertEqual(projected["capturedAt"], 99.0)
            self.assertEqual(projected["mapping"]["invalidReason"], "stale")
            self.assertEqual(projected["producerBootId"], "producer-boot")
            self.assertEqual(projected["entityVersion"], 2)
            self.assertEqual(projected["revision"], 3)
            self.assertTrue(after.post("/api/observations", json=body, headers=headers).json()["replayed"])
            conflicting = {**body, "frameId": 2, "receivedAt": 101.0}
            self.assertEqual(
                after.post("/api/observations", json=conflicting, headers=alias_headers).status_code, 409,
            )

    def test_projection_loads_legacy_mapping_observation(self):
        from backend.postgres_projection import PostgresObservationLog

        self.store.record_observation("mapping", self.producer, self.sample, frame_id=1)
        body, source = next(
            (body, source) for body, source in PostgresObservationLog(self.database).load()
            if body["cameraId"] == self.context.camera_id
        )
        self.assertEqual(body["receivedAt"], self.sample.received_at)
        self.assertEqual(body["capturedAt"], self.sample.received_at)
        self.assertEqual(source, {"vision_event_id": self.producer})

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

    def test_vision_revocation_blocks_old_mapping_evidence_until_reacquired(self):
        from backend.state import BackendState
        from backend.stationarity import evaluate_stationarity
        from backend.tests.test_vision_ingest import publication
        from backend.vision_ingest import ACCEPTED, parse_vision_event
        from backend.vision_store import PostgresVisionLog

        for frame in range(11):
            object_id = self.store.record_observation(
                self.producer, f"revoke-frame-{frame}",
                replace(self.sample, received_at=100.0 + frame / 2), frame_id=frame,
            )
        with self.database() as connection:
            connection.execute("UPDATE rover_backend.objects SET state = '{\"pickupProfileId\":\"demo\"}' WHERE id=%s", (object_id,))
        vision = BackendState(now=lambda: 105.0, vision_log=PostgresVisionLog(self.database))
        boot = uuid4().hex
        wire_id = f"{self.context.camera_id}:session:track"

        class Mapping:
            calls = 0
            def plan(self, request, evidence):
                self.calls += 1
                return Route("fixture-revoked-plan", "fixture-rover", 106.0)

        mapping = Mapping()
        collection = PostgresCollectionStore(self.database, self.store.load, lambda: 105.0)
        for sequence, status in enumerate(["LOST", "POTENTIAL_TARGET", "EXCLUDED", "POTENTIAL_TARGET"], 1):
            message = publication(
                "object.updated", sequence, boot, cameraId=self.context.camera_id,
                sessionId="session", trackId="track", objectId=wire_id,
                geometryVersion="geometry", frameId=20 + sequence, entityVersion=sequence,
                status=status, potentialTarget=status == "POTENTIAL_TARGET", mapping=None,
            )
            vision.ingest_vision(parse_vision_event(message, ACCEPTED))
            if status == "POTENTIAL_TARGET":
                self.assertTrue(evaluate_stationarity(self.load(object_id).samples, evaluated_at=105.0).eligible)
                continue
            # Even another calibrated Mapping sample must not silently revive
            # a track that Vision still marks LOST/EXCLUDED.
            self.store.record_observation(self.producer, f"after-revoke-{sequence}",
                                          replace(self.sample, received_at=105.0), frame_id=11 + sequence)
            result = admit_collection(
                CollectionRequest(wire_id, "fixture-zone", "manual"), scope=self.scope,
                key=f"revoked-{sequence}", store=collection, mapping=mapping, now=lambda: 105.0,
            )
            self.assertEqual(result.outcome, "stationarity_required")
            self.assertEqual(mapping.calls, 0)
            with self.database() as connection:
                self.assertGreaterEqual(connection.execute(
                    "SELECT count(*) FROM rover_backend.observations WHERE object_id=%s", (object_id,)
                ).fetchone()[0], 11)


if __name__ == "__main__":
    unittest.main()
