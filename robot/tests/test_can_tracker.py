import sys
from pathlib import Path
import unittest
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_tracker import CanTracker


def jpeg(image):
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


class CanTrackerTests(unittest.TestCase):
    def setUp(self):
        random = np.random.default_rng(7)
        self.texture = random.integers(0, 255, (60, 30, 3), dtype=np.uint8)
        self.image = np.full((200, 300, 3), 128, np.uint8)
        self.image[60:120, 100:130] = self.texture
        self.tracker = CanTracker(jpeg(self.image), (100, 60, 30, 60))

    def test_actual_appearance_moves_with_the_object(self):
        moved = np.full_like(self.image, 128)
        moved[65:125, 105:135] = self.texture
        result = self.tracker.locate(jpeg(moved))
        self.assertEqual(result.box[:2], (105, 65))
        self.assertGreater(result.score, 0.8)

    def test_ambiguous_duplicate_target_stops_association(self):
        image = self.image.copy()
        image[60:120, 200:230] = self.texture
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.tracker.locate(jpeg(image))

    def test_missing_target_is_not_a_new_identity(self):
        with self.assertRaises(ValueError):
            self.tracker.locate(jpeg(np.full_like(self.image, 128)))

    def test_camera_geometry_change_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "geometry"):
            self.tracker.locate(jpeg(np.full((100, 150, 3), 128, np.uint8)))


if __name__ == "__main__":
    unittest.main()
