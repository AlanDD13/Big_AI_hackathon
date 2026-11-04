import unittest

from backend.bridge_client import (
    interpret_bridge_response,
    pickup_authorization,
    pose_is_fresh,
    status_is_unknown,
)


class BridgeClientTests(unittest.TestCase):
    def test_stage_mapping_and_lost_command(self):
        picking = interpret_bridge_response(200, {"stage": "PICKING", "statusRevision": 2})
        self.assertEqual(picking.observation.state, "running")
        self.assertEqual(picking.observation.reason, "authorized_picking")
        self.assertTrue(picking.reuse_command)
        self.assertEqual(picking.status_revision, 2)

        held = interpret_bridge_response(409, {"stage": "HELD", "error": {"code": "HELD", "message": "held"}})
        self.assertEqual(held.observation.state, "holding")
        self.assertEqual(held.observation.reason, "HELD")

        already = interpret_bridge_response(
            409, {"stage": "PICKING", "error": {"code": "ALREADY_PICKING", "message": "moving"}}
        )
        self.assertEqual(already.observation.state, "running")
        self.assertEqual(already.observation.reason, "authorized_picking")

        stopped = interpret_bridge_response(200, {"stage": "STOPPED"})
        self.assertEqual(stopped.observation.state, "stopped")

        lost = interpret_bridge_response(
            404, {"error": {"code": "UNKNOWN_COMMAND", "message": "restarted"}}
        )
        self.assertEqual(lost.observation.state, "unknown")
        self.assertEqual(lost.observation.reason, "unknown_command")
        self.assertFalse(lost.reuse_command)

    def test_pickup_deadlines(self):
        body = pickup_authorization(
            command_id="cmd-1",
            mission_id="mission-1",
            object_id="phone-1:session1:track1",
            target_revision=3,
            confirmation_evidence_id="evidence-1",
            issued_at=100.0,
        )
        self.assertEqual(body["expiresAt"], 103.0)
        self.assertTrue(pose_is_fresh(pose_received_at=99.5, now=100.0))
        self.assertFalse(pose_is_fresh(pose_received_at=99.4, now=100.0))
        self.assertFalse(pose_is_fresh(pose_received_at=101.0, now=100.0))
        self.assertFalse(status_is_unknown(last_status_at=97.1, now=100.0))
        self.assertTrue(status_is_unknown(last_status_at=97.0, now=100.0))
