"""Trusted-adapter inputs and untrusted-model tool arguments (draft v1)."""
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

Action = Literal["approach", "align", "pickup", "transport", "place", "stop"]
Phase = Literal["APPROACH", "ALIGN", "PICKUP", "VERIFY_PICKUP", "TRANSPORT",
                "PLACE", "VERIFY_PLACE", "COMPLETE", "BLOCKED", "UNKNOWN"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class Target(StrictModel):
    object_id: str = Field(min_length=1, max_length=384)
    revision: int = Field(ge=1)
    classification: dict
    dispatch_eligible: bool


class Mission(StrictModel):
    mission_id: str = Field(min_length=1, max_length=128)
    target: Target
    destination_id: str = Field(min_length=1, max_length=128)


class Observation(StrictModel):
    camera_id: str
    session_id: str
    frame_id: int = Field(ge=1)
    captured_at: float
    telemetry_at: float
    object_id: str
    target_revision: int = Field(ge=1)
    identity_evidence_id: str = Field(min_length=1)
    identity_confirmed: bool
    stationary: bool
    living_obstacle: bool
    safety: Literal["READY", "UNKNOWN", "STOPPED", "HELD"]
    after_command_id: str | None = None
    near_target: bool = False
    aligned: bool = False
    holding: bool = False
    at_destination: bool = False
    released: bool = False
    pickup_evidence_id: str | None = None
    placement_evidence_id: str | None = None
    pickup_command_id: str | None = None
    placement_command_id: str | None = None


class ToolCall(StrictModel):
    action: Action
    object_id: str
    destination_id: str


class Command(StrictModel):
    command_id: str
    mission_id: str
    object_id: str
    target_revision: int
    destination_id: str
    action: Action
    frame_id: int
    identity_evidence_id: str
    expires_at: float
    max_duration_ms: int = Field(ge=1, le=300)


class Receipt(StrictModel):
    command_id: str
    outcome: Literal["ACKNOWLEDGED", "REJECTED", "UNKNOWN"]


class Bridge(Protocol):
    mode: str

    def begin(self, mission_id: str) -> bool: ...
    def observe(self, mission: Mission) -> Observation: ...
    def execute(self, command: Command) -> Receipt: ...
    def stop(self, mission_id: str) -> Literal["STOPPED", "UNKNOWN"]: ...


class Policy(Protocol):
    def choose(self, mission: Mission, phase: Phase, observation: Observation) -> ToolCall: ...
