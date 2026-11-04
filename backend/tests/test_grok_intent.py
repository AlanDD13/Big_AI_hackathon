import unittest

from backend.grok_intent import (
    ExplainAlert,
    InspectObjects,
    InvalidIntent,
    ProposeCollection,
    decode_intent,
)


class GrokIntentTests(unittest.TestCase):
    def test_supported_actions_decode_to_typed_values(self):
        self.assertEqual(decode_intent("inspect_objects", {}), InspectObjects())
        self.assertEqual(
            decode_intent("explain_alert", {"alertId": "alert-1"}),
            ExplainAlert("alert-1"),
        )
        self.assertEqual(
            decode_intent(
                "propose_collect", {"objectId": "object-1", "destinationZoneId": "zone-1"}
            ),
            ProposeCollection("object-1", "zone-1"),
        )

    def test_unsupported_actions_and_malformed_arguments_are_rejected(self):
        cases = [
            ("motor_command", {"speed": 1}),
            ("inspect_objects", []),
            ("inspect_objects", {"objectId": "object-1"}),
            ("explain_alert", {"alertId": " "}),
            ("propose_collect", {"objectId": "object-1"}),
            ("propose_collect", {"objectId": 7, "destinationZoneId": "zone-1"}),
            (
                "propose_collect",
                {"objectId": "object-1", "destinationZoneId": "zone-1", "force": True},
            ),
        ]
        for action, arguments in cases:
            with self.subTest(action=action, arguments=arguments):
                with self.assertRaises(InvalidIntent):
                    decode_intent(action, arguments)
