import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from motion_control import heading_correction, project_initial_body


class MotionControlTests(unittest.TestCase):
    def test_rotated_forward_motion_is_not_strafe(self):
        forward, sideways = project_initial_body(0, 0.05, 90)
        self.assertAlmostEqual(forward, 0.05)
        self.assertAlmostEqual(sideways, 0)

    def test_actual_recorded_run_projects_to_small_lateral_error(self):
        forward, sideways = project_initial_body(0.06174, -0.01215, -9.72)
        self.assertAlmostEqual(forward, 0.062905, places=5)
        self.assertLess(abs(sideways), 0.002)

    def test_correction_opposes_measured_heading_error(self):
        self.assertGreater(heading_correction(-10, -11), 0)
        self.assertLess(heading_correction(-10, -9), 0)
        self.assertEqual(heading_correction(-10, -10.2), 0)

    def test_wrap_caps_and_abort(self):
        self.assertEqual(heading_correction(179, -179), -3)
        with self.assertRaisesRegex(ValueError, "exceeded"):
            heading_correction(0, 6)
        with self.assertRaises(ValueError):
            heading_correction(float("nan"), 0)


if __name__ == "__main__":
    unittest.main()
