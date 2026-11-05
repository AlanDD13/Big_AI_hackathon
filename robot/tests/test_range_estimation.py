import sys
from pathlib import Path
import unittest
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from range_estimation import estimate_points


class RangeTests(unittest.TestCase):
    def points(self):
        random = np.random.default_rng(4)
        return random.uniform((300, 40), (350, 160), size=(40, 2)).astype(np.float32)

    def test_known_forward_baseline_recovers_depth_without_object_size(self):
        before = self.points()
        baseline, remaining_depth = 0.05, 0.50
        scale = 1 + baseline / remaining_depth
        after = (before - (320, 180)) * scale + (320, 180)
        result = estimate_points(before, after, baseline)
        self.assertAlmostEqual(result.optical_depth_m, remaining_depth, places=3)
        self.assertGreater(result.inliers, 30)

    def test_unmeasurable_parallax_does_not_fabricate_a_range(self):
        before = self.points()
        with self.assertRaisesRegex(ValueError, "parallax"):
            estimate_points(before, before + (2, 0), 0.05)

    def test_rotation_is_not_forward_motion_range(self):
        before = self.points()
        angle = np.radians(5)
        matrix = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
        after = before @ matrix.T * 1.1
        with self.assertRaisesRegex(ValueError, "rotation"):
            estimate_points(before, after, 0.05)

    def test_short_baseline_is_rejected(self):
        points = self.points()
        with self.assertRaisesRegex(ValueError, "baseline"):
            estimate_points(points, points * 1.1, 0.01)


if __name__ == "__main__":
    unittest.main()
