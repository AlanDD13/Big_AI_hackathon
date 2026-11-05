"""Manual admission consults durable active/unknown missions, not QUEUED."""
import os
import unittest
from uuid import uuid4

from psycopg import connect

from backend.manual_proxy import PostgresManualGate


DATABASE_URL = os.environ.get("BACKEND_TEST_DATABASE_URL")


@unittest.skipUnless(DATABASE_URL, "requires a migrated disposable PostgreSQL database")
class PostgresManualGateTests(unittest.TestCase):
    def test_all_active_states_block_only_the_assigned_rover(self):
        object_id, mission_id, command_id = uuid4(), uuid4(), uuid4()
        rover_id = "manual-fixture-" + uuid4().hex
        gate = PostgresManualGate(lambda: connect(DATABASE_URL))
        try:
            with connect(DATABASE_URL) as connection:
                connection.execute(
                    """INSERT INTO rover_backend.objects(id,camera_id,session_id,track_id,state)
                       VALUES (%s,%s,'fixture-session','fixture-track','{}')""", (object_id, rover_id),
                )
                connection.execute(
                    """INSERT INTO rover_backend.missions(id,object_id,rover_id,request_scope,
                       idempotency_key,request_data,command_id) VALUES(%s,%s,%s,%s,'manual-gate','{}',%s)""",
                    (mission_id, object_id, rover_id, rover_id, command_id),
                )
            for state in ["queued", "dispatching", "running", "holding", "unknown",
                          "succeeded", "failed", "stopped", "cancelled"]:
                with self.subTest(state=state):
                    with connect(DATABASE_URL) as connection:
                        connection.execute("UPDATE rover_backend.missions SET state=%s WHERE id=%s", (state, mission_id))
                    self.assertEqual(gate(rover_id), state in {"dispatching", "running", "holding", "unknown"})
                    self.assertFalse(gate(rover_id + "-other"))
        finally:
            with connect(DATABASE_URL) as connection:
                connection.execute("DELETE FROM rover_backend.missions WHERE id=%s", (mission_id,))
                connection.execute("DELETE FROM rover_backend.objects WHERE id=%s", (object_id,))
