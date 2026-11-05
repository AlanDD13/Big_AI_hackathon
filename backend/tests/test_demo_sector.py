"""Synthetic fixture coverage for the display-only demo sector."""
import copy
import unittest

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_vision_ingest import BOOT_A, PEER, OPERATOR, publication
from backend.vision_ingest import ACCEPTED, InvalidVision, parse_vision_event


def sector(name="B3"):
    return {"mapId": "demo-grid", "calibrated": False, "convention": "ROW_A_TOP",
            "grid": "convex-floor-0.5m", "sector": name, "bboxCenterPx": [35, 55]}


def event(**changes):
    return publication("object.updated", 1, BOOT_A, demoSector=sector(), **changes)


class DemoSectorTests(unittest.TestCase):
    def test_validation(self):
        for key, bad in [("mapId", "other"), ("calibrated", 0), ("convention", "other"),
                         ("grid", "other"), ("sector", "E1"), ("sector", "A5"),
                         ("bboxCenterPx", [-1, 0]), ("bboxCenterPx", [True, 0]),
                         ("bboxCenterPx", [37, 55]),
                         ("bboxCenterPx", [1]), ("bboxCenterPx", [float("inf"), 0])]:
            with self.subTest(key=key, bad=bad):
                item = event()
                item["payload"]["demoSector"][key] = bad
                with self.assertRaises(InvalidVision):
                    parse_vision_event(item, ACCEPTED)
        for bad in [None, {}, {**sector(), "extra": 1}]:
            with self.subTest(value=bad):
                item = event()
                item["payload"]["demoSector"] = bad
                with self.assertRaises(InvalidVision):
                    parse_vision_event(item, ACCEPTED)

    def test_only_confirmed_trash_is_projected(self):
        valid = event()
        self.assertEqual(parse_vision_event(valid, ACCEPTED).projection["demoSector"], sector())
        for key, value in [("category", "OTHER"), ("subject_kind", "PERSON"),
                           ("carrying", "YES"), ("confidence", "LOW")]:
            item = copy.deepcopy(valid)
            item["payload"]["classification"][key] = value
            self.assertIsNone(parse_vision_event(item, ACCEPTED).projection["demoSector"])
        for status in ["PENDING", "UNAVAILABLE"]:
            item = copy.deepcopy(valid)
            item["payload"]["classificationStatus"] = status
            self.assertIsNone(parse_vision_event(item, ACCEPTED).projection["demoSector"])
        item = copy.deepcopy(valid)
        item["payload"]["classification"]["confidence"] = "MEDIUM"
        self.assertIsNotNone(parse_vision_event(item, ACCEPTED).projection["demoSector"])

    def test_two_synthetic_objects_and_next_frame(self):
        app = create_app(operator_token=OPERATOR, peer_token=PEER, cookie_secure=False,
                         now=lambda: 1100.0, boot_id="d" * 32, idle_timeout=0)
        client = TestClient(app)
        self.assertEqual(client.post("/api/session", json={"token": OPERATOR}).status_code, 204)
        headers = {"Authorization": f"Bearer {PEER}"}
        # These track IDs and descriptions are synthetic fixture values, not live source IDs.
        for sequence, track, label, location in [(1, "fixture-can", "can", "B3"),
                                                  (2, "fixture-paper", "crumpled paper", "C2")]:
            item = publication("object.updated", sequence, BOOT_A, trackId=track,
                               objectId=f"phone-1:session1:{track}", detectorLabel=label,
                               mapping=None, demoSector=sector(location), entityVersion=1)
            self.assertEqual(client.post("/api/vision-events", json=item, headers=headers).status_code, 200)
        objects = client.get("/api/snapshot").json()["objects"]
        self.assertEqual({obj["trackId"]: obj["demoSector"]["sector"] for obj in objects},
                         {"fixture-can": "B3", "fixture-paper": "C2"})
        item = publication("object.updated", 3, BOOT_A, trackId="fixture-can",
                           objectId="phone-1:session1:fixture-can", mapping=None,
                           frameId=12, entityVersion=2)
        self.assertEqual(client.post("/api/vision-events", json=item, headers=headers).status_code, 200)
        objects = {obj["trackId"]: obj for obj in client.get("/api/snapshot").json()["objects"]}
        self.assertIsNone(objects["fixture-can"]["demoSector"])
        self.assertEqual(objects["fixture-paper"]["demoSector"]["sector"], "C2")
        self.assertFalse(objects["fixture-can"]["dispatchEligible"])


if __name__ == "__main__":
    unittest.main()
