import os
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_vision_ingest import OPERATOR, PEER, publication
from backend.vision_store import PostgresVisionLog


DATABASE_URL = os.environ.get("BACKEND_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "requires a migrated disposable PostgreSQL database")
class PostgresDemoSectorTests(unittest.TestCase):
    def database(self):
        from psycopg import connect

        return connect(DATABASE_URL)

    def client(self):
        return TestClient(create_app(
            operator_token=OPERATOR,
            peer_token=PEER,
            cookie_secure=False,
            idle_timeout=0,
            now=lambda: 1100.0,
            vision_log=PostgresVisionLog(self.database),
        ))

    def test_distinct_display_pairs_survive_restart_without_metric_evidence(self):
        boot = uuid4().hex
        camera = f"demo-{boot}"
        expected = {"B3": "can", "C2": "crumpled paper"}
        with self.client() as client:
            for sequence, (sector, description) in enumerate(expected.items(), 1):
                track = f"fixture-track-{sequence}"
                event = publication(
                    "object.classified",
                    sequence,
                    boot,
                    objectId=f"{camera}:fixture-session:{track}",
                    cameraId=camera,
                    sessionId="fixture-session",
                    trackId=track,
                    frameId=7,
                    bboxPx=[100 * sequence - 10, 190, 100 * sequence + 10, 210],
                    mapping=None,
                    classificationStatus="COMPLETED",
                    classificationProvider="fixture",
                    classification={
                        "category": "TRASH",
                        "subject_kind": "INANIMATE",
                        "carrying": "NO",
                        "confidence": "MEDIUM",
                        "description": description,
                        "reason": "display fixture",
                    },
                    demoSector={
                        "mapId": "demo-grid",
                        "calibrated": False,
                        "convention": "ROW_A_TOP",
                        "grid": "convex-floor-0.5m",
                        "sector": sector,
                        "bboxCenterPx": [100 * sequence, 200],
                    },
                )
                response = client.post(
                    "/api/classifications", json=event, headers={"Authorization": f"Bearer {PEER}"}
                )
                self.assertEqual(response.status_code, 200, response.text)

        with self.client() as restarted:
            self.assertEqual(restarted.post("/api/session", json={"token": OPERATOR}).status_code, 204)
            objects = [
                item for item in restarted.get("/api/snapshot").json()["objects"]
                if item["cameraId"] == camera
            ]
        self.assertEqual(len(objects), 2)
        self.assertEqual(len({item["objectId"] for item in objects}), 2)
        self.assertEqual({
            item["demoSector"]["sector"]: item["classification"]["description"] for item in objects
        }, expected)
        for item in objects:
            self.assertFalse(item["demoSector"]["calibrated"])
            self.assertFalse(item["dispatchEligible"])
            self.assertIsNone(item["mapping"])
