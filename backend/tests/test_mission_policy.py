from dataclasses import replace
import unittest

from backend.mission_policy import Classification, Rejection, collection_rejection
from backend.stationarity import Stationarity


class MissionPolicyTests(unittest.TestCase):
    stationary = Stationarity(sample_count=11, span_seconds=5.0)
    trash = Classification("COMPLETED", "TRASH", "INANIMATE", "NO", "HIGH")

    def test_evidence_and_pickup_profile_are_required(self):
        cases = [
            (Stationarity(), self.trash, "demo", Rejection.STATIONARITY_REQUIRED),
            (self.stationary, None, "demo", Rejection.CLASSIFICATION_REQUIRED),
            (
                self.stationary,
                replace(self.trash, status="UNAVAILABLE"),
                "demo",
                Rejection.CLASSIFICATION_REQUIRED,
            ),
            (self.stationary, self.trash, None, Rejection.PICKUP_PROFILE_REQUIRED),
            (self.stationary, self.trash, "demo", None),
        ]
        for stationarity, classification, profile, expected in cases:
            with self.subTest(classification=classification, profile=profile):
                self.assertIs(
                    collection_rejection(
                        stationarity, classification, pickup_profile_id=profile
                    ),
                    expected,
                )

    def test_stationarity_cannot_override_classification_exclusions(self):
        for changes in [
            {"subject_kind": "PERSON"},
            {"subject_kind": "ANIMAL"},
            {"subject_kind": "UNCERTAIN"},
            {"carrying": "YES"},
            {"carrying": "UNCERTAIN"},
            {"category": "OTHER"},
            {"confidence": "LOW"},
        ]:
            with self.subTest(changes=changes):
                self.assertIs(
                    collection_rejection(
                        self.stationary,
                        replace(self.trash, **changes),
                        pickup_profile_id="demo",
                    ),
                    Rejection.CLASSIFICATION_EXCLUDED,
                )
