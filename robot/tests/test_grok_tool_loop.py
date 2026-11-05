"""Strict provider-response handling without model or hardware requests."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    import rover_tools
except ImportError:
    from robot import rover_tools
from robot import route_mission


def load_runner():
    path = Path(__file__).resolve().parents[1] / "grok_tool_loop.py"
    spec = importlib.util.spec_from_file_location("offline_grok_tool_loop", path)
    runner = importlib.util.module_from_spec(spec)
    # Never consult real operator .env files or run main during import.
    with patch.object(Path, "is_file", return_value=False), patch.object(
            sys, "argv", [str(path), "1", "test/model"]), patch.dict(
            sys.modules, {"rover_tools": rover_tools, "route_mission": route_mission}):
        spec.loader.exec_module(runner)
    runner.MODEL = "test/model"
    return runner


class Response:
    def __init__(self, body):
        self.body = body
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return json.dumps(self.body).encode()[:limit]


class Opener:
    def __init__(self, message=None, error=None):
        self.message, self.error, self.requests = message, error, []
    def open(self, request, timeout):
        self.requests.append(request)
        if self.error:
            raise self.error
        return Response({"id": "offline", "choices": [{"message": self.message}]})


def tool(arguments='{"reason":"operator verification"}'):
    return {"function": {"name": "stop_robot", "arguments": arguments}}


class GrokToolLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def test_one_structured_tool_uses_one_request(self):
        opener = Opener({"content": "explanation", "tool_calls": [tool()]})
        name, args, _, _ = self.runner.ask_grok(opener, "fake", b"jpeg", {})
        self.assertEqual(name, "stop_robot")
        self.assertEqual(args, {"reason": "operator verification"})
        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(opener.requests[0].full_url, 'https://api.x.ai/v1/chat/completions')

    def test_camera_reference_is_data_and_pickup_requires_alignment(self):
        opener = Opener({"tool_calls": [tool()]})
        with patch.object(self.runner, 'TARGET_REFERENCE', b'reference-jpeg'):
            self.runner.ask_grok(opener, 'fake', b'live-jpeg', {'pickupOnly': True})
        payload = json.loads(opener.requests[0].data)
        images = [part for part in payload['messages'][1]['content'] if part['type'] == 'image_url']
        self.assertEqual(len(images), 2)
        self.assertIn('isInCorridor AND isInGraspRange', payload['messages'][0]['content'])
        self.assertNotIn('distanceToJawsCm <= 9 cm', payload['messages'][0]['content'])

    def test_provider_failure_is_not_retried(self):
        opener = Opener(error=TimeoutError("unknown provider outcome"))
        with self.assertRaises(TimeoutError):
            self.runner.ask_grok(opener, "fake", b"jpeg", {})
        self.assertEqual(len(opener.requests), 1)

    def test_multiple_calls_reject_entire_response(self):
        opener = Opener({"tool_calls": [tool(), tool()]})
        with self.assertRaisesRegex(RuntimeError, "exactly_one"):
            self.runner.ask_grok(opener, "fake", b"jpeg", {})
        self.assertEqual(len(opener.requests), 1)

    def test_tool_looking_text_is_not_executable(self):
        opener = Opener({"content": '{"name":"drive_chassis","arguments":{"x_mps":.2}}'})
        with self.assertRaisesRegex(RuntimeError, "exactly_one"):
            self.runner.ask_grok(opener, "fake", b"jpeg", {})
        self.assertEqual(len(opener.requests), 1)

    def test_malformed_or_nonobject_arguments_do_not_retry(self):
        for arguments in ("{", "[]"):
            with self.subTest(arguments=arguments):
                opener = Opener({"tool_calls": [tool(arguments)]})
                with self.assertRaises((ValueError, RuntimeError)):
                    self.runner.ask_grok(opener, "fake", b"jpeg", {})
                self.assertEqual(len(opener.requests), 1)

    def test_pickup_loop_exits_after_grasp_without_transport_or_release(self):
        tools = Mock(allow_transport=True, tof_mm=None, latest_tracker=None)
        tools.frame_jpeg.return_value = b"jpeg"
        tools.status.return_value = {}
        tools.dispatch.return_value = {"gripperCloseCommanded": True,
                                       "gripperClamped": True, "armLifted": True,
                                       "seatedDrive": {"stoppedBy": None}}
        with tempfile.TemporaryDirectory() as directory, patch.object(
                self.runner, "LOG", Path(directory)), patch.object(
                self.runner, "BRAIN_DIR", Path(directory)), patch.object(
                self.runner, "update_brain_monitor"), patch.object(
                self.runner, "ask_grok", return_value=("grasp_can", {}, "id", "")) as ask:
            result = self.runner.run_grok_loop(tools, None, "fake", pickup_only=True)
        self.assertEqual(result["result"], "PICKUP_COMMANDED")
        self.assertEqual(result["grokCalls"], 1)
        self.assertEqual(ask.call_count, 1)
        tools.dispatch.assert_called_once_with("grasp_can", {})
        tools.reset_arm_and_gripper.assert_not_called()

    def test_failed_macro_never_reports_pickup_success(self):
        tools = Mock(allow_transport=True, tof_mm=None, latest_tracker=None)
        tools.frame_jpeg.return_value = b"jpeg"
        tools.status.return_value = {}
        for outcome in ({"armLifted": False}, rover_tools.RoverError("uncertain")):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                tools.dispatch.side_effect = outcome if isinstance(outcome, Exception) else None
                tools.dispatch.return_value = outcome
                with patch.object(self.runner, "LOG", Path(directory)), patch.object(
                        self.runner, "BRAIN_DIR", Path(directory)), patch.object(
                        self.runner, "update_brain_monitor"), patch.object(
                        self.runner, "ask_grok", return_value=("grasp_can", {}, "id", "")) as ask:
                    result = self.runner.run_grok_loop(tools, None, "fake", pickup_only=True)
                self.assertEqual(result["result"], "HARDWARE_ERROR")
                self.assertEqual(ask.call_count, 1)
