import pytest

from vision.graspability import (
    DEFAULT_MAX_OPENING_MM, DEFAULT_MAX_PAYLOAD_G, MIN_GRASP_MM, OPENING_MARGIN_MM,
    PHYSICAL_CLOSED_GAP_MM, estimate_graspability, robot_profile_from_env, scale_from_reference,
)
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
    size_fit = estimate_graspability((0, 0, 40, 40), 1.0)
    assert size_fit.verdict == 'SIZE_FITS'
    assert not size_fit.pickup_verified
    assert estimate_graspability((0, 0, 200, 150), 1.0).verdict == 'TOO_LARGE'
    too_small = estimate_graspability((0, 0, 8, 8), 1.0)
    assert too_small.verdict == 'TOO_SMALL'
    assert not too_small.pickup_verified
    assert 'bounding-box screen' in too_small.reason
    assert estimate_graspability((0, 0, 40, 400), 1.0).verdict == 'TOO_LARGE'
    assert estimate_graspability((5, 5, 5, 9), 1.0).verdict == 'UNKNOWN_SCALE'
    assert scale_from_reference(85.6, 214) == pytest.approx(0.4)


def test_unit_measurements_label_the_screen_and_do_not_authorize_pickup(monkeypatch):
    monkeypatch.delenv('GRIPPER_MAX_OPENING_MM', raising=False)
    monkeypatch.delenv('GRIPPER_MAX_PAYLOAD_G', raising=False)
    assert DEFAULT_MAX_OPENING_MM == 100
    assert OPENING_MARGIN_MM == 10
    assert MIN_GRASP_MM == 15
    assert PHYSICAL_CLOSED_GAP_MM == 0
    assert DEFAULT_MAX_PAYLOAD_G == 200
    profile = robot_profile_from_env()
    assert profile['serial'] == '3JKCK7W0030DJS'
    assert profile['firmware'] == '01.01.1150'
    assert profile['boardComment'] == '6639012926'
    assert profile['nominalGripperOpeningMm'] == 100
    assert profile['nominalGripperOpeningSource'] == 'measured-on-unit-3JKCK7W0030DJS'
    assert profile['usableOpeningMm'] == 90
    assert profile['physicalClosedGapMm'] == 0
    assert profile['minGraspScreenMm'] == 15
    assert 'bounding-box screen' in profile['minGraspScreenNote']
    assert '0 mm' in profile['minGraspScreenNote']
    arm = profile['measuredArm']
    assert (arm['encoderXMm'], arm['encoderYMm']) == (200, -63)
    assert arm['floorToGripperClearanceMm'] == 40
    assert arm['approximateFloorContactYMm'] == -103
    assert arm['tipsAheadOfFrontWheelsMm'] == 200
    assert arm['tipsAheadOfFrontChassisBodyMm'] == 190
    assert arm['waitForCompletedReturnsS'] == pytest.approx(0.30)
    assert arm['waitForCompletedWhileArmStillMoving'] is True
    assert arm['physicalSettleS'] == pytest.approx(0.81)
    assert (arm['verticalBacklashMmMin'], arm['verticalBacklashMmMax']) == (4, 6)
    assert profile['demoItemMassG'] == {'crumpledPaperAbout': 5, 'empty330MlCanAbout': 15}
    assert profile['fullCansLiftTested'] is False
    assert profile['conservativePayloadG'] == 200
    assert profile['gripForceSource'] == 'published'
    assert profile['armTravelSource'] == 'published'
    assert profile['unitCalibrated'] is False
    assert profile['objectSizeMeasured'] is False
    assert profile['objectWeightMeasured'] is False
    assert profile['robotToObjectPoseKnown'] is False
    assert profile['pickupVerified'] is False
    assert profile['estimateAuthorizesPickup'] is False
