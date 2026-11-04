import json
import unittest

from fastapi.testclient import TestClient

from backend.app import create_app


OPERATOR = "operator-token-value"
PEER = "peer-token-value-ok"


def evidence(frame, received, *, valid=True, reason=None):
    return {
        "cameraId": "phone-1",
        "sessionId": "session1",
        "geometryVersion": "geom1",
        "frameId": frame,
        "trackId": "track1",
        "mapId": "map1",
        "mapVersion": "v1",
        "calibrationId": "cal1",
        "calibrationValid": valid,
        "capturedAt": received,
        "receivedAt": received,
        "valid": valid,
        "invalidReason": None if valid else reason,
        "floorPointM": [1.0, 1.0] if valid else None,
        "positionErrorM": 0.01 if valid else None,
    }


def parse_sse(text):
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        item = {"id": None, "event": None, "data": None}
        for line in block.split("\n"):
            if line.startswith("id: "):
                item["id"] = line[4:]
            elif line.startswith("event: "):
                item["event"] = line[7:]
            elif line.startswith("data: "):
                item["data"] = json.loads(line[6:])
        events.append(item)
    return events


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.clock = {"now": 1_000.0}
        self.app = create_app(
            operator_token=OPERATOR,
            peer_token=PEER,
            cookie_secure=False,
            now=lambda: self.clock["now"],
            boot_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            idle_timeout=0,
        )
        self.client = TestClient(self.app)

    def sign_in(self):
        response = self.client.post("/api/session", json={"token": OPERATOR})
        self.assertEqual(response.status_code, 204)
        return response

    def test_session_snapshot_and_secure_cookie_contract(self):
        rejected = self.client.post("/api/session", json={"token": "wrong-token-value"})
        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.json()["error"]["code"], "UNAUTHORIZED")

        secure_app = create_app(
            operator_token=OPERATOR,
            peer_token=PEER,
            cookie_secure=True,
            boot_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
        opened = TestClient(secure_app).post("/api/session", json={"token": OPERATOR})
        cookie = opened.headers["set-cookie"]
        self.assertIn("session=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Max-Age=28800", cookie)
        self.assertNotIn(OPERATOR, cookie)

        self.sign_in()
        snapshot = self.client.get("/api/snapshot")
        self.assertEqual(snapshot.status_code, 200)
        body = snapshot.json()
        self.assertEqual(body["schemaVersion"], "backend.v1")
        self.assertEqual(body["backendBootId"], "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        self.assertEqual(body["objects"], [])
        self.assertEqual(body["missions"], [])
        self.assertEqual(body["robots"], [])
        self.assertEqual(body["alerts"], [])
        self.assertTrue(body["cursor"])

        self.client.delete("/api/session")
        self.assertEqual(self.client.get("/api/snapshot").status_code, 401)

    def test_observation_auth_validation_and_replay(self):
        self.sign_in()
        missing = self.client.post("/api/observations", json=evidence(1, 1000))
        self.assertEqual(missing.status_code, 401)
        cookie_only = self.client.post(
            "/api/observations",
            json=evidence(1, 1000),
            headers={"Authorization": f"Bearer {OPERATOR}"},
        )
        self.assertEqual(cookie_only.status_code, 401)

        created = self.client.post(
            "/api/observations",
            json=evidence(1, 1000),
            headers={"Authorization": f"Bearer {PEER}", "X-Vision-Event-Id": "boot:1"},
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["objectId"], "phone-1:session1:track1")
        self.assertFalse(created.json()["replayed"])

        replay = self.client.post(
            "/api/observations",
            json=evidence(1, 1000),
            headers={"Authorization": f"Bearer {PEER}", "X-Vision-Event-Id": "boot:1"},
        )
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["backendRevision"], created.json()["backendRevision"])

        conflict = self.client.post(
            "/api/observations",
            json=evidence(1, 1000.2),
            headers={"Authorization": f"Bearer {PEER}"},
        )
        self.assertEqual(conflict.status_code, 409)
        older = self.client.post(
            "/api/observations",
            json=evidence(0, 999),
            headers={"Authorization": f"Bearer {PEER}"},
        )
        self.assertEqual(older.status_code, 409)

        invalid = evidence(2, 1001, valid=False, reason=None)
        rejected = self.client.post(
            "/api/observations",
            json=invalid,
            headers={"Authorization": f"Bearer {PEER}"},
        )
        self.assertEqual(rejected.status_code, 422)

        snapshot = self.client.get("/api/snapshot").json()
        observed = snapshot["objects"][0]
        self.assertEqual(observed["state"], "OBSERVING")
        self.assertEqual(observed["floorPointM"], [1.0, 1.0])
        self.assertEqual(observed["mapping"]["floorPointM"], [1.0, 1.0])
        self.assertFalse(observed["potentialTarget"])
        self.assertFalse(observed["dispatchEligible"])
        self.assertIsNone(observed["detectorScore"])
        self.assertIsNone(observed["grokConfidence"])
        self.assertEqual(observed["eligibilityReason"], "stationarity_required")

        demo = self.client.post(
            "/api/observations",
            json={"sector": "A1", "demoPointM": [0.5, 0.5], "mapId": "demo-grid", "calibrated": False},
            headers={"Authorization": f"Bearer {PEER}"},
        )
        self.assertEqual(demo.status_code, 422)

    def test_sse_cursor_precedence_expiry_and_evidence(self):
        self.sign_in()
        cursor = self.client.get("/api/snapshot").json()["cursor"]
        self.client.post(
            "/api/observations",
            json=evidence(1, self.clock["now"]),
            headers={"Authorization": f"Bearer {PEER}"},
        )
        first = parse_sse(self.client.get("/api/events", params={"after": cursor}).text)
        self.assertEqual(first[0]["event"], "object.updated")
        self.assertEqual(first[0]["id"], first[0]["data"]["id"])
        self.assertEqual(first[0]["data"]["entity"], "object")
        self.assertIn("body", first[0]["data"])
        self.assertNotIn("\nid: ", "\n" + self.client.get("/api/events", params={"after": "missing"}).text)

        self.clock["now"] = 1001
        self.client.post(
            "/api/observations",
            json=evidence(2, self.clock["now"]),
            headers={"Authorization": f"Bearer {PEER}"},
        )
        replay = parse_sse(
            self.client.get(
                "/api/events",
                params={"after": cursor},
                headers={"Last-Event-ID": first[-1]["id"]},
            ).text
        )
        self.assertTrue(replay)
        self.assertTrue(all(item["data"]["body"]["frameId"] == 2 for item in replay if item["event"] == "object.updated"))

        required = self.client.get("/api/events", params={"after": "missing"}).text
        parsed = parse_sse(required)
        self.assertEqual(parsed, [{
            "id": None,
            "event": "snapshot_required",
            "data": {"reason": "REPLAY_UNAVAILABLE"},
        }])
        self.assertNotIn("id:", required)

        self.clock["now"] += 901
        expired = parse_sse(self.client.get("/api/events", params={"after": cursor}).text)
        self.assertEqual(expired[0]["event"], "snapshot_required")

        self.clock["now"] = 1000
        self.sign_in()
        fresh = self.client.get("/api/snapshot").json()["cursor"]
        self.client.cookies.clear()
        self.sign_in()
        foreign = parse_sse(self.client.get("/api/events", params={"after": fresh}).text)
        self.assertEqual(foreign[0]["event"], "snapshot_required")

        gone = self.client.get("/api/evidence/evidence-1")
        self.assertEqual(gone.status_code, 404)
        self.assertEqual(gone.json()["error"]["code"], "EVIDENCE_UNAVAILABLE")
        self.assertNotIn("location", {name.lower() for name in gone.headers})

    def test_stationarity_alerts_once_per_transition(self):
        self.sign_in()
        for index in range(11):
            received = 1000.0 + index * 0.5
            self.clock["now"] = received
            response = self.client.post(
                "/api/observations",
                json=evidence(index + 1, received),
                headers={"Authorization": f"Bearer {PEER}"},
            )
            self.assertEqual(response.status_code, 200, response.text)
        snapshot = self.client.get("/api/snapshot").json()
        self.assertTrue(snapshot["objects"][0]["potentialTarget"])
        self.assertFalse(snapshot["objects"][0]["dispatchEligible"])
        self.assertEqual([alert["kind"] for alert in snapshot["alerts"]], ["potential_target"])

        self.clock["now"] = 1005.5
        again = self.client.post(
            "/api/observations",
            json=evidence(12, 1005.5),
            headers={"Authorization": f"Bearer {PEER}"},
        )
        self.assertFalse(again.json()["replayed"])
        self.assertEqual(
            [alert["kind"] for alert in self.client.get("/api/snapshot").json()["alerts"]],
            ["potential_target"],
        )

        self.clock["now"] = 1006.0
        revoked = self.client.post(
            "/api/observations",
            json=evidence(13, 1006.0, valid=False, reason="stale"),
            headers={"Authorization": f"Bearer {PEER}"},
        )
        self.assertEqual(revoked.status_code, 200)
        alerts = self.client.get("/api/snapshot").json()["alerts"]
        self.assertEqual([alert["kind"] for alert in alerts], ["potential_target", "target_revoked"])
        self.assertEqual(alerts[1]["action"], "HOLD_BEFORE_PICKUP")
        self.assertFalse(self.client.get("/api/snapshot").json()["objects"][0]["potentialTarget"])


class RecordingLog:
    def __init__(self):
        self.saved = []

    def save(self, evidence, **source):
        self.saved.append((evidence["frameId"], source["vision_event_id"]))

    def load(self):
        return []


class PostgresWiringTests(unittest.TestCase):
    def test_new_observation_is_saved_once(self):
        log = RecordingLog()
        clock = {"now": 1000.0}
        app = create_app(
            operator_token=OPERATOR,
            peer_token=PEER,
            cookie_secure=False,
            now=lambda: clock["now"],
            boot_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            idle_timeout=0,
            observation_log=log,
        )
        client = TestClient(app)
        self.assertEqual(client.post("/api/session", json={"token": OPERATOR}).status_code, 204)
        created = client.post(
            "/api/observations",
            json=evidence(1, 1000),
            headers={"Authorization": f"Bearer {PEER}", "X-Vision-Event-Id": "boot:1"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        replay = client.post(
            "/api/observations",
            json=evidence(1, 1000),
            headers={"Authorization": f"Bearer {PEER}", "X-Vision-Event-Id": "boot:1"},
        )
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(log.saved, [(1, "boot:1")])
