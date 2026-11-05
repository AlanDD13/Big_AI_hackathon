"""Metric forward-motion ranging of a stationary textured object.

For a rigid target and near-pure forward camera translation, image scale s
gives current optical depth Z = forward_baseline / (s - 1). This is NOT a
gripper distance: camera/chassis extrinsics and grasp pose are separate.
Reject low parallax, rotation, weak matches and excessive fit uncertainty.
"""
from dataclasses import dataclass
import math
import cv2
import numpy as np


@dataclass(frozen=True)
class RangeEstimate:
    optical_depth_m: float
    uncertainty_m: float
    scale: float
    inliers: int
    fit_error_px: float


def estimate_points(before_points, after_points, forward_m):
    if not math.isfinite(forward_m) or not 0.025 <= forward_m <= 0.10:
        raise ValueError("range_baseline_outside_25_to_100mm")
    before = np.asarray(before_points, dtype=np.float32).reshape(-1, 2)
    after = np.asarray(after_points, dtype=np.float32).reshape(-1, 2)
    if len(before) < 12 or before.shape != after.shape:
        raise ValueError("insufficient_range_feature_matches")
    matrix, mask = cv2.estimateAffinePartial2D(before, after, method=cv2.RANSAC,
        ransacReprojThreshold=1.5, maxIters=2000, confidence=0.99)
    if matrix is None:
        raise ValueError("range_fit_failed")
    valid = mask.ravel().astype(bool)
    count = int(valid.sum())
    if count < 12 or count / len(before) < 0.6:
        raise ValueError("range_fit_not_consistent")
    scale = float(math.hypot(matrix[0, 0], matrix[1, 0]))
    rotation = abs(math.degrees(math.atan2(matrix[1, 0], matrix[0, 0])))
    if rotation > 1.5 or not 1.035 <= scale <= 1.6:
        raise ValueError("range_low_parallax_or_rotation")
    predicted = before[valid] @ matrix[:, :2].T + matrix[:, 2]
    residual = np.linalg.norm(after[valid] - predicted, axis=1)
    error = float(np.sqrt(np.mean(residual ** 2)))
    spread = float(np.sqrt(np.mean(np.sum((before[valid] - before[valid].mean(axis=0)) ** 2, axis=1))))
    if spread < 12 or error > 1.0:
        raise ValueError("range_target_geometry_or_fit_weak")
    depth = forward_m / (scale - 1)
    # Include 1% scale systematics and 5mm odometry baseline error. This is a
    # conservative engineering bound, not a calibrated probabilistic interval.
    scale_error = max(0.01, 2 * error / spread / math.sqrt(count))
    uncertainty = depth * (0.005 / forward_m + scale_error / (scale - 1))
    if not 0.08 <= depth <= 2.5 or uncertainty > min(0.20, depth * 0.4):
        raise ValueError("range_uncertainty_too_large")
    return RangeEstimate(depth, uncertainty, scale, count, error)


def estimate_jpegs(before_jpeg, after_jpeg, before_box, forward_m):
    def decode(value):
        image = cv2.imdecode(np.frombuffer(value, np.uint8), cv2.IMREAD_GRAYSCALE)
        if image is None:
            raise ValueError("invalid_range_image")
        return image
    before, after = decode(before_jpeg), decode(after_jpeg)
    if before.shape != after.shape:
        raise ValueError("range_camera_geometry_changed")
    x, y, width, height = before_box
    mask = np.zeros_like(before)
    mask[y:y+height, x:x+width] = 255
    sift = cv2.SIFT_create(contrastThreshold=0.015)
    first, first_desc = sift.detectAndCompute(before, mask)
    second, second_desc = sift.detectAndCompute(after, None)
    if first_desc is None or second_desc is None:
        raise ValueError("range_can_texture_insufficient")
    matches = cv2.BFMatcher().knnMatch(first_desc, second_desc, k=2)
    good = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < 0.7 * pair[1].distance]
    points1 = [first[m.queryIdx].pt for m in good]
    points2 = [second[m.trainIdx].pt for m in good]
    return estimate_points(points1, points2, forward_m)
