import os
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.mission_http import FixtureMapping
from backend.postgres_collection import PostgresCollectionStore
from backend.postgres_evidence import PostgresEvidenceStore
from backend.postgres_projection import PostgresObservationLog
from backend.tests.test_api import OPERATOR, PEER, evidence, parse_sse


DATABASE_URL = os.environ.get("BACKEND_TEST_DATABASE_URL")


class MissionRequestTests(unittest.TestCase):
    def test_operator_auth_and_validation(self):
        app = create_app(operator_token=OPERATOR, peer_token=PEER, cookie_secure=False)
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/missions", json={}).status_code, 401)
            self.assertEqual(client.post("/api/session", json={"token": OPERATOR}).status_code, 204)
            response = client.post("/api/missions", json={}, headers={"Idempotency-Key": "mission-key"})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")


@unittest.skipUnless(DATABASE_URL, "requires a migrated disposable PostgreSQL database")
class MissionQueueTests(unittest.TestCase):
    def database(self):
        from psycopg import connect

        return connect(DATABASE_URL)

    def app(self, boot_id):
        now = lambda: 105.0
        store = PostgresEvidenceStore(self.database)
        return create_app(
            operator_token=OPERATOR,
            peer_token=PEER,
            cookie_secure=False,
            idle_timeout=0,
            now=now,
            boot_id=boot_id,
            observation_log=PostgresObservationLog(self.database),
            collection_store=PostgresCollectionStore(
                self.database,
                lambda conn, object_id: store.load(conn, object_id, require_classification_provider=True),
                now,
            ),
            mapping_client=FixtureMapping(now),
        )

    def test_queue_replay_sse_and_restart(self):
        camera = f"queue-{uuid4()}"
        body = {"objectId": f"{camera}:session1:track1", "destinationZoneId": "zone-1", "source": "manual"}
        headers = {"Idempotency-Key": "queue-intent"}
        with TestClient(self.app("a" * 32)) as client:
            for frame in range(11):
                sample = {**evidence(frame, 100.0 + frame / 2), "cameraId": camera}
                response = client.post("/api/observations", json=sample, headers={"Authorization": f"Bearer {PEER}"})
                self.assertEqual(response.status_code, 200, response.text)

        with self.database() as connection:
            object_id, = connection.execute(
                """UPDATE rover_backend.objects SET state = '{"pickupProfileId":"demo"}'
                   WHERE camera_id = %s RETURNING id""", (camera,),
            ).fetchone()
            connection.execute(
                """INSERT INTO rover_backend.classifications (object_id, status, data)
                   VALUES (%s, 'completed',
                           '{"category":"TRASH","subject_kind":"INANIMATE",
                             "carrying":"NO","confidence":"HIGH","provider":"fixture"}')""",
                (object_id,),
            )

        with TestClient(self.app("b" * 32)) as client:
            self.assertEqual(client.post("/api/session", json={"token": OPERATOR}).status_code, 204)
            initial = client.get("/api/snapshot").json()
            target = next(item for item in initial["objects"] if item["objectId"] == body["objectId"])
            self.assertEqual(target["classificationProvider"], "fixture")
            self.assertFalse(target["dispatchEligible"])
            missing = client.post(
                "/api/missions", json={**body, "objectId": "absent:session:track"}, headers=headers
            )
            self.assertEqual((missing.status_code, missing.json()["error"]["code"]), (404, "OBJECT_NOT_FOUND"))
            created = client.post("/api/missions", json=body, headers=headers)
            self.assertEqual(created.status_code, 202, created.text)
            mission = created.json()
            self.assertEqual(mission, {
                **body, "missionId": mission["missionId"], "state": "QUEUED",
                "backendBootId": "b" * 32, "backendRevision": 1,
            })
            replay = client.post("/api/missions", json=body, headers=headers)
            self.assertEqual((replay.status_code, replay.json()), (200, mission))
            conflict = client.post("/api/missions", json={**body, "destinationZoneId": "zone-2"}, headers=headers)
            self.assertEqual((conflict.status_code, conflict.json()["error"]["code"]), (409, "IDEMPOTENCY_CONFLICT"))
            reserved = client.post("/api/missions", json=body, headers={"Idempotency-Key": "other-intent"})
            self.assertEqual((reserved.status_code, reserved.json()["error"]["code"]), (409, "EVIDENCE_CHANGED"))
            snapshot = client.get("/api/snapshot").json()
            self.assertIn(mission, snapshot["missions"])
            stream = client.get("/api/events", params={"after": initial["cursor"]})
            events = [item for item in parse_sse(stream.text) if item["event"] == "mission.updated"]
            self.assertEqual(len(events), 1)
            self.assertIsNotNone(events[0]["id"])
            self.assertEqual(events[0]["data"], mission)

        with TestClient(self.app("c" * 32)) as client:
            self.assertEqual(client.post("/api/session", json={"token": OPERATOR}).status_code, 204)
            self.assertIn({**mission, "backendBootId": "c" * 32}, client.get("/api/snapshot").json()["missions"])
        with self.database() as connection:
            self.assertEqual(connection.execute(
                "SELECT state, command_id FROM rover_backend.missions WHERE id = %s", (mission["missionId"],)
            ).fetchone(), ("queued", None))
