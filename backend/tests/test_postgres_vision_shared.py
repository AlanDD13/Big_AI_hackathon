import json
import os
import unittest
from uuid import uuid4

from backend.postgres_evidence import PostgresEvidenceStore
from backend.postgres_projection import PostgresObservationLog
from backend.stationarity import MetricSample, TrackContext
from backend.vision_ingest import ParsedVisionEvent
from backend.vision_store import PostgresVisionLog

DATABASE_URL = os.environ.get("BACKEND_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "requires a migrated disposable PostgreSQL database")
class PostgresVisionSharedTests(unittest.TestCase):
    def setUp(self):
        self.scope = uuid4().hex
        self.context = TrackContext(f"camera-{self.scope}", "session", "geometry", "track", "map", "v1", "cal")
        self.evidence = PostgresEvidenceStore(self.database)
        self.vision = PostgresVisionLog(self.database)
        sample = MetricSample(self.context, 100.0, (1.0, 2.0), 0.01, True, True, True)
        self.object_id = self.evidence.record_observation(f"mapping-{self.scope}", "frame", sample, frame_id=1)
        self.classification = {"category": "TRASH", "subject_kind": "INANIMATE", "carrying": "NO", "confidence": "HIGH", "description": "bottle", "reason": "fixture"}

    def database(self):
        from psycopg import connect
        return connect(DATABASE_URL)

    def save(self, sequence, provider="fixture", classification=None):
        event_id = f"{self.scope}:{sequence}"
        event = ParsedVisionEvent(json.dumps({"id": event_id}, sort_keys=True, separators=(",", ":")), event_id, self.scope, sequence, "object.classified", None, None, None, None, None, None, None, None)
        self.vision.save(event, {"cameraId": self.context.camera_id, "sessionId": "session", "trackId": "track", "classificationStatus": "COMPLETED", "classification": self.classification if classification is None else classification, "classificationProvider": provider})

    def load(self, require_provider=True):
        with self.database() as connection:
            return self.evidence.load(connection, self.object_id, require_classification_provider=require_provider)

    def test_vision_upsert_preserves_backend_policy_and_mapping_evidence(self):
        with self.database() as connection:
            connection.execute("UPDATE rover_backend.objects SET state = %s::jsonb WHERE id = %s", (json.dumps({"pickupProfileId": "demo", "backendPolicy": {"key": "value"}}), self.object_id))
        before = self.load()
        self.save(1)
        result = self.load()
        self.assertEqual(result.pickup_profile_id, "demo")
        self.assertEqual(result.samples, before.samples)
        self.assertEqual(result.classification.category, "TRASH")
        self.assertEqual(result.classification.provider, "fixture")
        with self.database() as connection:
            state, data = connection.execute("SELECT state, data FROM rover_backend.objects JOIN rover_backend.classifications ON object_id = id WHERE id = %s", (self.object_id,)).fetchone()
        self.assertEqual(state["backendPolicy"], {"key": "value"})
        self.assertEqual(data["provider"], "fixture")
        self.assertEqual(data["category"], "TRASH")
        self.assertNotIn("classification", data)
        self.save(2, provider=None)
        self.assertIsNone(self.load().classification)

    def test_legacy_nested_and_flat_rows_restore_with_provider_gate(self):
        self.save(1)
        with self.database() as connection:
            connection.execute("UPDATE rover_backend.classifications SET data = %s::jsonb WHERE object_id = %s", (json.dumps({"classification": self.classification, "classificationProvider": "grok"}), self.object_id))
        self.assertEqual(self.load().classification.provider, "grok")
        projected = dict((object_id, data) for object_id, status, data in PostgresObservationLog(self.database).load_classifications())
        self.assertEqual(projected[f"{self.context.camera_id}:session:track"]["category"], "TRASH")
        with self.database() as connection:
            connection.execute("UPDATE rover_backend.classifications SET data = %s::jsonb WHERE object_id = %s", (json.dumps({"classification": self.classification, "classificationProvider": None}), self.object_id))
        self.assertIsNone(self.load().classification)
        self.assertEqual(self.load(False).classification.category, "TRASH")
        with self.database() as connection:
            connection.execute("UPDATE rover_backend.classifications SET data = %s::jsonb WHERE object_id = %s", (json.dumps({**self.classification, "provider": "fixture"}), self.object_id))
        self.assertEqual(self.load().classification.provider, "fixture")


if __name__ == "__main__":
    unittest.main()
