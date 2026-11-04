import os
import unittest
from uuid import uuid4

from backend.dispatch import BridgeReply, advance_dispatch
from backend.postgres_dispatch import PostgresDispatchStore


DATABASE_URL = os.environ.get("BACKEND_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "requires a migrated disposable PostgreSQL database")
class PostgresDispatchTests(unittest.TestCase):
    def test_lost_ack_reconciles_after_restart_without_resending(self):
        from psycopg import connect

        def database():
            return connect(DATABASE_URL)

        object_id = uuid4()
        rover_id = str(uuid4())
        with database() as connection:
            connection.execute(
                """INSERT INTO rover_backend.objects
                   (id, camera_id, session_id, track_id, state)
                   VALUES (%s, 'test-camera', 'test-session', %s, '{}')""",
                (object_id, str(object_id)),
            )
            outcome, mission_id, _ = connection.execute(
                "SELECT * FROM rover_backend.enqueue_mission(%s, %s, 'test', %s, '{}')",
                (object_id, rover_id, str(object_id)),
            ).fetchone()
        self.assertEqual(outcome, "created")

        preflight_calls = []

        def preflight(connection, target_mission):
            preflight_calls.append(target_mission)
            self.assertEqual(
                connection.execute(
                    "SELECT id FROM rover_backend.objects WHERE id = %s FOR UPDATE",
                    (object_id,),
                ).fetchone(),
                (object_id,),
            )
            return {"planId": "fixture-plan"}

        def stored_command():
            with database() as connection:
                return connection.execute(
                    """SELECT state, command_id
                       FROM rover_backend.missions WHERE id = %s""",
                    (mission_id,),
                ).fetchone()

        class FakeBridge:
            def __init__(self):
                self.sends = []
                self.queries = []

            def send(self, target_mission, command_id, plan):
                self.sends.append((target_mission, command_id, plan, stored_command()))
                raise TimeoutError

            def query(self, target_mission, command_id):
                self.queries.append((target_mission, command_id))
                return BridgeReply(target_mission, command_id, "failed")

        bridge = FakeBridge()
        first = advance_dispatch(
            mission_id, store=PostgresDispatchStore(database, preflight), bridge=bridge
        )
        command_id = first.claim.command_id
        self.assertIsNotNone(command_id)
        self.assertTrue(first.outcome_recorded)
        self.assertEqual(stored_command(), ("unknown", command_id))

        second = advance_dispatch(
            mission_id, store=PostgresDispatchStore(database, preflight), bridge=bridge
        )
        self.assertTrue(second.outcome_recorded)
        self.assertEqual(stored_command(), ("failed", command_id))
        self.assertEqual(
            bridge.sends,
            [(mission_id, command_id, {"planId": "fixture-plan"}, ("dispatching", command_id))],
        )
        self.assertEqual(bridge.queries, [(mission_id, command_id)])
        self.assertEqual(preflight_calls, [mission_id])
