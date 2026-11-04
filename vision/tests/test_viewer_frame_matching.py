import numpy as np
from vision.live_view import frame_reference, fit_preview


def test_newer_camera_poll_does_not_hide_boxes_from_the_object_snapshot():
    camera = {'stream': {'session_id': 's', 'latest_frame_id': 102},
              'processed': {'sessionId': 's', 'frameId': 101, 'geometryVersion': 'g'}}
    objects = [{'status': 'OBSERVING', 'sessionId': 's', 'frameId': 100,
                'geometryVersion': 'g', 'lastSeenAt': 1000}]
    assert frame_reference(camera, objects) == ('s', 100, 'g')


def test_zero_detections_still_show_processed_frame_without_old_epoch_boxes():
    camera = {'stream': {'session_id': 'new', 'latest_frame_id': 2},
              'processed': {'sessionId': 'new', 'frameId': 1, 'geometryVersion': 'g2'}}
    old = [{'status': 'OBSERVING', 'sessionId': 'old', 'frameId': 100,
            'geometryVersion': 'g1', 'lastSeenAt': 1000}]
    assert frame_reference(camera, old) == ('new', 1, 'g2')
    assert frame_reference(camera, []) == ('new', 1, 'g2')


def test_pc_preview_preserves_the_entire_ipad_frame_and_native_upload_dimensions():
    frame = np.zeros((2760, 3840, 3), dtype=np.uint8)
    frame[0, 0] = [255, 255, 255]
    frame[-1, -1] = [255, 255, 255]
    display = fit_preview(frame)
    assert display.shape[1] <= 1100 and display.shape[0] <= 790
    assert abs(display.shape[1] / display.shape[0] - 3840 / 2760) < .002
    assert frame.shape[:2] == (2760, 3840)
    assert display[0, 0].any() and display[-1, -1].any()
