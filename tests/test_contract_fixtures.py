"""Shared contract fixtures parse and name the decisions peers asked to freeze."""
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "contracts"


def load(name):
    return json.loads((ROOT / name).read_text())


def demo_sector(x, y, width, height):
    if not (0 <= x < width and 0 <= y < height):
        return None
    column = min(4, int(x / width * 4) + 1)
    row_from_top = min(4, int(y / height * 4) + 1)
    return "ABCD"[4 - row_from_top] + str(column)


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

    def test_manual_release_response_is_unknown_until_confirmed(self):
        fixture = load("fixtures/manual-control.json")
        resp = fixture["releaseResponse"]
        self.assertEqual(resp["stage"], "UNKNOWN")
        self.assertIs(resp["stationaryConfirmed"], False)
        self.assertEqual(resp["stationarySamples"], 0)
        self.assertIs(resp["motion"], False)
        self.assertEqual(fixture["hardwareStopStateMachine"]["cleanReleaseTimeoutStage"], "UNKNOWN")
        text = (ROOT / "manual-control-v1.md").read_text()
        self.assertIn("STAYS", text)

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
        self.assertEqual(fixture["caps"]["maxIntentTtlMs"], 500)
        self.assertEqual(fixture["caps"]["maxTelemetryAgeMs"], 500)
        self.assertEqual(fixture["caps"]["stationarySamplesRequired"], 3)
        self.assertEqual(fixture["caps"]["stationarySpanMs"], 200)
        self.assertEqual(
            fixture["hardwareStopStateMachine"]["zeroRequestInitialStage"],
            "UNKNOWN",
        )
        self.assertEqual(
            fixture["hardwareStopStateMachine"]["unknownStageLeaseDriveHttpStatus"],
            503,
        )
        self.assertEqual(
            fixture["hardwareStopStateMachine"]["unknownStageLeaseDriveCode"],
            "TELEMETRY_STALE",
        )
        self.assertEqual(
            fixture["hardwareStopStateMachine"]["unknownStageRearmHttpStatus"],
            409,
        )
        self.assertEqual(
            fixture["hardwareStopStateMachine"]["unknownStageRearmCode"],
            "NOT_STOPPED",
        )
        self.assertEqual(
            fixture["hardwareStopStateMachine"]["faultLatchedRejectionCode"],
            "STOP_LOCKED",
        )
        self.assertTrue(
            fixture["hardwareStopStateMachine"]["distinctFromAutonomousMissionUnknown"]
        )
        self.assertTrue(
            fixture["hardwareStopStateMachine"]["watchdogSilenceLatchesStopped"]
        )
        self.assertEqual(
            fixture["hardwareStopStateMachine"]["statusStages"],
            ["IDLE", "MANUAL_IDLE", "MANUAL_DRIVING", "UNKNOWN", "STOPPED"],
        )
        self.assertEqual(
            fixture["hardwareStopStateMachine"]["faultConfirmedStage"],
            "STOPPED",
        )
        self.assertEqual(
            fixture["hardwareStopStateMachine"]["rearmTargetStage"],
            "IDLE",
        )
        self.assertFalse(
            fixture["hardwareStopStateMachine"]["rearmRestoresAuthorityOrMotion"]
        )
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
        self.assertIs(fixture["snapshot"]["objects"][0]["dispatchEligible"], False)
        self.assertIs(fixture["durableEvent"]["body"]["dispatchEligible"], False)
        self.assertEqual(
            fixture["durableAlertEvent"]["body"]["alertId"],
            "alert-0001",
        )
        self.assertEqual(
            fixture["durableAlertEvent"]["body"]["visionEventId"],
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:10",
        )

    def test_mission_queue_replay_is_current_state_without_a_robot_call(self):
        fixture = load("fixtures/mission-queue.json")
        self.assertFalse(fixture["robotCalled"])
        self.assertFalse(fixture["dispatchEligible"])
        self.assertEqual(fixture["created"]["http"], 202)
        self.assertEqual(fixture["created"]["mission"]["state"], "QUEUED")
        self.assertEqual(fixture["replay"]["http"], 200)
        self.assertTrue(fixture["replay"]["sameMissionId"])
        self.assertFalse(fixture["replay"]["secondSseEvent"])
        codes = {item["id"]: (item["http"], item["code"]) for item in fixture["errors"]}
        self.assertEqual(codes["missing_object"], (404, "OBJECT_NOT_FOUND"))
        self.assertEqual(codes["idempotency_conflict"], (409, "IDEMPOTENCY_CONFLICT"))
        self.assertEqual(codes["storage_unavailable"], (503, "STORAGE_UNAVAILABLE"))
        self.assertEqual(codes["expired_session"], (401, "UNAUTHORIZED"))
        text = (ROOT / "mission-queue-v1.md").read_text()
        self.assertIn("does not call the robot", text)

    def test_demo_grid_matches_the_half_metre_example(self):
        self.assertEqual(demo_sector(855, 423, 1920, 1080), "C2")
        self.assertEqual(demo_sector(0, 1079, 1920, 1080), "A1")
        self.assertEqual(demo_sector(1919, 0, 1920, 1080), "D4")
        self.assertIsNone(demo_sector(1920, 423, 1920, 1080))
        text = (ROOT / "demo-grid-v1.md").read_text()
        self.assertIn("calibrated` is false", text)
        self.assertIn("POST /api/observations", text)

    def test_documents_exist(self):
        for name in ("vision-v1.md", "backend-stream-v1.md", "robot-simulator-v1.md", "manual-control-v1.md", "mission-queue-v1.md", "demo-grid-v1.md"):
            self.assertGreater(len((ROOT / name).read_text()), 400)


if __name__ == "__main__":
    unittest.main()
