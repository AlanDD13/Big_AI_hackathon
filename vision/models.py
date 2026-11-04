"""Versioned wire models. Detector scores are not calibrated probabilities."""
from enum import StrEnum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class WireModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True,
                             extra="forbid", allow_inf_nan=False)


class CameraKind(StrEnum):
    FIXED = "FIXED"
    ROVER = "ROVER"


class MappingEvidence(WireModel):
    camera_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    geometry_version: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    frame_id: int = Field(ge=0)
    track_id: str = Field(min_length=1, max_length=128)
    map_id: str = Field(min_length=1, max_length=128)
    map_version: str = Field(min_length=1, max_length=128)
    calibration_id: str = Field(min_length=1, max_length=128)
    calibration_valid: bool
    captured_at: float = Field(ge=0)
    received_at: float = Field(ge=0)
    floor_point_m: tuple[float, float] | None = None
    position_error_m: float | None = Field(default=None, ge=0)
    valid: bool
    invalid_reason: str | None = Field(max_length=200)

    @model_validator(mode="after")
    def valid_point(self):
        if self.valid and self.floor_point_m is None:
            raise ValueError("valid mapping evidence needs a floor point")
        if self.valid and not self.calibration_valid:
            raise ValueError("valid mapping evidence needs current calibration")
        if self.valid and self.position_error_m is None:
            raise ValueError("valid mapping evidence needs measured uncertainty")
        if not self.valid and self.floor_point_m is not None:
            raise ValueError("invalid position must be null")
        return self


class TargetRequest(WireModel):
    mission_id: str = Field(min_length=1, max_length=128)
    object_id: str = Field(min_length=1, max_length=256)
    rover_camera_id: str = Field(min_length=1, max_length=64)


class Event(WireModel):
    schema_version: str = "vision.v1"
    event_id: str
    sequence: int
    type: str
    timestamp: float
    payload: dict


class PixelTrack(WireModel):
    track_id: str = Field(min_length=1, max_length=128)
    object_id: str = Field(min_length=1, max_length=512)
    detector_label: str
    detector_score: float = Field(ge=0, le=1)
    bbox_px: tuple[float, float, float, float]
    contact_point_px: tuple[float, float] | None
    motion_state: Literal["OBSERVING", "MOVING", "STATIONARY", "EXCLUDED", "UNKNOWN"]
    pixel_stationary_seconds: float = Field(ge=0)
    exclusion_reason: str | None


class DetectionBatch(WireModel):
    camera_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    geometry_version: str = Field(min_length=1, max_length=128)
    frame_id: int = Field(ge=0)
    camera_kind: CameraKind
    captured_at: float = Field(ge=0)
    received_at: float = Field(ge=0)
    timestamp_basis: Literal["SOURCE", "RECEIVE"]
    width: int = Field(gt=0, le=8192)
    height: int = Field(gt=0, le=8192)
    tracks: list[PixelTrack] = Field(max_length=512)
    frame_ref: str


class DetectionEvent(Event):
    schema_version: Literal["vision.v1"] = "vision.v1"
    type: Literal["frame.detections"]
    payload: DetectionBatch
