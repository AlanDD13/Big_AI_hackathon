import json
import unittest
from dataclasses import replace
from pathlib import Path

from backend.stationarity import (
    MetricSample,
    Stationarity,
    TrackContext,
    evaluate_stationarity,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "stationarity.json"


def metric_sample(data):
    point = data["pointM"]
    return MetricSample(
        context=TrackContext(
            camera_id=data["cameraId"],
            session_id=data["sessionId"],
            geometry_version=data["geometryVersion"],
            track_id=data["trackId"],
            map_id=data["mapId"],
            map_version=data["mapVersion"],
            calibration_id=data["calibrationId"],
        ),
        received_at=data["receivedAt"],
        point_m=tuple(point) if point is not None else None,
        position_error_m=data["positionErrorM"],
        is_fixed_camera=data["cameraKind"] == "FIXED",
        position_valid=data["positionValid"],
        calibration_valid=data["calibrationValid"],
    )


class StationarityTests(unittest.TestCase):
    def test_review_vectors(self):
        vectors = json.loads(FIXTURE_PATH.read_text(encoding="utf-8-sig"))
        defaults = vectors["defaults"]
        for case in vectors["cases"]:
            with self.subTest(case=case["name"]):
                sample_defaults = defaults["sample"] | case.get("sampleDefaults", {})
                offsets = case.get("offsets", defaults["offsets"])
                overrides = {item["index"]: item for item in case.get("overrides", [])}
                samples = [
                    metric_sample(
                        sample_defaults | {"receivedAt": offset} | overrides.get(index, {})
                    )
                    for index, offset in enumerate(offsets)
                ]
                if "append" in case:
                    samples.append(metric_sample(sample_defaults | case["append"]))
                result = evaluate_stationarity(samples, evaluated_at=case["evaluatedAt"])
                self.assertEqual(result.eligible, case["eligible"])

    def test_reports_partial_progress_and_resets_it_when_stale(self):
        sample = MetricSample(
            context=TrackContext("camera", "session", "geometry", "track", "map", "map-v1", "calibration"),
            received_at=1.0,
            point_m=(0.0, 0.0),
            position_error_m=0.01,
            is_fixed_camera=True,
            position_valid=True,
            calibration_valid=True,
        )
        samples = [sample, replace(sample, received_at=1.5)]
        self.assertEqual(
            evaluate_stationarity(samples, evaluated_at=1.5),
            Stationarity(sample_count=2, span_seconds=0.5),
        )
        self.assertEqual(evaluate_stationarity(samples, evaluated_at=2.6), Stationarity())


if __name__ == "__main__":
    unittest.main()
