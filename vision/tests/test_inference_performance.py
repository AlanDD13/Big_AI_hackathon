"""Inference regressions: batching must preserve image association and exclusions."""
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PIL import Image

from vision.perception import YoloDetector


class Boxes(list):
    def cpu(self):
        self.transfers += 1
        return self

    def __init__(self, items):
        super().__init__(items)
        self.transfers = 0


def box(label, coords):
    return SimpleNamespace(cls=[label], conf=[.8], xyxy=[SimpleNamespace(tolist=lambda: coords)])


@pytest.mark.parametrize('device,expected_batches', [('mps', [1, 4]), ('cpu', [1, 1, 1, 1, 1])])
def test_tiles_keep_original_coordinates_all_classes_and_full_frame_safety(device, expected_batches, monkeypatch):
    monkeypatch.setenv('VISION_PAPER_TILES', '1')
    detector = YoloDetector(device='cpu', min_area_frac=0, max_area_frac=1, max_objects=20)
    detector.device = device
    calls, boxes = [], []

    def predict(**kwargs):
        source = kwargs['source']
        images = source if isinstance(source, list) else [source]
        calls.append(len(images))
        results = []
        for image in images:
            # A person in the whole frame must remain visible to the tracker.
            whole = image.size == (641, 481)
            found = Boxes([box(0 if whole else 1, [10, 20, 30, 40])])
            boxes.append(found)
            results.append(SimpleNamespace(boxes=found, names={0: 'person', 1: 'can'}))
        return results

    detector._model = SimpleNamespace(predict=predict)
    image = BytesIO(); Image.new('RGB', (641, 481)).save(image, 'JPEG')
    detections = detector.detect(image.getvalue())
    assert calls == expected_batches
    assert any(d.label == 'person' for d in detections)
    assert {d.bbox for d in detections if d.label == 'can'} == {
        (10, 20, 30, 40), (10, 212, 30, 232),
        (266, 20, 286, 40), (266, 212, 286, 232),
    }
    assert all(b.transfers == 1 for b in boxes)


def test_batch_result_count_mismatch_fails_closed(monkeypatch):
    monkeypatch.setenv('VISION_PAPER_TILES', '1')
    detector = YoloDetector(device='cpu')
    detector.device = 'mps'
    empty = SimpleNamespace(boxes=Boxes([]), names={})
    detector._model = SimpleNamespace(predict=lambda **kw: [empty])
    image = BytesIO(); Image.new('RGB', (640, 480)).save(image, 'JPEG')
    with pytest.raises(ValueError, match='result count'):
        detector.detect(image.getvalue())
