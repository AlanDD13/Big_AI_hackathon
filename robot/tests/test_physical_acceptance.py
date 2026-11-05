import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from physical_acceptance import RecordingOutput, main, require_response, run


class AcceptanceTests(unittest.TestCase):
    def test_competing_listener_prevents_sdk_connection(self):
        with patch("physical_acceptance.socket.socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value.connect_ex.return_value = 0
            with patch("physical_acceptance.rover_session") as session:
                with self.assertRaisesRegex(RuntimeError, "occupied"):
                    run("3JKCK7W0030DJS")
                session.assert_not_called()

    def test_operator_decline_never_opens_session(self):
        with patch("sys.argv", ["acceptance", "--sn", "3JKCK7W0030DJS", "--allow-motion"]), \
             patch("physical_acceptance.check_no_camera_server"), \
             patch("builtins.input", return_value="no"), \
             patch("physical_acceptance.rover_session") as session:
            with self.assertRaises(SystemExit):
                main()
            session.assert_not_called()

    def test_recording_output_rejects_other_axes_and_speeds(self):
        writes = []
        rover = SimpleNamespace(chassis=SimpleNamespace(drive_speed=lambda **kw: writes.append(kw)))
        output = RecordingOutput(rover)
        for velocity in ((0.051, 0, 0), (-0.05, 0, 0), (0, 0.01, 0), (0, 0, 1)):
            with self.assertRaises(ValueError):
                output(*velocity)
        self.assertEqual(writes, [])
        output(0.05, 0, 0)
        output(0, 0, 0)
        self.assertEqual(len(output.events), 2)
        self.assertEqual(writes[0]["timeout"], 1)
        self.assertNotIn("timeout", writes[1])

    def test_failed_bridge_response_aborts_phase(self):
        with self.assertRaises(RuntimeError):
            require_response((503, {"error": {"code": "TELEMETRY_STALE"}}), 200)


if __name__ == "__main__":
    unittest.main()
