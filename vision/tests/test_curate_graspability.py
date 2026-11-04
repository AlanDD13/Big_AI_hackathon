import pytest

from vision.graspability import estimate_graspability, scale_from_reference
from vision.perception import Detection, curate


def det(label, score, box):
    return Detection(label, score, box, ((box[0] + box[2]) / 2, box[3]))


def test_curate_drops_weak_tiny_huge_and_background_but_keeps_living():
    detections = [
        det('bottle', .9, (10, 10, 60, 90)),
        det('bottle', .8, (12, 12, 62, 92)),      # near-duplicate box
        det('cup', .3, (100, 10, 140, 50)),        # weak
        det('speck', .9, (0, 0, 2, 2)),            # tiny
        det('box', .9, (0, 0, 200, 190)),          # fills the frame
        det('table', .95, (20, 20, 80, 80)),       # background class
        det('person', .27, (150, 10, 190, 90)),    # low-confidence living must survive
    ]
    kept = curate(detections, 200, 200, .45, .002, .4, 6)
    labels = sorted(d.label for d in kept)
    assert labels == ['bottle', 'person']


def test_curate_limits_object_count():
    detections = [det('cup', .9 - i * .01, (i * 20, 0, i * 20 + 15, 15)) for i in range(8)]
    assert len([d for d in curate(detections, 400, 400, .45, .0001, .4, 3)]) == 3


def test_graspability_verdicts():
    assert estimate_graspability((0, 0, 40, 40), None).verdict == 'UNKNOWN_SCALE'
    assert estimate_graspability((0, 0, 40, 40), 1.0).verdict == 'LIKELY_GRASPABLE'
    assert estimate_graspability((0, 0, 200, 150), 1.0).verdict == 'TOO_LARGE'
    assert estimate_graspability((0, 0, 8, 8), 1.0).verdict == 'TOO_SMALL'
    assert estimate_graspability((0, 0, 40, 400), 1.0).verdict == 'TOO_LARGE'
    assert estimate_graspability((5, 5, 5, 9), 1.0).verdict == 'UNKNOWN_SCALE'
    assert scale_from_reference(85.6, 214) == pytest.approx(0.4)
