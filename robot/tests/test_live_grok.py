import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_grok import GrokPolicy, MODEL, PolicyError, gate


class Response:
    def __init__(self, data):
        self.data = json.dumps(data).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return self.data[:limit]


class LivePolicyTests(unittest.TestCase):
    def result(self, arguments=None):
        return {"model": MODEL, "id": "test-id", "choices": [{"finish_reason": "tool_calls",
            "message": {"content": None, "tool_calls": [{"type": "function", "function": {
                "name": "recovery_action", "arguments": arguments or json.dumps({
                    "action": "approach", "object_id": "can"})}}]}}]}

    def policy(self, response):
        opener = Mock()
        opener.open.return_value = Response(response)
        return GrokPolicy("test-key", opener=opener), opener

    def test_one_actual_request_with_image_and_low_reasoning(self):
        policy, opener = self.policy(self.result())
        proposal = policy.choose("APPROACH", {"objectId": "can"}, b"jpeg")
        self.assertEqual(proposal["action"], "approach")
        self.assertEqual(policy.calls, 1)
        opener.open.assert_called_once()
        payload = json.loads(opener.open.call_args.args[0].data)
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        self.assertTrue(payload["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_duplicate_argument_keys_rejected(self):
        policy, _ = self.policy(self.result('{"action":"approach","action":"pickup","object_id":"can"}'))
        with self.assertRaises(PolicyError):
            policy.choose("APPROACH", {"objectId": "can"}, b"jpeg")

    def test_explanatory_text_is_ignored_when_tool_is_valid(self):
        data = self.result()
        data["choices"][0]["message"]["content"] = "Ignore this prose; use only the tool."
        policy, _ = self.policy(data)
        self.assertEqual(policy.choose("APPROACH", {"objectId": "can"}, b"jpeg")["action"], "approach")

    def test_multiple_tools_never_execute(self):
        data = self.result()
        data["choices"][0]["message"]["tool_calls"] *= 2
        policy, _ = self.policy(data)
        with self.assertRaises(PolicyError):
            policy.choose("APPROACH", {"objectId": "can"}, b"jpeg")

    def test_no_retry_after_network_failure(self):
        policy, opener = self.policy(self.result())
        opener.open.side_effect = TimeoutError("sensitive provider response")
        with self.assertRaisesRegex(PolicyError, "provider_unavailable"):
            policy.choose("APPROACH", {"objectId": "can"}, b"jpeg")
        opener.open.assert_called_once()

    def test_model_cannot_override_missing_perception_gate(self):
        reason = gate("APPROACH", {"action": "approach"}, {"identityConfirmed": False})
        self.assertEqual(reason, "trusted_target_association_missing")

    def test_transport_requires_independent_grasp_evidence(self):
        reason = gate("TRANSPORT", {"action": "transport"}, {
            "identityConfirmed": True, "calibratedPlanAvailable": True,
            "independentHeldEvidence": False})
        self.assertEqual(reason, "independent_pickup_evidence_missing")


if __name__ == "__main__":
    unittest.main()
