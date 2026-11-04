"""Deterministic appearance association for one operator-selected local can.

This tracks a photographed template, not a DB identity or a grasp detector.
It supplies no metric range, floor calibration, destination or held evidence.
"""
from dataclasses import dataclass
import cv2
import numpy as np


@dataclass(frozen=True)
class Match:
    box: tuple
    score: float
    independent_margin: float


class CanTracker:
    def __init__(self, reference_jpeg, box):
        reference = cv2.imdecode(np.frombuffer(reference_jpeg, np.uint8), cv2.IMREAD_COLOR)
        x, y, width, height = box
        if reference is None or min(width, height) < 20:
            raise ValueError("invalid_can_reference")
        if min(x, y) < 0 or x + width > reference.shape[1] or y + height > reference.shape[0]:
            raise ValueError("reference_box_outside_image")
        self.template = cv2.cvtColor(reference[y:y + height, x:x + width], cv2.COLOR_BGR2GRAY)
        if float(self.template.std()) < 15:
            raise ValueError("can_reference_lacks_texture")
        self.reference_shape = reference.shape[:2]

    def locate(self, jpeg):
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None or image.shape != self.reference_shape:
            raise ValueError("camera_geometry_changed")
        candidates = []
        for scale in (0.9, 1.0, 1.1, 1.2, 1.3):
            template = cv2.resize(self.template, None, fx=scale, fy=scale)
            height, width = template.shape
            scores = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, position = cv2.minMaxLoc(scores)
            x, y = position
            # Suppress overlapping matches before measuring ambiguity.
            scores[max(0, y-height):y+height, max(0, x-width):x+width] = -1
            _, second, _, _ = cv2.minMaxLoc(scores)
            candidates.append(Match((x, y, width, height), score, score - second))
        best = max(candidates, key=lambda match: match.score)
        if best.score < 0.80 or best.independent_margin < 0.08:
            raise ValueError("can_association_ambiguous_or_lost")
        return best
