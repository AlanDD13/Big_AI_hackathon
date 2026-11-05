import numpy as np
from vision.live_view import display_objects, fit_preview, frame_reference, render_preview


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


def test_stale_boxes_remain_visible_on_their_exact_processed_frame():
    objects = [dict(sessionId='s', frameId=10, geometryVersion='g', status='LOST',
                    eligibilityReason='STALE_OBSERVATION', potentialTarget=False,
                    bboxPx=[1800, 1200, 1960, 1400], detectorLabel='can', detectorScore=.58,
                    motionState='UNKNOWN', exclusionReason=None, classificationStatus='PENDING'),
               dict(sessionId='s', frameId=10, geometryVersion='g', status='LOST',
                    eligibilityReason='TRACK_LOST', bboxPx=[10, 10, 40, 40])]
    shown = display_objects(objects, ('s', 10, 'g'))
    assert [item[0]['detectorLabel'] for item in shown] == ['can']
    assert shown[0][1] is True
    frame = np.zeros((2760, 3840, 3), dtype=np.uint8)
    blank = render_preview(frame, [], None, 100)
    preview = render_preview(frame, shown, None, 100)
    assert int(np.any(preview != blank, axis=2).sum()) > 200
    assert preview.shape[1] <= 1100 and preview.shape[0] <= 790
