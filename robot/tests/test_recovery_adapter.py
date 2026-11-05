from dataclasses import replace
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from recovery_adapter import ChassisPlan, Journal, PhysicalRecoveryAdapter


class PhysicalRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "reservation.sqlite"
        self.clock = 100.0
        self.ctrl = Mock()
        self.ctrl.status.return_value = dict(stage="IDLE", armed=True, telemetryFresh=True,
            heartbeatFresh=True, watchdogAlive=True, watchdogError=None,
            releasePending=False, stopConfirmed=False)
        self.bridge = Mock()
        self.bridge.manual_lease.return_value = (200, {"leaseId": "lease-1", "generation": 1})
        self.bridge.manual_drive.return_value = (200, {})
        self.authority = Mock()
        self.authority.reserve.return_value = True
        self.authority.allows.return_value = True
        self.obs = NS(camera_id="rover", session_id="session", frame_id=2,
            captured_at=100.0, telemetry_at=100.0, identity_confirmed=True,
            identity_evidence_id="identity-2", object_id="rover:session:1",
            target_revision=1, living_obstacle=False, stationary=True, safety="READY",
            near_target=True)
        self.command = NS(command_id="cmd-1", mission_id="mission-1", action="approach",
            object_id=self.obs.object_id, target_revision=1, frame_id=1,
            expires_at=100.3, max_duration_ms=300)
        self.plan = ChassisPlan("cmd-1", self.obs.object_id, 1, 2, "identity-2",
                               True, 0.05, 0, 0, 0.1)
        self.planner = Mock(side_effect=lambda command, obs: self.plan)
        self.adapter = PhysicalRecoveryAdapter(NS(controller=self.ctrl, bridge=self.bridge),
            authority=self.authority, observations=lambda binding: self.obs,
            planner=self.planner, journal_path=self.path,
            now=lambda: self.clock, monotonic=lambda: self.clock, sleep=self.advance)
        self.addCleanup(self.adapter.close)
        self.assertTrue(self.adapter.begin("mission-1"))

    def advance(self, seconds):
        self.clock += seconds

    def test_valid_bound_plan_uses_bridge_once_and_never_replays(self):
        self.assertEqual(self.adapter.mode, "physical")
        self.assertEqual(self.adapter.execute(self.command).outcome, "ACKNOWLEDGED")
        self.assertEqual(self.adapter.execute(self.command).outcome, "UNKNOWN")
        self.bridge.manual_drive.assert_called_once()
        self.bridge.manual_release.assert_called_once()
        self.ctrl.rearm.assert_not_called()

    def test_manipulation_and_transport_are_never_dispatched(self):
        for action in ("pickup", "place", "transport"):
            self.command.command_id = action
            self.command.action = action
            self.assertEqual(self.adapter.execute(self.command).outcome, "REJECTED")
        self.bridge.manual_drive.assert_not_called()

    def test_uncalibrated_plan_rejected_before_output(self):
        self.plan = replace(self.plan, calibrated=False)
        self.assertEqual(self.adapter.execute(self.command).outcome, "REJECTED")
        self.bridge.manual_drive.assert_not_called()

    def test_stale_evidence_rejected_before_output(self):
        self.obs.captured_at = 99
        self.assertEqual(self.adapter.execute(self.command).outcome, "REJECTED")
        self.planner.assert_not_called()

    def test_safety_not_ready_never_rearms(self):
        self.ctrl.status.return_value["stage"] = "UNKNOWN"
        self.assertEqual(self.adapter.execute(self.command).outcome, "REJECTED")
        self.bridge.manual_lease.assert_not_called()
        self.ctrl.rearm.assert_not_called()

    def test_lost_drive_ack_remains_unknown_after_confirmed_stop(self):
        self.bridge.manual_drive.side_effect = TimeoutError()
        self.assertEqual(self.adapter.execute(self.command).outcome, "UNKNOWN")
        self.ctrl.status.return_value["stopConfirmed"] = True
        self.assertEqual(self.adapter.stop("mission-1"), "STOPPED")
        self.assertEqual(self.adapter.execute(self.command).outcome, "UNKNOWN")
        self.bridge.manual_drive.assert_called_once()

    def test_other_manual_owner_blocks_dispatch(self):
        self.bridge.manual_lease.return_value = (409, {})
        self.assertEqual(self.adapter.execute(self.command).outcome, "REJECTED")
        self.bridge.manual_drive.assert_not_called()

    def test_velocity_limit_and_frame_binding(self):
        self.plan = replace(self.plan, x_mps=0.051)
        self.assertEqual(self.adapter.execute(self.command).outcome, "REJECTED")
        self.bridge.manual_drive.assert_not_called()

    def test_durable_journal_retains_reservation_after_restart(self):
        journal = Journal(self.path)
        try:
            self.assertFalse(journal.reserve("another-mission"))
            self.assertTrue(journal.attempt("interrupted-command"))
        finally:
            journal.close()
        journal = Journal(self.path)
        try:
            self.assertFalse(journal.attempt("interrupted-command"))
        finally:
            journal.close()

    def test_observation_rejects_replayed_frame(self):
        mission = NS(mission_id="mission-1", target=NS(object_id=self.obs.object_id, revision=1))
        self.adapter.observe(mission)
        with self.assertRaisesRegex(ValueError, "replayed"):
            self.adapter.observe(mission)


if __name__ == "__main__":
    unittest.main()
