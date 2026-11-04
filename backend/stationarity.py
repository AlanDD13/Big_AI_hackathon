"""Evaluate metric floor stationarity from backend-owned observation history."""

from collections.abc import Iterable
from dataclasses import dataclass
from math import dist, isfinite


@dataclass(frozen=True)
class TrackContext:
    camera_id: str
    session_id: str
    geometry_version: str
    track_id: str
    map_id: str
    map_version: str
    calibration_id: str


@dataclass(frozen=True)
class MetricSample:
    context: TrackContext
    received_at: float
    point_m: tuple[float, float] | None
    position_error_m: float | None
    is_fixed_camera: bool
    position_valid: bool
    calibration_valid: bool


@dataclass(frozen=True)
class Stationarity:
    sample_count: int = 0
    span_seconds: float = 0.0

    @property
    def eligible(self) -> bool:
        """Whether the floor stationarity gate alone passes."""
        return self.sample_count >= 10 and self.span_seconds >= 5.0


def evaluate_stationarity(
    samples: Iterable[MetricSample], *, evaluated_at: float
) -> Stationarity:
    """Evaluate one track's deduplicated samples in vision receipt order.

    Both times must use the same clock. Include invalid samples because they
    break continuity. The caller resolves current track identity and applies
    classification, mission and any other dispatch gates separately.
    """
    if not isfinite(evaluated_at):
        raise ValueError("Evaluation time must be finite")

    previous_time = None
    anchor = None
    anchor_point = None
    sample_count = 0
    for sample in samples:
        received_at = sample.received_at
        if not isfinite(received_at) or (
            previous_time is not None and received_at < previous_time
        ):
            raise ValueError("Receipt times must be finite and ordered")
        gap = 0.0 if previous_time is None else received_at - previous_time
        previous_time = received_at
        point = sample.point_m
        error = sample.position_error_m
        if (
            not sample.is_fixed_camera
            or not sample.position_valid
            or not sample.calibration_valid
            or point is None
            or not all(isfinite(coordinate) for coordinate in point)
            or error is None
            or not 0.0 <= error <= 0.025
        ):
            anchor = None
            anchor_point = None
            sample_count = 0
            continue
        if (
            anchor is None
            or anchor_point is None
            or sample.context != anchor.context
            or gap > 1.0
            or dist(anchor_point, point) > 0.05
        ):
            anchor = sample
            anchor_point = point
            sample_count = 0
        sample_count += 1

    if anchor is None or previous_time is None:
        return Stationarity()
    if not 0.0 <= evaluated_at - previous_time <= 1.0:
        return Stationarity()
    return Stationarity(sample_count, previous_time - anchor.received_at)
