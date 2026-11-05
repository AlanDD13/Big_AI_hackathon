"""Strict provider-response handling without model or hardware requests."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

try:
    import rover_tools
except ImportError:
    from robot import rover_tools


def load_runner():
    path = Path(__file__).resolve().parents[1] / "grok_tool_loop.py"
    spec = importlib.util.spec_from_file_location("offline_grok_tool_loop", path)
    runner = importlib.util.module_from_spec(spec)
    # Never consult real operator .env files or run main during import.
    with patch.object(Path, "is_file", return_value=False), patch.object(
            sys, "argv", [str(path), "1", "test/model"]), patch.dict(
            sys.modules, {"rover_tools": rover_tools}):
        spec.loader.exec_module(runner)
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
