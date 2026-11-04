"""Advisory size-based graspability estimate for the EP Core gripper.

This is an ESTIMATE from a 2-D bounding box and an operator-supplied floor scale
(millimetres per pixel at the object's depth). It is never permission to drive or pick
up: it ignores depth, perspective, occlusion and weight, and the metric stationarity gate
in vision-v1 is unchanged. Without a scale the verdict is UNKNOWN_SCALE.

Inner jaw opening, closed gap, chassis reach and arm settle below were measured
on EP Core unit 3JKCK7W0030DJS, firmware 01.01.1150 (board comment 6639012926).
Those figures are measurements of that unit, not a brochure guess. The 15 mm
minimum is a 2-D bounding-box screen; the claw tips touch (0 mm gap). The 200 g
cap stays the existing conservative payload: crumpled paper (about 5 g) and an
empty 330 ml can (about 15 g) are inside it, and full cans were not lift-tested.
Grip force (5 N) and 220/150 mm arm travel remain DJI's published figures.
Override the active opening or payload with GRIPPER_MAX_OPENING_MM /
GRIPPER_MAX_PAYLOAD_G; an override is not a new measurement of this unit.
This estimate does not authorize pickup.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os

# Measured inner jaw opening at full open on unit 3JKCK7W0030DJS.
DEFAULT_MAX_OPENING_MM = 100.0
# Existing conservative cap, not a measured maximum lift. Demo items of about
# 5 g (crumpled paper) and about 15 g (empty 330 ml can) are inside it.
DEFAULT_MAX_PAYLOAD_G = 200.0
# Existing alignment margin. Usable opening on this unit is 90 mm.
OPENING_MARGIN_MM = 10.0
# Conservative 2-D bounding-box screen. Not the physical closed gap (tips touch).
MIN_GRASP_MM = 15.0
MAX_LENGTH_MM = 300.0
# Measured gap when the claw tips touch at full close. Do not use this as the screen.
PHYSICAL_CLOSED_GAP_MM = 0.0
MEASURED_SERIAL = "3JKCK7W0030DJS"
MEASURED_FIRMWARE = "01.01.1150"
MEASURED_BOARD_COMMENT = "6639012926"
PUBLISHED_SOURCE = "https://www.dji.com/support/product/robomaster-ep-core"


def robot_profile_from_env():
    """Active limits plus measurements from unit 3JKCK7W0030DJS.

    Opening, the 0 mm closed gap, reach and settle times are measurements of
    that unit (firmware 01.01.1150, board comment 6639012926). MIN_GRASP_MM is
    only a bounding-box screen. Grip force and 220/150 mm arm travel stay
    published brochure figures. unitCalibrated stays false: object size, weight
    and robot-to-object pose are not measured, and pickup is unverified.
    """
    opening, payload = limits_from_env()
    opening_measured = opening == DEFAULT_MAX_OPENING_MM
    payload_is_cap = payload == DEFAULT_MAX_PAYLOAD_G
    return {
        'profileId': 'dji-ep-core-unit-3JKCK7W0030DJS-v1',
        'robot': 'DJI RoboMaster EP Core',
        'serial': MEASURED_SERIAL,
        'firmware': MEASURED_FIRMWARE,
        'boardComment': MEASURED_BOARD_COMMENT,
        'nominalGripperOpeningMm': opening,
        'nominalGripperOpeningSource': (
            'measured-on-unit-3JKCK7W0030DJS' if opening_measured else 'env-override'),
        'openingMarginMm': OPENING_MARGIN_MM,
        'usableOpeningMm': max(0.0, opening - OPENING_MARGIN_MM),
        'usableOpeningNote': (
            'measured 100 mm inner jaw minus the existing 10 mm alignment margin'
            if opening_measured else
            'env override minus the existing 10 mm alignment margin; not the unit measurement'),
        'physicalClosedGapMm': PHYSICAL_CLOSED_GAP_MM,
        'physicalClosedGapNote': 'claw tips touch at full close; this is not the size-screen minimum',
        'minGraspScreenMm': MIN_GRASP_MM,
        'minGraspScreenNote': (
            '15 mm is a conservative 2-D bounding-box screen, not the 0 mm physical closed gap'),
        'nominalGripForceN': 5,
        'gripForceSource': 'published',
        'horizontalArmTravelMm': 220,
        'verticalArmTravelMm': 150,
        'armTravelSource': 'published',
        'publishedSource': PUBLISHED_SOURCE,
        'conservativePayloadG': payload,
        'payloadSource': 'existing-conservative-cap' if payload_is_cap else 'env-override',
        'payloadNote': (
            '200 g remains the conservative cap. Crumpled paper about 5 g and an empty '
            '330 ml can about 15 g are inside it. Full cans are not lift-tested and stay out of scope.'
            if payload_is_cap else
            'env override of the conservative payload cap; not a new measurement of this unit'),
        'demoItemMassG': {'crumpledPaperAbout': 5, 'empty330MlCanAbout': 15},
        'fullCansLiftTested': False,
        'unitCalibrated': False,
        'source': 'board comment 6639012926; unit 3JKCK7W0030DJS firmware 01.01.1150',
        'objectSizeMeasured': False,
        'objectWeightMeasured': False,
        'robotToObjectPoseKnown': False,
        'pickupVerified': False,
        'estimateAuthorizesPickup': False,
        'measuredArm': {
            'encoderXMm': 200,
            'encoderYMm': -63,
            'floorToGripperClearanceMm': 40,
            'approximateFloorContactYMm': -103,
            'tipsAheadOfFrontWheelsMm': 200,
            'tipsAheadOfFrontChassisBodyMm': 190,
            'waitForCompletedReturnsS': 0.30,
            'waitForCompletedWhileArmStillMoving': True,
            'physicalSettleS': 0.81,
            'verticalBacklashMmMin': 4,
            'verticalBacklashMmMax': 6,
        },
    }


@dataclass(frozen=True)
class Graspability:
    verdict: str  # SIZE_FITS | TOO_LARGE | TOO_SMALL | UNKNOWN_SCALE
    width_mm: float | None
    height_mm: float | None
    reason: str
    advisory: bool = True
    pickup_verified: bool = False


def scale_from_reference(real_mm: float, pixels: float) -> float:
    """mm per pixel from a known-size reference placed where the object will be."""
    if not (math.isfinite(real_mm) and math.isfinite(pixels)) or real_mm <= 0 or pixels <= 0:
        raise ValueError("reference size and pixel span must be positive and finite")
    return real_mm / pixels


def limits_from_env() -> tuple[float, float]:
    limits = (float(os.getenv("GRIPPER_MAX_OPENING_MM", DEFAULT_MAX_OPENING_MM)),
              float(os.getenv("GRIPPER_MAX_PAYLOAD_G", DEFAULT_MAX_PAYLOAD_G)))
    if any(not math.isfinite(v) or v <= 0 for v in limits):
        raise ValueError("Gripper limits must be positive, finite measurements")
    return limits


def estimate_graspability(bbox_px, mm_per_px: float | None,
                          max_opening_mm: float = DEFAULT_MAX_OPENING_MM) -> Graspability:
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox_px)
        valid = all(math.isfinite(v) for v in (x1, y1, x2, y2)) and x2 > x1 and y2 > y1
    except (TypeError, ValueError):
        valid = False
    if not valid:
        return Graspability("UNKNOWN_SCALE", None, None, "Invalid bounding box")
    if (mm_per_px is None or not math.isfinite(mm_per_px) or mm_per_px <= 0 or
            not math.isfinite(max_opening_mm) or max_opening_mm <= OPENING_MARGIN_MM):
        return Graspability("UNKNOWN_SCALE", None, None,
                            "No floor scale: set VISION_MM_PER_PX from a known reference object")
    width, height = (x2 - x1) * mm_per_px, (y2 - y1) * mm_per_px
    narrow, long_side = min(width, height), max(width, height)
    if narrow < MIN_GRASP_MM:
        return Graspability(
            "TOO_SMALL", width, height,
            f"Narrow side {narrow:.0f} mm is below the {MIN_GRASP_MM:.0f} mm bounding-box screen "
            f"(claw tips close to {PHYSICAL_CLOSED_GAP_MM:.0f} mm; screen is not the physical gap)")
    if narrow > max_opening_mm - OPENING_MARGIN_MM or long_side > MAX_LENGTH_MM:
        return Graspability("TOO_LARGE", width, height,
                            f"{width:.0f}x{height:.0f} mm exceeds the {max_opening_mm:.0f} mm gripper opening or length limit")
    return Graspability("SIZE_FITS", width, height,
                        f"{width:.0f}x{height:.0f} mm fits the size screen only; grip, thickness and weight are unverified")
