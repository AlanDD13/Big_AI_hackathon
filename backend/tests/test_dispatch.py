import unittest
from uuid import UUID

from backend.dispatch import (
    BridgeReply,
    DispatchClaim,
    DispatchObservation,
    advance_dispatch,
)


MISSION_ID = UUID(int=1)
COMMAND_ID = UUID(int=2)


class FakeStore:
    def __init__(self):
        self.state = "queued"
        self.command_id = None
        self.preflighted_plan = object()
        self.rover_busy = False
        self.records = []

    def claim(self, mission_id):
        if self.command_id is not None:
            return DispatchClaim("already_claimed", mission_id, self.state, self.command_id)
        if self.rover_busy:
            return DispatchClaim("rover_busy", mission_id, self.state, None)
        self.state = "dispatching"
        self.command_id = COMMAND_ID
        return DispatchClaim(
            "claimed", mission_id, self.state, self.command_id, self.preflighted_plan
        )

    def record_outcome(self, claim, observation):
        if claim.mission_state != self.state or claim.command_id != self.command_id:
            return False
        self.records.append((claim.mission_id, claim.command_id, observation))
        self.state = observation.state
        return True


class FakeBridge:
    def __init__(self):
        self.sends = []
        self.queries = []
        self.on_send = lambda: BridgeReply(MISSION_ID, COMMAND_ID, "running")
        self.query_reply = None

    def send(self, mission_id, command_id, plan):
        self.sends.append((mission_id, command_id, plan))
        return self.on_send()

    def query(self, mission_id, command_id):
        self.queries.append((mission_id, command_id))
        return self.query_reply


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.bridge = FakeBridge()

    def advance(self):
        return advance_dispatch(MISSION_ID, store=self.store, bridge=self.bridge)

    def test_first_claim_sends_after_persisting_command_identity(self):
        def accepted():
            self.assertEqual(self.store.state, "dispatching")
            self.assertEqual(self.store.command_id, COMMAND_ID)
            return BridgeReply(MISSION_ID, COMMAND_ID, "running")

        self.bridge.on_send = accepted
        result = self.advance()
        self.assertTrue(result.outcome_recorded)
        self.assertEqual(
            self.bridge.sends, [(MISSION_ID, COMMAND_ID, self.store.preflighted_plan)]
        )
        self.assertEqual(self.bridge.queries, [])
        self.assertEqual(self.store.state, "running")

    def test_lost_send_ack_is_unknown_and_retry_only_queries_original_command(self):
        def lost_ack():
            raise TimeoutError

        self.bridge.on_send = lost_ack
        result = self.advance()
        self.assertEqual(result.observation, DispatchObservation("unknown", "bridge_timeout"))
        self.assertEqual(self.store.state, "unknown")
        self.assertEqual(self.store.command_id, COMMAND_ID)

        missing = self.advance()
        self.assertEqual(
            missing.observation, DispatchObservation("unknown", "bridge_status_unavailable")
        )
        self.bridge.query_reply = BridgeReply(MISSION_ID, COMMAND_ID, "failed")
        failed = self.advance()
        self.assertTrue(failed.outcome_recorded)
        self.assertEqual(self.store.state, "failed")

        terminal = self.advance()
        self.assertEqual(terminal.claim.mission_state, "failed")
        self.assertIsNone(terminal.observation)
        self.assertEqual(
            self.bridge.sends, [(MISSION_ID, COMMAND_ID, self.store.preflighted_plan)]
        )
        self.assertEqual(self.bridge.queries, [(MISSION_ID, COMMAND_ID)] * 2)

    def test_mismatched_reply_does_not_release_original_command(self):
        for reply in (
            BridgeReply(UUID(int=9), COMMAND_ID, "succeeded"),
            BridgeReply(MISSION_ID, UUID(int=9), "succeeded"),
        ):
            with self.subTest(reply=reply):
                store = FakeStore()
                self.bridge.on_send = lambda: reply
                result = advance_dispatch(MISSION_ID, store=store, bridge=self.bridge)
                self.assertEqual(
                    result.observation, DispatchObservation("unknown", "bridge_identity_mismatch")
                )
                self.assertEqual(
                    store.records, [(MISSION_ID, COMMAND_ID, result.observation)]
                )

    def test_busy_rover_does_not_send_or_query(self):
        self.store.rover_busy = True
        result = self.advance()
        self.assertEqual(result.claim.outcome, "rover_busy")
        self.assertEqual(self.bridge.sends, [])
        self.assertEqual(self.bridge.queries, [])
        self.assertEqual(self.store.records, [])

    def test_first_claim_without_preflighted_plan_cannot_send(self):
        self.store.preflighted_plan = None
        with self.assertRaises(ValueError):
            self.advance()
        self.assertEqual(self.bridge.sends, [])

    def test_concurrent_outcome_is_not_overwritten_by_late_timeout(self):
        def lose_ack_after_success_event():
            self.store.state = "succeeded"
            raise TimeoutError

        self.bridge.on_send = lose_ack_after_success_event
        result = self.advance()
        self.assertFalse(result.outcome_recorded)
        self.assertEqual(self.store.state, "succeeded")
        self.assertEqual(self.store.records, [])


if __name__ == "__main__":
    unittest.main()
