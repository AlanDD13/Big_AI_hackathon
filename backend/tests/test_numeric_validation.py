"""Peer JSON numbers outside float range are validation errors, not crashes."""
import copy
import unittest

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.evidence import InvalidEvidence, parse_evidence
from backend.tests.test_api import OPERATOR, PEER, evidence
from backend.tests.test_vision_ingest import BOOT_A, publication


HUGE = 10 ** 1000


class NumericValidationTests(unittest.TestCase):
    def client(self):
        return TestClient(create_app(operator_token=OPERATOR, peer_token=PEER, cookie_secure=False),
                          raise_server_exceptions=False)

    def test_mapping_overflow_is_422_and_rejected_evidence_is_not_stored(self):
        with self.client() as client:
            for field, value in [("capturedAt", HUGE), ("receivedAt", HUGE),
                                 ("positionErrorM", HUGE), ("floorPointM", [HUGE, 0]),
                                 ("floorPointM", [0, -HUGE])]:
                body = {**evidence(1, 100.0), field: value}
                with self.subTest(field=field, negative=isinstance(value, list) and value[-1] == -HUGE):
                    response = client.post("/api/observations", json=body, headers={"Authorization": f"Bearer {PEER}"})
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()["error"]["code"], "INVALID_EVIDENCE")
                    with self.assertRaises(InvalidEvidence):
                        parse_evidence(body)
            response = client.post("/api/observations", json=evidence(1, 100.0), headers={"Authorization": f"Bearer {PEER}"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["backendRevision"], 1)

    def test_vision_overflow_is_422_and_event_identity_remains_available(self):
        valid = publication("object.updated", 1, BOOT_A)
        with self.client() as client:
            for field in ["timestamp", "capturedAt", "detectorScore", "metricStationarySeconds", "bboxPx"]:
                body = copy.deepcopy(valid)
                if field == "timestamp":
                    body[field] = HUGE
                else:
                    body["payload"][field] = [0, 0, HUGE, 20] if field == "bboxPx" else HUGE
                with self.subTest(field=field):
                    response = client.post("/api/vision-events", json=body, headers={"Authorization": f"Bearer {PEER}"})
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
            response = client.post("/api/vision-events", json=valid, headers={"Authorization": f"Bearer {PEER}"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["backendRevision"], 1)
