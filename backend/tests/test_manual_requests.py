"""Pure request parsing; no bridge, DB, socket or robot interaction."""
import unittest

from backend.manual_requests import InvalidManualRequest, parse_manual_request


def intent(**changes):
    return {"roverId": "rover-1", "leaseId": "lease-1", "leaseGeneration": 1,
            "bridgeBootId": "a" * 32,
            "sequence": 1, "issuedAt": 100.0, "expiresAt": 100.2,
            "xMps": 0.2, "yMps": 0.0, "zDegps": 0.0, "frame": "BODY", **changes}


class ManualRequestTests(unittest.TestCase):
    def parse(self, action, body):
        return parse_manual_request(action, body, now=100.05)

    def test_canonical_actions_copy_without_changing_ids_or_timestamps(self):
        bodies = {
            "lease": {"roverId": "rover-1", "requestedTtlMs": 2000},
            "drive": intent(),
            "release": {"roverId": "rover-1", "leaseId": "lease-1", "leaseGeneration": 1, "bridgeBootId": "a" * 32, "reason": "blur"},
            "stop": {"roverId": "rover-1", "reason": "operator_stop"},
            "rearm": {"roverId": "rover-1", "bridgeBootId": "a" * 32},
        }
        for action, body in bodies.items():
            with self.subTest(action=action):
                canonical = self.parse(action, body)
                self.assertEqual(canonical, body)
                self.assertIsNot(canonical, body)

    def test_velocity_and_time_fail_closed(self):
        for field, value in [
            ("xMps", 0.351), ("yMps", -0.351), ("zDegps", 45.1),
            ("xMps", float("nan")), ("yMps", float("inf")), ("zDegps", True),
            ("xMps", 10 ** 1000),
            ("issuedAt", True), ("issuedAt", 100.1), ("expiresAt", 100.05),
            ("expiresAt", 100.501), ("expiresAt", 99), ("sequence", True),
            ("sequence", 0), ("sequence", 1.5), ("frame", "MAP"),
            ("leaseGeneration", 0), ("leaseGeneration", True),
        ]:
            with self.subTest(field=field, value=value):
                with self.assertRaises(InvalidManualRequest):
                    self.parse("drive", intent(**{field: value}))
        self.assertEqual(self.parse("drive", intent(xMps=-0.35, yMps=0.35, zDegps=-45.0))["zDegps"], -45.0)

    def test_shape_identity_ttl_and_reason_validation(self):
        cases = [
            ("lease", {"roverId": "rover-1", "requestedTtlMs": True}),
            ("lease", {"roverId": "rover-1", "requestedTtlMs": 10001}),
            ("lease", {"roverId": "rover-1", "requestedTtlMs": 0}),
            ("drive", intent(roverId="../not-an-id")),
            ("drive", intent(leaseId="")),
            ("drive", intent(extra=1)),
            ("stop", {"roverId": "rover-1", "reason": "start"}),
            ("release", {"roverId": "rover-1", "leaseId": "lease-1", "leaseGeneration": 1, "reason": "anything"}),
            ("release", {"roverId": "rover-1", "leaseId": "lease-1", "leaseGeneration": 1, "reason": []}),
            ("rearm", {"roverId": "rover-1", "leaseId": "old"}),
            ("pickup", {"roverId": "rover-1"}),
            ("lease", None), ("lease", []),
        ]
        for action, body in cases:
            with self.subTest(action=action, body=body):
                with self.assertRaises(InvalidManualRequest):
                    self.parse(action, body)


if __name__ == "__main__":
    unittest.main()
