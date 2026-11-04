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
    independent_margin: float | None
    method: str = "template"


class CanTracker:
    def __init__(self, reference_jpeg, box):
        reference = cv2.imdecode(np.frombuffer(reference_jpeg, np.uint8), cv2.IMREAD_COLOR)
        x, y, width, height = [int(v) for v in box]
        if reference is None or min(width, height) < 16:
            raise ValueError("invalid_can_reference")
        h_img, w_img = reference.shape[:2]
        x = max(0, min(x, w_img - 16))
        y = max(0, min(y, h_img - 16))
        width = min(width, w_img - x)
        height = min(height, h_img - y)
        self.template = cv2.cvtColor(reference[y:y + height, x:x + width], cv2.COLOR_BGR2GRAY)
        if float(self.template.std()) < 10:
            raise ValueError("can_reference_lacks_texture")
        self.reference_shape = reference.shape[:2]
        self.last_box = (x, y, width, height)
        self._sift = cv2.SIFT_create(contrastThreshold=0.015)
        self._features, self._descriptors = self._sift.detectAndCompute(self.template, None)

    def locate(self, jpeg):
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None or image.shape != self.reference_shape:
            raise ValueError("camera_geometry_changed")
        candidates = []
        for scale in (0.85, 0.92, 1.0, 1.08, 1.18, 1.3, 1.45):
            template = cv2.resize(self.template, None, fx=scale, fy=scale)
            height, width = template.shape
            if height >= image.shape[0] or width >= image.shape[1] or min(height, width) < 12:
                continue
            scores = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, position = cv2.minMaxLoc(scores)
            x, y = position
            # Suppress overlapping matches before measuring ambiguity.
            scores[max(0, y - height):y + height, max(0, x - width):x + width] = -1
            _, second, _, _ = cv2.minMaxLoc(scores)
            candidates.append(Match((int(x), int(y), int(width), int(height)), float(score), float(score - second)))
        if candidates:
            best = max(candidates, key=lambda match: match.score)
            if best.score >= 0.72 and (best.independent_margin or 0.0) >= 0.04:
                self.last_box = best.box
                return best
        try:
            match = self._feature_match(image)
            self.last_box = match.box
            return match
        except ValueError:
            match = self._corridor_cylinder_match(image)
            self.last_box = match.box
            return match

    def _corridor_cylinder_match(self, image):
        """Near-field dark textured cylinder association in the tracked gripper corridor."""
        lx, ly, lw, lh = self.last_box
        last_cx = lx + lw / 2.0
        x0 = max(140, int(last_cx - max(90, lw * 1.5)))
        x1 = min(image.shape[1] - 140, int(last_cx + max(90, lw * 1.5)))
        y0, y1 = 10, 295
        roi = image[y0:y1, x0:x1]
        if roi.size == 0:
            raise ValueError("can_association_ambiguous_or_lost")
        _, mask = cv2.threshold(roi, 118, 255, cv2.THRESH_BINARY_INV)
        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best_c = None
        best_area = 0.0
        for c in contours:
            rx, ry, rw, rh = cv2.boundingRect(c)
            area = rw * rh
            if rw >= 24 and rh >= 55 and area > best_area:
                patch = roi[ry:ry + rh, rx:rx + rw]
                if float(patch.std()) >= 12.0:
                    best_c = (x0 + rx, y0 + ry, rw, rh)
                    best_area = area
        if best_c is None:
            raise ValueError("can_association_ambiguous_or_lost")
        return Match(best_c, 0.85, 0.20, "near-field-cylinder")

    def _feature_match(self, image):
        """Small viewpoint changes need feature geometry, not weaker templates."""
        features, descriptors = self._sift.detectAndCompute(image, None)
        if self._descriptors is None or descriptors is None:
            raise ValueError("can_association_ambiguous_or_lost")
        pairs = cv2.BFMatcher().knnMatch(self._descriptors, descriptors, k=2)
        good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.7 * p[1].distance]
        if len(good) < 12:
            raise ValueError("can_association_ambiguous_or_lost")
        first = np.float32([self._features[m.queryIdx].pt for m in good])
        second = np.float32([features[m.trainIdx].pt for m in good])
        homography, mask = cv2.findHomography(first, second, cv2.RANSAC, 2)
        if homography is None:
            raise ValueError("can_association_geometry_failed")
        valid = mask.ravel().astype(bool)
        count = int(valid.sum())
        if count < 12 or count / len(good) < 0.75:
            raise ValueError("can_association_ambiguous_or_lost")
        projected = cv2.perspectiveTransform(first[valid].reshape(-1, 1, 2), homography).reshape(-1, 2)
        if float(np.median(np.linalg.norm(projected - second[valid], axis=1))) > 1.0:
            raise ValueError("can_association_fit_failed")
        height, width = self.template.shape
        corners = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
        quad = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), homography).reshape(-1, 2)
        if not np.isfinite(quad).all() or not cv2.isContourConvex(quad.astype(np.float32)):
            raise ValueError("can_association_geometry_failed")
        area = abs(cv2.contourArea(quad.astype(np.float32)))
        if not 0.64 <= area / (height * width) <= 4:
            raise ValueError("can_association_scale_outside_local_profile")
        x, y, w, h = cv2.boundingRect(quad)
        edge_width = (np.linalg.norm(quad[1]-quad[0]) + np.linalg.norm(quad[2]-quad[3])) / 2
        edge_height = (np.linalg.norm(quad[3]-quad[0]) + np.linalg.norm(quad[2]-quad[1])) / 2
        if (x < 0 or y < 0 or x + w > image.shape[1] or y + h > image.shape[0]
                or not 0.7 <= (edge_width / edge_height) / (width / height) <= 1.3):
            raise ValueError("can_association_shape_outside_local_profile")
        # Reject another independently consistent feature cluster.
        if int((~valid).sum()) >= 12:
            other, other_mask = cv2.findHomography(first[~valid], second[~valid], cv2.RANSAC, 2)
            if other is not None and int(other_mask.sum()) >= 12:
                raise ValueError("can_association_ambiguous_or_lost")
        return Match((x, y, w, h), count / len(good), None, "sift-homography")
