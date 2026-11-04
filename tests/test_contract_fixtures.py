"""Shared contract fixtures parse and name the decisions peers asked to freeze."""
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "contracts"


def load(name):
    return json.loads((ROOT / name).read_text())


class ContractFixtureTest(unittest.TestCase):
    def test_robot_cases_cover_the_requested_races(self):
        fixture = load("fixtures/robot-simulator.json")
        self.assertFalse(fixture["hardwareValidated"])
        self.assertFalse(fixture["bridgeImplemented"])
        self.assertEqual(
            {case["id"] for case in fixture["cases"]},
            {
                "hold_before_pickup", "pickup_before_hold", "priority_stop_during_picking",
                "expired_authorization", "conflicting_authorization", "duplicate_authorization",
                "lost_stop_acknowledgment", "rearm_does_not_resume",
            },
        )

    def test_manual_control_is_not_slice_one(self):
        fixture = load("fixtures/manual-control.json")
        self.assertEqual(fixture["status"], "MANUAL_CHASSIS_SLICE_1")
        self.assertEqual(fixture["platform"], "rmepcore")
        self.assertEqual(fixture["soleDjiOwner"], "platonyesin-dev")
        self.assertTrue(fixture["sdkTimeoutIsHostTimer"])
        self.assertTrue(fixture["implementRoutes"])
        self.assertTrue(fixture["liveChassisDriving"])
        self.assertEqual(fixture["overheadNavigationMode"], "SIMULATED_ONLY")
        self.assertEqual(fixture["manipulationMode"], "SIMULATED_ONLY")
        self.assertEqual(fixture["caps"]["watchdogTimeoutMs"], 300)
        self.assertEqual(fixture["caps"]["maxAbsXMps"], 0.35)
        self.assertEqual(fixture["caps"]["maxAbsYMps"], 0.35)
        self.assertEqual(fixture["caps"]["maxAbsZDegps"], 45.0)
        self.assertIn("POST /api/manual/lease", fixture["routes"]["browserToBackend"])
        self.assertIn("POST /manual/drive", fixture["routes"]["backendToBridge"])
        self.assertIn("pixel", fixture["overheadRequest"])
        self.assertNotIn("xMps", fixture["overheadRequest"])

    def test_backend_snapshot_required_has_no_id(self):
        fixture = load("fixtures/backend-stream.json")
        self.assertIsNone(fixture["snapshotRequired"]["id"])
        self.assertEqual(fixture["snapshotRequired"]["event"], "snapshot_required")
        self.assertEqual(len(fixture["snapshot"]["objects"]), 1)
        self.assertEqual(
            fixture["durableEvent"]["body"]["objectId"],
            "phone-1:session1:cccccccccccccccccccccccccccccccc",
        )
        self.assertEqual(fixture["durableEvent"]["body"]["backendRevision"], 4)
        self.assertEqual(
            fixture["durableAlertEvent"]["body"]["alertId"],
            "alert-0001",
        )
        self.assertEqual(
            fixture["durableAlertEvent"]["body"]["visionEventId"],
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:10",
        )

    def test_documents_exist(self):
        for name in ("vision-v1.md", "backend-stream-v1.md", "robot-simulator-v1.md", "manual-control-v1.md"):
            self.assertGreater(len((ROOT / name).read_text()), 400)


if __name__ == "__main__":
    unittest.main()
