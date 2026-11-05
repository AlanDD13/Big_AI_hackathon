"""Vision ingest projected for the dashboard. The robot is not called."""

import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from backend.app import create_app


OPERATOR = "operator-token-value"
PEER = "peer-token-value-ok"
FIXTURE = json.loads(
    Path(__file__).with_name("fixtures").joinpath("vision-recovery.json").read_text(encoding="utf-8")
)
BOOT_A = FIXTURE["producerBootA"]
BOOT_B = FIXTURE["producerBootB"]
ROUTES = {
    "object.observed": "/api/detections",
    "object.classified": "/api/classifications",
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


def case(case_id):
    return next(item for item in FIXTURE["cases"] if item["id"] == case_id)


def merged(event, **overrides):
    payload = dict(FIXTURE["sampleObject"])
    payload.update(event["payload"])
    payload.update(overrides)
    body = dict(event)
    body["payload"] = payload
    return body


def publication(event_type, sequence, boot, **overrides):
    payload = dict(FIXTURE["sampleObject"])
    payload["producerBootId"] = boot
    payload.update(overrides)
    return {
        "schemaVersion": "vision.v1",
        "producerBootId": boot,
        "eventId": f"{boot}:{sequence}",
        "sequence": sequence,
        "type": event_type,
        "timestamp": 1000.0 + sequence,
        "payload": payload,
    }


class VisionIngestTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(
            operator_token=OPERATOR,
            peer_token=PEER,
            cookie_secure=False,
            now=lambda: 1_100.0,
            boot_id="dddddddddddddddddddddddddddddddd",
            idle_timeout=0,
        )
        self.client = TestClient(self.app)

    def sign_in(self):
        response = self.client.post("/api/session", json={"token": OPERATOR})
        self.assertEqual(response.status_code, 204)

    def post_event(self, event):
        return self.client.post(
            ROUTES.get(event["type"], "/api/vision-events"),
            json=event,
            headers={"Authorization": f"Bearer {PEER}"},
        )

    def snapshot(self):
        return self.client.get("/api/snapshot").json()

    def only_object(self):
        objects = self.snapshot()["objects"]
        self.assertEqual(len(objects), 1)
        return objects[0]

    def test_pickup_rejection_persists_score_and_streams_one_frontend_alert(self):
        self.sign_in()
        before = self.snapshot()['cursor']
        event = publication('alert.pickup_unavailable', 1, BOOT_A, entityVersion=1,
            revision=1, reason='GRABBABILITY_LOW_OR_UNKNOWN', visualPickupStatus='COMPLETED',
            visualPickupAdvisory=dict(object_form='OTHER', visual_suitability='UNCERTAIN',
                reason='Unknown payload', advisory=True, pickupVerified=False,
                sourceFrameId=11, grabbability_score=.42))
        response = self.post_event(event)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.only_object()['visualPickupAdvisory']['grabbability_score'], .42)
        self.assertFalse(self.only_object()['dispatchEligible'])
        alerts = self.snapshot()['alerts']
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]['kind'], 'pickup_unavailable')
        self.assertEqual(alerts[0]['reason'], 'GRABBABILITY_LOW_OR_UNKNOWN')
        stream = parse_sse(self.client.get('/api/events', params={'after': before}).text)
        self.assertIn('alert.pickup_unavailable', [e['event'] for e in stream])
        self.assertEqual(self.post_event(event).status_code, 200)
        self.assertEqual(len(self.snapshot()['alerts']), 1)

    def test_grabbability_score_rejects_nonprobability_values(self):
        for score in (True, '0.9', -1, 1.1, 10**400):
            with self.subTest(score=score):
                event = publication('object.classified', 1, BOOT_A, entityVersion=1,
                    revision=1, visualPickupStatus='COMPLETED', visualPickupAdvisory=dict(
                        object_form='OTHER', visual_suitability='POSSIBLE', reason='Shape only',
                        advisory=True, pickupVerified=False, sourceFrameId=11, grabbability_score=score))
                self.assertEqual(self.post_event(event).status_code, 422)

    def test_peer_token_required(self):
        event = publication("object.observed", 1, BOOT_A, entityVersion=1, revision=0)
        missing = self.client.post("/api/detections", json=event)
        self.assertEqual(missing.status_code, 401)
        wrong = self.client.post(
            "/api/classifications",
            json=publication("object.classified", 1, BOOT_A),
            headers={"Authorization": f"Bearer {OPERATOR}"},
        )
        self.assertEqual(wrong.status_code, 401)
        alert = self.client.post("/api/vision-events", json=event)
        self.assertEqual(alert.status_code, 401)
        mismatched = self.client.post(
            "/api/detections",
            json=publication("object.updated", 1, BOOT_A),
            headers={"Authorization": f"Bearer {PEER}"},
        )
        self.assertEqual(mismatched.status_code, 422)

    def test_observed_is_visible_before_classification(self):
        self.sign_in()
        created = self.post_event(publication(
            "object.observed",
            1,
            BOOT_A,
            status="STATIC_PENDING_REVIEW",
            classification=None,
            classificationStatus="PENDING",
            classificationProvider=None,
            mapping=None,
            potentialTarget=False,
            eligibilityReason="NOT_ELIGIBLE",
            entityVersion=1,
            revision=0,
            metricStationarySeconds=0,
            frameId=0,
            frameRef="/api/frames/phone-1/session1/0",
        ))
        self.assertEqual(created.status_code, 200, created.text)
        self.assertFalse(created.json()["replayed"])
        observed = self.only_object()
        self.assertEqual(observed["status"], "OBSERVING")
        self.assertEqual(observed["state"], "OBSERVING")
        self.assertIsNone(observed["classification"])
        self.assertEqual(observed["classificationStatus"], "PENDING")
        self.assertIsNone(observed["mapping"])
        self.assertEqual(observed["detectorScore"], FIXTURE["sampleObject"]["detectorScore"])
        self.assertEqual(self.snapshot()["missions"], [])

    def test_classification_is_stored_separately_from_detector_score(self):
        self.sign_in()
        self.assertEqual(self.post_event(publication(
            "object.observed",
            1,
            BOOT_A,
            status="OBSERVING",
            classification=None,
            classificationStatus="PENDING",
            classificationProvider=None,
            mapping=None,
            potentialTarget=False,
            eligibilityReason="NOT_ELIGIBLE",
            entityVersion=1,
            revision=0,
            metricStationarySeconds=0,
        )).status_code, 200)
        classified = self.post_event(publication(
            "object.classified",
            2,
            BOOT_A,
            entityVersion=2,
            classificationStatus="COMPLETED",
            classification=FIXTURE["sampleObject"]["classification"],
            classificationProvider="fixture",
            status="STATIC_PENDING_REVIEW",
            potentialTarget=False,
            mapping=None,
        ))
        self.assertEqual(classified.status_code, 200, classified.text)
        observed = self.only_object()
        self.assertEqual(observed["objectId"], FIXTURE["sampleObject"]["objectId"])
        self.assertEqual(observed["classification"]["category"], "TRASH")
        self.assertEqual(observed["classification"]["description"], "Labelled fixture bottle")
        self.assertEqual(observed["classification"]["confidence"], "HIGH")
        self.assertIsInstance(observed["classification"]["confidence"], str)
        self.assertEqual(observed["classificationProvider"], "fixture")
        self.assertEqual(observed["grokConfidence"], "HIGH")
        self.assertEqual(observed["detectorScore"], 0.87)
        self.assertNotEqual(observed["classification"]["confidence"], observed["detectorScore"])
        mapped = self.post_event(publication(
            "object.updated",
            3,
            BOOT_A,
            entityVersion=3,
            mapping=FIXTURE["sampleObject"]["mapping"],
            potentialTarget=False,
            classificationStatus="COMPLETED",
            classification=FIXTURE["sampleObject"]["classification"],
            classificationProvider="fixture",
        ))
        self.assertEqual(mapped.status_code, 200, mapped.text)
        joined = self.only_object()
        self.assertEqual(joined["objectId"], observed["objectId"])
        self.assertEqual(joined["mapping"]["floorPointM"], [0.2, 0.3])
        self.assertEqual(joined["classification"]["confidence"], "HIGH")
        self.assertEqual(joined["detectorScore"], 0.87)

    def test_replay_does_not_double_publish(self):
        self.sign_in()
        cursor = self.snapshot()["cursor"]
        event = publication(
            "object.observed",
            1,
            BOOT_A,
            status="OBSERVING",
            classification=None,
            classificationStatus="PENDING",
            classificationProvider=None,
            mapping=None,
            potentialTarget=False,
            eligibilityReason="NOT_ELIGIBLE",
            entityVersion=1,
            revision=0,
        )
        first = self.post_event(event)
        self.assertEqual(first.status_code, 200, first.text)
        published = parse_sse(self.client.get("/api/events", params={"after": cursor}).text)
        self.assertEqual([item["event"] for item in published], ["object.updated"])
        replay = self.post_event(event)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(replay.json()["backendRevision"], first.json()["backendRevision"])
        again = parse_sse(
            self.client.get("/api/events", params={"after": published[-1]["id"]}).text
        )
        self.assertEqual(again, [])
        self.assertEqual(len(self.snapshot()["objects"]), 1)

    def test_missing_advisory_is_not_graspable(self):
        self.sign_in()
        event = merged(case("same_revision_entity_update")["events"][0])
        self.assertNotIn("visualPickupAdvisory", event["payload"])
        self.assertEqual(self.post_event(event).status_code, 200)
        observed = self.only_object()
        self.assertIsNone(observed["visualPickupAdvisory"])
        self.assertIsNone(observed["visualPickupStatus"])
        self.assertIsNot(observed.get("pickupVerified"), True)
        self.assertIsNot(observed.get("graspable"), True)
        self.assertNotEqual(observed.get("visual_suitability"), "POSSIBLE")

    def test_pickup_verified_stays_false(self):
        self.sign_in()
        event = publication(
            "object.updated",
            1,
            BOOT_A,
            entityVersion=1,
            revision=1,
            potentialTarget=True,
            visualPickupStatus="COMPLETED",
            visualPickupAdvisory={
                "object_form": "CRUMPLED",
                "visual_suitability": "POSSIBLE",
                "reason": "Visible shape only",
                "advisory": True,
                "pickupVerified": True,
                "sourceFrameId": 11,
            },
        )
        response = self.post_event(event)
        self.assertEqual(response.status_code, 200, response.text)
        advisory = self.only_object()["visualPickupAdvisory"]
        self.assertEqual(advisory["object_form"], "CRUMPLED")
        self.assertEqual(advisory["visual_suitability"], "POSSIBLE")
        self.assertTrue(advisory["advisory"])
        self.assertFalse(advisory["pickupVerified"])
        self.assertFalse(self.only_object()["dispatchEligible"])
        self.assertEqual(self.snapshot()["missions"], [])

    def test_dispatch_eligible_stays_false(self):
        self.sign_in()
        event = merged(case("same_revision_entity_update")["events"][0])
        self.assertTrue(event["payload"]["potentialTarget"])
        response = self.post_event(event)
        self.assertEqual(response.status_code, 200, response.text)
        observed = self.only_object()
        self.assertTrue(observed["potentialTarget"])
        self.assertFalse(observed["dispatchEligible"])
        self.assertEqual(self.snapshot()["missions"], [])
        self.assertEqual(self.snapshot()["robots"], [])

    def test_revoked_alert_once(self):
        self.sign_in()
        cursor = self.snapshot()["cursor"]
        events = [merged(item) for item in case("revocation")["events"]]
        self.assertEqual(events[0]["eventId"], events[1]["eventId"])
        self.assertEqual(events[0]["payload"]["action"], "HOLD_BEFORE_PICKUP")
        first = self.post_event(events[0])
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()["replayed"])
        replay = self.post_event(events[1])
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["replayed"])
        alerts = self.snapshot()["alerts"]
        self.assertEqual([alert["kind"] for alert in alerts], ["target_revoked"])
        self.assertEqual(alerts[0]["action"], "HOLD_BEFORE_PICKUP")
        self.assertEqual(alerts[0]["visionEventId"], events[0]["eventId"])
        self.assertFalse(self.only_object()["potentialTarget"])
        self.assertFalse(self.only_object()["dispatchEligible"])
        published = parse_sse(self.client.get("/api/events", params={"after": cursor}).text)
        self.assertEqual(
            [item["event"] for item in published if item["event"].startswith("alert.")],
            ["alert.target_revoked"],
        )
        trailing = parse_sse(
            self.client.get("/api/events", params={"after": published[-1]["id"]}).text
        )
        self.assertEqual(trailing, [])

    def test_potential_target_alert_once_per_revision(self):
        self.sign_in()
        first = self.post_event(publication(
            "alert.potential_target", 1, BOOT_A, entityVersion=1, revision=3, potentialTarget=True
        ))
        second = self.post_event(publication(
            "alert.potential_target", 2, BOOT_A, entityVersion=2, revision=3, potentialTarget=True
        ))
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual([alert["kind"] for alert in self.snapshot()["alerts"]], ["potential_target"])
        self.assertFalse(self.only_object()["dispatchEligible"])

    def test_frame_pending_does_not_revoke_or_realert(self):
        self.sign_in()
        opened = self.post_event(publication(
            "alert.potential_target", 5, BOOT_A, entityVersion=5, revision=3, potentialTarget=True
        ))
        self.assertEqual(opened.status_code, 200, opened.text)
        pending, restored = (merged(item) for item in case("frame_pending_same_episode")["events"])
        self.assertEqual(self.post_event(pending).status_code, 200)
        self.assertFalse(self.only_object()["potentialTarget"])
        self.assertEqual(self.only_object()["eligibilityReason"], "FRAME_PENDING")
        self.assertEqual(self.only_object()["revision"], 3)
        self.assertEqual([alert["kind"] for alert in self.snapshot()["alerts"]], ["potential_target"])
        self.assertEqual(self.post_event(restored).status_code, 200)
        self.assertTrue(self.only_object()["potentialTarget"])
        self.assertEqual(self.only_object()["status"], "POTENTIAL_TARGET")
        self.assertEqual([alert["kind"] for alert in self.snapshot()["alerts"]], ["potential_target"])
        self.assertFalse(self.only_object()["dispatchEligible"])

    def test_older_boot_is_discarded(self):
        self.sign_in()
        established = self.post_event(publication(
            "object.updated",
            4,
            BOOT_A,
            entityVersion=8,
            revision=4,
            potentialTarget=True,
            status="POTENTIAL_TARGET",
        ))
        self.assertEqual(established.status_code, 200, established.text)
        reset = merged(case("new_boot_reset")["events"][0])
        accepted = self.post_event(reset)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertFalse(accepted.json()["discarded"])
        current = self.only_object()
        self.assertEqual(current["producerBootId"], BOOT_B)
        self.assertEqual(current["entityVersion"], 1)
        self.assertEqual(current["status"], "OBSERVING")
        self.assertFalse(current["potentialTarget"])
        delayed = merged(case("delayed_old_boot")["events"][0])
        self.assertTrue(delayed["payload"]["potentialTarget"])
        rejected = self.post_event(delayed)
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertTrue(rejected.json()["discarded"])
        self.assertFalse(rejected.json()["replayed"])
        kept = self.only_object()
        self.assertEqual(kept["producerBootId"], BOOT_B)
        self.assertEqual(kept["entityVersion"], 1)
        self.assertFalse(kept["potentialTarget"])
        self.assertFalse(kept["dispatchEligible"])

    def test_same_revision_update_is_not_a_new_alert(self):
        self.sign_in()
        for event in case("same_revision_entity_update")["events"]:
            response = self.post_event(merged(event))
            self.assertEqual(response.status_code, 200, response.text)
        observed = self.only_object()
        self.assertEqual(observed["revision"], 3)
        self.assertEqual(observed["entityVersion"], 5)
        self.assertEqual(observed["frameId"], 12)
        self.assertEqual(self.snapshot()["alerts"], [])

    def test_sequence_gap_is_not_applied(self):
        self.sign_in()
        for event in case("same_revision_entity_update")["events"]:
            self.assertEqual(self.post_event(merged(event)).status_code, 200)
        before = self.only_object()
        gap = case("sequence_gap_and_complete_snapshot")["gap"]
        self.assertEqual(gap, {"lastApplied": 5, "rejected": 8})
        revoked = merged(case("revocation")["events"][0])
        self.assertEqual(revoked["sequence"], gap["rejected"])
        rejected = self.post_event(revoked)
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(self.only_object()["entityVersion"], before["entityVersion"])
        self.assertTrue(self.only_object()["potentialTarget"])
        self.assertEqual(self.snapshot()["alerts"], [])
        later = publication(
            "object.updated",
            9,
            BOOT_A,
            entityVersion=9,
            lastSeenAt=1009.0,
            potentialTarget=False,
        )
        self.assertEqual(self.post_event(later).status_code, 409)
        self.assertEqual(self.only_object()["lastSeenAt"], before["lastSeenAt"])

    def test_numeric_confidence_is_rejected(self):
        self.sign_in()
        self.post_event(publication(
            "object.observed",
            1,
            BOOT_A,
            status="OBSERVING",
            classification=None,
            classificationStatus="PENDING",
            classificationProvider=None,
            mapping=None,
            potentialTarget=False,
            eligibilityReason="NOT_ELIGIBLE",
            entityVersion=1,
            revision=0,
        ))
        bad = publication("object.classified", 2, BOOT_A, entityVersion=2)
        bad["payload"]["classification"] = dict(FIXTURE["sampleObject"]["classification"])
        bad["payload"]["classification"]["confidence"] = 0.87
        rejected = self.post_event(bad)
        self.assertEqual(rejected.status_code, 422)
        observed = self.only_object()
        self.assertIsNone(observed["classification"])
        self.assertEqual(observed["detectorScore"], 0.87)
        self.assertEqual(self.snapshot()["missions"], [])


class RecordingVisionLog:
    def __init__(self, stored=()):
        self.saved = []
        self.stored = list(stored)

    def save(self, event, body):
        self.saved.append((event.event_id, None if body is None else body["cameraId"]))

    def load(self):
        return self.stored


class VisionPersistenceTests(unittest.TestCase):
    def test_new_detection_is_saved_once_and_reloads(self):
        event = publication(
            "object.observed",
            1,
            BOOT_A,
            status="OBSERVING",
            classification=None,
            classificationStatus="PENDING",
            classificationProvider=None,
            mapping=None,
            potentialTarget=False,
            eligibilityReason="NOT_ELIGIBLE",
            entityVersion=1,
            revision=0,
        )
        log = RecordingVisionLog()
        app = create_app(
            operator_token=OPERATOR,
            peer_token=PEER,
            cookie_secure=False,
            now=lambda: 1_100.0,
            boot_id="dddddddddddddddddddddddddddddddd",
            idle_timeout=0,
            vision_log=log,
        )
        client = TestClient(app)
        created = client.post(
            "/api/detections",
            json=event,
            headers={"Authorization": f"Bearer {PEER}"},
        )
        self.assertEqual(created.status_code, 200, created.text)
        replay = client.post(
            "/api/detections",
            json=event,
            headers={"Authorization": f"Bearer {PEER}"},
        )
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(log.saved, [(event["eventId"], event["payload"]["cameraId"])])

        restored = create_app(
            operator_token=OPERATOR,
            peer_token=PEER,
            cookie_secure=False,
            now=lambda: 1_100.0,
            boot_id="eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            idle_timeout=0,
            vision_log=RecordingVisionLog([event]),
        )
        restored_client = TestClient(restored)
        self.assertEqual(restored_client.post("/api/session", json={"token": OPERATOR}).status_code, 204)
        snapshot = restored_client.get("/api/snapshot").json()
        self.assertEqual(snapshot["objects"][0]["cameraId"], event["payload"]["cameraId"])
        self.assertFalse(snapshot["objects"][0]["dispatchEligible"])
