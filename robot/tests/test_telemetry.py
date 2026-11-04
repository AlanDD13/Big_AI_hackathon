import unittest

from robot.telemetry import YawRate


class YawRateTests(unittest.TestCase):
    def test_bursted_callbacks_do_not_amplify_tiny_angle_noise(self):
        estimator = YawRate()
        self.assertIsNone(estimator.update(10.0, 50))
        self.assertIsNone(estimator.update(10.1, 50.01))
        self.assertIsNone(estimator.update(10.100001, 50.02))
        self.assertAlmostEqual(estimator.update(10.2, 50.02), 0.1)

    def test_real_rotation_is_not_reported_stationary(self):
        estimator = YawRate()
        estimator.update(10, 50)
        estimator.update(10.1, 50.5)
        self.assertAlmostEqual(estimator.update(10.2, 51), 5)

    def test_wrap_at_180_degrees(self):
        estimator = YawRate()
        estimator.update(10, 179.8)
        self.assertAlmostEqual(estimator.update(10.2, -179.2), 5)

    def test_gap_or_invalid_input_requires_a_new_window(self):
        estimator = YawRate()
        estimator.update(10, 0)
        self.assertIsNone(estimator.update(11, 0))
        self.assertIsNone(estimator.update(11.1, float("nan")))
        self.assertIsNone(estimator.update(11.2, 0))

    def test_nonincreasing_stamp_cannot_add_evidence(self):
        estimator = YawRate()
        estimator.update(10, 0)
        estimator.update(10.2, 0)
        self.assertIsNone(estimator.update(10.2, 0))


if __name__ == "__main__":
    unittest.main()
