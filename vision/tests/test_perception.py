import math
import unittest
from io import BytesIO
from unittest.mock import patch
from types import SimpleNamespace
from PIL import Image

from vision.perception import Detection, Tracker, YoloDetector


def det(label='bottle', x=0, y=0):
    return Detection(label, .9, (x, y, x + 20, y + 20))


def observe(tracker, t, detections, **kw):
    return tracker.observe('cam', kw.get('session', 's'), kw.get('geometry', 'g'), int(t * 1000), t, detections, kw.get('source', 'FIXED'))


class TrackerTests(unittest.TestCase):
    def test_stationarity_requires_ten_observations_and_full_five_seconds(self):
        tracker = Tracker()
        for i in range(10):
            result = observe(tracker, i / 2, [det()])[0]
            self.assertNotEqual(result.motion_state, 'STATIONARY')
        self.assertEqual(observe(tracker, 5, [det()])[0].motion_state, 'STATIONARY')

    def test_people_and_animals_and_overlapping_objects_are_excluded(self):
        tracker = Tracker()
        for i in range(11):
            updates = observe(tracker, i / 2, [det('person'), det('cat', 50), det('bottle', 5)])
        self.assertTrue(all(u.motion_state == 'EXCLUDED' for u in updates))

    def test_movement_and_slow_drift_reset_stationarity(self):
        tracker = Tracker()
        for i in range(11):
            update = observe(tracker, i / 2, [det(x=i * .7)])[0]
        self.assertNotEqual(update.motion_state, 'STATIONARY')
        self.assertLess(update.stationary_for_s, 5)

    def test_missing_and_long_gap_start_new_identity(self):
        tracker = Tracker()
        first = observe(tracker, 0, [det()])[0]
        observe(tracker, .5, [])
        self.assertNotEqual(first.track_id, observe(tracker, 1, [det()])[0].track_id)
        later = observe(tracker, 3, [det()])[0]
        self.assertTrue(later.new)
        self.assertNotEqual(first.track_id, later.track_id)

    def test_ambiguous_crossing_restarts_identities(self):
        tracker = Tracker()
        old = observe(tracker, 0, [det(x=0), det(x=10)])
        new = observe(tracker, .5, [det(x=4), det(x=6)])
        self.assertTrue(all(u.new for u in new))
        self.assertTrue(set(u.track_id for u in old).isdisjoint(u.track_id for u in new))

    def test_geometry_and_session_change_reset(self):
        tracker = Tracker()
        initial = observe(tracker, 0, [det()])[0]
        geometry = observe(tracker, .5, [det()], geometry='g2')[0]
        session = observe(tracker, 1, [det()], geometry='g2', session='s2')[0]
        self.assertEqual(len({initial.track_id, geometry.track_id, session.track_id}), 3)

    def test_rover_never_uses_pixel_stationarity(self):
        tracker = Tracker()
        for i in range(11):
            update = observe(tracker, i / 2, [det()], source='ROVER')[0]
        self.assertEqual(update.motion_state, 'UNKNOWN')

    def test_invalid_detections_are_rejected(self):
        tracker = Tracker()
        invalid = [Detection('x', math.nan, (0, 0, 1, 1)), Detection('x', 1.1, (0, 0, 1, 1)), Detection('x', .5, (0, 0, math.inf, 1)), Detection('x', .5, (1, 0, 0, 1))]
        self.assertEqual(observe(tracker, 0, invalid), [])

    def test_movement_revokes_stationary_state(self):
        tracker = Tracker()
        for i in range(11):
            stationary = observe(tracker, i / 2, [det()])[0]
        self.assertEqual(stationary.motion_state, 'STATIONARY')
        moving = observe(tracker, 5.5, [det(x=5)])[0]
        self.assertEqual(moving.motion_state, 'MOVING')
        self.assertEqual(moving.stationary_for_s, 0)

    def test_replayed_frame_and_reversed_timestamp_revoke_tracks(self):
        tracker = Tracker()
        for i in range(11):
            result = tracker.observe('cam', 's', 'g', i, i / 2, [det()])
        self.assertEqual(result[0].motion_state, 'STATIONARY')
        self.assertEqual(tracker.observe('cam', 's', 'g', 2, 5.5, [det()]), [])
        fresh = tracker.observe('cam', 's', 'g', 11, 6, [det()])[0]
        self.assertTrue(fresh.new)
        self.assertEqual(tracker.observe('cam', 's', 'g', 12, 4, [det()]), [])
        self.assertEqual(tracker.observe('cam', 's', 'g', 13, 5, [det()]), [])
        self.assertTrue(tracker.observe('cam', 's', 'g', 14, 6.5, [det()])[0].new)

    def test_negative_boxes_and_oversized_batch_are_rejected(self):
        tracker = Tracker(max_detections=2)
        self.assertEqual(observe(tracker, 0, [det(x=-1)]), [])
        observe(tracker, .5, [det()])
        self.assertEqual(observe(tracker, 1, [det()] * 3), [])
        self.assertTrue(observe(tracker, 1.5, [det()])[0].new)

    def test_camera_capacity_evicts_old_state(self):
        tracker = Tracker(max_cameras=2)
        old = tracker.observe('old', 's', 'g', 0, 0, [det()])[0]
        for i in range(2):
            tracker.observe(str(i), 's', 'g', 0, 0, [det()])
        current = tracker.observe('old', 's', 'g', 1, .5, [det()])[0]
        self.assertTrue(current.new)
        self.assertNotEqual(old.track_id, current.track_id)


class DetectorTests(unittest.TestCase):
    def test_detector_reuses_model_and_returns_original_pixel_coordinates(self):
        class Array:
            def tolist(self):
                return [10, 20, 110, 70]
        box = SimpleNamespace(xyxy=[Array()], cls=[0], conf=[.8])
        result = SimpleNamespace(boxes=[box], names={0: 'bottle'})
        calls = []
        class FakeModel:
            def __init__(self, model):
                calls.append(('init', model))
            def predict(self, **kwargs):
                calls.append(('predict', kwargs['source'].size))
                return [result]
        data = BytesIO()
        Image.new('RGB', (200, 100)).save(data, 'JPEG')
        with patch.dict('sys.modules', {'ultralytics': SimpleNamespace(YOLOE=FakeModel)}):
            detector = YoloDetector(device='cpu')
            self.assertEqual(calls, [])
            first = detector.detect(data.getvalue())
            self.assertEqual(detector.detect(data.getvalue()), first)
        self.assertEqual(first[0].bbox, (10, 20, 110, 70))
        self.assertEqual(first[0].contact_point, (60, 70))
        tiled = [('predict', (200, 100)), ('predict', (120, 60)), ('predict', (120, 60)),
                 ('predict', (120, 60)), ('predict', (120, 60))]
        self.assertEqual(calls, [('init', 'yoloe-26s-seg-pf.pt'), *tiled, *tiled])


if __name__ == '__main__':
    unittest.main()
