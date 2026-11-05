from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
import pytest

from vision.perception import Detection, PAPER_CLASSES, SAFETY_CLASSES, YoloDetector, _living
from vision.live_view import annotation_lines, frame_reference
from vision.graspability import estimate_graspability, limits_from_env


def test_paper_profile_keeps_small_floor_objects_and_safety_classes():
    class Array:
        def tolist(self): return [100, 100, 120, 120]
    class Model:
        def __init__(self, name): self.names = {}; self.prompts = []; self.calls = []
        def set_classes(self, classes):
            self.prompts = classes
            self.names = dict(enumerate(classes))
        def predict(self, **kwargs):
            self.calls.append(kwargs)
            return [SimpleNamespace(names=self.names, boxes=SimpleNamespace(cpu=lambda: [SimpleNamespace(
                xyxy=[Array()], cls=[0], conf=[.32])]))]
    data = BytesIO(); Image.new('RGB', (1000, 1000)).save(data, 'JPEG')
    with patch.dict('sys.modules', {'ultralytics': SimpleNamespace(YOLOE=Model)}):
        detector = YoloDetector(model='yoloe-26s-seg.pt', device='cpu', profile='paper')
        assert detector.detect(data.getvalue())[0].label == 'crumpled paper'
        assert set(SAFETY_CLASSES) <= set(detector._model.prompts)
        assert detector._model.calls[0]['imgsz'] == 960
    assert _living('hand')


def test_prompt_free_model_rejects_custom_prompts_and_bad_image_sizes():
    with pytest.raises(ValueError, match='prompt-free'):
        YoloDetector(classes=['paper'], profile='default', device='cpu')
    with pytest.raises(ValueError):
        YoloDetector(image_size=9999, device='cpu')


def test_viewer_uses_grok_identity_and_separate_advisory():
    obj = dict(detectorLabel='comet', detectorScore=.98, motionState='STATIONARY',
               bboxPx=[0, 0, 40, 40], classificationStatus='COMPLETED',
               classificationProvider='grok', classification={'description': 'crumpled paper', 'confidence': 'MEDIUM'})
    lines = annotation_lines(obj, None, 100)
    assert 'crumpled paper' in lines[0] and 'comet' in lines[1]
    assert 'UNKNOWN' in lines[2]
    assert 'UNVERIFIED' in annotation_lines(obj, 1, 100)[2]
    obj['exclusionReason'] = 'PERSON_OVERLAP'
    assert 'EXCLUDED' in annotation_lines(obj, 1, 100)[1]


def test_empty_scene_still_uses_current_processed_frame():
    camera = {'stream': {'session_id': 'new', 'latest_frame_id': 7},
              'processed': {'sessionId': 'new', 'frameId': 6, 'geometryVersion': 'g'}}
    assert frame_reference(camera, []) == ('new', 6, 'g')
    camera['processed']['sessionId'] = 'old'
    assert frame_reference(camera, []) == ('new', 7, None)


def test_invalid_gripper_limits_never_make_size_fit(monkeypatch):
    assert estimate_graspability((0, 0, 40, 40), 1, float('nan')).verdict == 'UNKNOWN_SCALE'
    monkeypatch.setenv('GRIPPER_MAX_OPENING_MM', 'nan')
    with pytest.raises(ValueError): limits_from_env()


def test_tiled_paper_proposals_keep_original_coordinates_and_living_exclusions():
    class Array:
        def tolist(self): return [10, 20, 30, 40]
    class Model:
        def __init__(self, name): self.names = {}; self.calls = 0
        def set_classes(self, classes): self.names = dict(enumerate(classes))
        def predict(self, **kwargs):
            self.calls += 1
            label = 'person' if self.calls == 1 else 'paper ball'
            box = SimpleNamespace(xyxy=[Array()], cls=[list(self.names.values()).index(label)], conf=[.18])
            return [SimpleNamespace(names=self.names, boxes=SimpleNamespace(cpu=lambda: [box]))]
    out = BytesIO(); Image.new('RGB', (640, 480)).save(out, 'JPEG')
    with patch.dict('sys.modules', {'ultralytics': SimpleNamespace(YOLOE=Model)}):
        detector = YoloDetector(model='yoloe-26s-seg.pt', profile='paper', device='cpu')
        detections = detector.detect(out.getvalue())
    assert any(d.label == 'person' for d in detections)
    assert any(d.label == 'paper ball' and d.bbox == (266, 212, 286, 232) for d in detections)
    assert detector._model.calls == 5


@pytest.mark.parametrize('label', ['can', 'plastic bottle'])
def test_zoom_tiles_propose_cans_and_bottles_not_only_paper(label):
    class Array:
        def tolist(self): return [10, 20, 30, 40]
    class Model:
        def __init__(self, name): self.names = {}; self.calls = 0
        def set_classes(self, classes): self.names = dict(enumerate(classes))
        def predict(self, **kwargs):
            self.calls += 1
            boxes = [] if self.calls == 1 else [SimpleNamespace(
                xyxy=[Array()], cls=[list(self.names.values()).index(label)], conf=[.7])]
            return [SimpleNamespace(names=self.names, boxes=SimpleNamespace(cpu=lambda: boxes))]
    out = BytesIO(); Image.new('RGB', (640, 480)).save(out, 'JPEG')
    with patch.dict('sys.modules', {'ultralytics': SimpleNamespace(YOLOE=Model)}):
        detector = YoloDetector(model='yoloe-26s-seg.pt', profile='paper', device='cpu')
        detections = detector.detect(out.getvalue())
    assert any(d.label == label and d.bbox == (266, 212, 286, 232) for d in detections)


def test_prompt_free_detector_boxes_any_object_without_a_class_list():
    class Array:
        def tolist(self): return [10, 20, 30, 40]
    class Model:
        def __init__(self, name):
            self.names = {0: 'shoe'}
            self.calls = 0
        def set_classes(self, classes):
            raise AssertionError('YOLO must not apply a classification vocabulary')
        def predict(self, **kwargs):
            self.calls += 1
            boxes = [] if self.calls == 1 else [SimpleNamespace(
                xyxy=[Array()], cls=[0], conf=[.4])]
            return [SimpleNamespace(names=self.names, boxes=SimpleNamespace(cpu=lambda: boxes))]
    out = BytesIO(); Image.new('RGB', (640, 480)).save(out, 'JPEG')
    with patch.dict('sys.modules', {'ultralytics': SimpleNamespace(YOLOE=Model)}):
        detector = YoloDetector(model='yoloe-26s-seg-pf.pt', profile='default', device='cpu')
        detections = detector.detect(out.getvalue())
    assert detector.classes == ()
    assert any(d.label == 'shoe' and d.bbox == (266, 212, 286, 232) for d in detections)
    assert detector._model.calls == 5
