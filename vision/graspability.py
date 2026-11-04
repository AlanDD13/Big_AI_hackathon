"""Advisory size-based graspability estimate for the EP Core gripper.

This is an ESTIMATE from a 2-D bounding box and an operator-supplied floor scale
(millimetres per pixel at the object's depth). It is never permission to drive or pick
up: it ignores depth, perspective, occlusion and weight, and the metric stationarity gate
in vision-v1 is unchanged. Without a scale the verdict is UNKNOWN_SCALE.

Gripper limits come from DJI's published EP Core figures (max opening about 100 mm,
payload about 200-300 g). Override with GRIPPER_MAX_OPENING_MM / GRIPPER_MAX_PAYLOAD_G
after measuring the real hardware.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os

DEFAULT_MAX_OPENING_MM = 100.0
DEFAULT_MAX_PAYLOAD_G = 200.0  # conservative end of the published 200-300 g range
OPENING_MARGIN_MM = 10.0
MIN_GRASP_MM = 15.0
MAX_LENGTH_MM = 300.0


@dataclass(frozen=True)
class Graspability:
    verdict: str  # LIKELY_GRASPABLE | TOO_LARGE | TOO_SMALL | UNKNOWN_SCALE
    width_mm: float | None
    height_mm: float | None
    reason: str
    advisory: bool = True


def scale_from_reference(real_mm: float, pixels: float) -> float:
    """mm per pixel from a known-size reference placed where the object will be."""
    if not (math.isfinite(real_mm) and math.isfinite(pixels)) or real_mm <= 0 or pixels <= 0:
        raise ValueError("reference size and pixel span must be positive and finite")
    return real_mm / pixels


def limits_from_env() -> tuple[float, float]:
    return (float(os.getenv("GRIPPER_MAX_OPENING_MM", DEFAULT_MAX_OPENING_MM)),
            float(os.getenv("GRIPPER_MAX_PAYLOAD_G", DEFAULT_MAX_PAYLOAD_G)))


def estimate_graspability(bbox_px, mm_per_px: float | None,
                          max_opening_mm: float = DEFAULT_MAX_OPENING_MM) -> Graspability:
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox_px)
        valid = all(math.isfinite(v) for v in (x1, y1, x2, y2)) and x2 > x1 and y2 > y1
    except (TypeError, ValueError):
        valid = False
    if not valid:
        return Graspability("UNKNOWN_SCALE", None, None, "Invalid bounding box")
    if mm_per_px is None or not math.isfinite(mm_per_px) or mm_per_px <= 0:
        return Graspability("UNKNOWN_SCALE", None, None,
                            "No floor scale: set VISION_MM_PER_PX from a known reference object")
    width, height = (x2 - x1) * mm_per_px, (y2 - y1) * mm_per_px
    narrow, long_side = min(width, height), max(width, height)
    if narrow < MIN_GRASP_MM:
        return Graspability("TOO_SMALL", width, height, f"Narrow side {narrow:.0f} mm is below {MIN_GRASP_MM:.0f} mm")
    if narrow > max_opening_mm - OPENING_MARGIN_MM or long_side > MAX_LENGTH_MM:
        return Graspability("TOO_LARGE", width, height,
                            f"{width:.0f}x{height:.0f} mm exceeds the {max_opening_mm:.0f} mm gripper opening or length limit")
    return Graspability("LIKELY_GRASPABLE", width, height,
                        f"{width:.0f}x{height:.0f} mm fits the gripper; depth and weight not estimated")
