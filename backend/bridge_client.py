"""Translate the robot-simulator contract into backend dispatch observations.

The bridge owns stage names. Mission storage keeps the backend's own states.
A lost command after a bridge restart is unknown and its command id is never sent again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from backend.dispatch import DispatchObservation


STAGE_STATES = {
    "NAVIGATING": "running",
    "ALIGNING": "running",
    "HELD": "holding",
    "PICKING": "running",
    "STOPPED": "stopped",
}
PICKUP_TTL_SECONDS = 3.0
POSE_MAX_AGE_SECONDS = 0.5
STATUS_UNKNOWN_AFTER_SECONDS = 3.0


@dataclass(frozen=True)
class BridgeInterpretation:
    observation: DispatchObservation
    reuse_command: bool
    stage: str | None
    status_revision: int | None


class BridgeContractError(ValueError):
    """The bridge replied with a body this caller cannot reconcile."""


def pose_is_fresh(*, pose_received_at: float, now: float) -> bool:
    """A new pickup needs a pose the backend received within the last 0.5 seconds."""
    age = now - pose_received_at
    return 0.0 <= age <= POSE_MAX_AGE_SECONDS


def status_is_unknown(*, last_status_at: float, now: float) -> bool:
    """Three missed one-second status intervals leave the link unknown."""
    return now - last_status_at >= STATUS_UNKNOWN_AFTER_SECONDS


def pickup_authorization(
    *,
    command_id: str,
    mission_id: str,
    object_id: str,
    target_revision: int,
    confirmation_evidence_id: str,
    issued_at: float,
) -> dict[str, object]:
    """Build the pickup body. expiresAt is exactly the 3 second contract limit."""
    return {
        "commandId": command_id,
        "missionId": mission_id,
        "objectId": object_id,
        "targetRevision": target_revision,
        "confirmationEvidenceId": confirmation_evidence_id,
        "expiresAt": issued_at + PICKUP_TTL_SECONDS,
    }


def interpret_bridge_response(status_code: int, body: Mapping[str, object] | None) -> BridgeInterpretation:
    """Map one bridge HTTP result. 404 UNKNOWN_COMMAND does not permit a resend."""
    payload = dict(body or {})
    if status_code == 404:
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if code != "UNKNOWN_COMMAND":
            raise BridgeContractError("Unexpected bridge 404")
        return BridgeInterpretation(
            DispatchObservation("unknown", "unknown_command"),
            reuse_command=False,
            stage=None,
            status_revision=None,
        )
    if status_code not in {200, 409}:
        raise BridgeContractError(f"Unexpected bridge status {status_code}")

    stage = payload.get("stage")
    if not isinstance(stage, str) or stage not in STAGE_STATES:
        raise BridgeContractError("Bridge stage is missing or unknown")
    reason = "authorized_picking" if stage == "PICKING" else None
    if status_code == 409:
        error = payload.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        if not isinstance(code, str) or not code:
            raise BridgeContractError("Bridge conflict is missing an error code")
        reason = code if stage != "PICKING" else "authorized_picking"
    revision = payload.get("statusRevision")
    if revision is not None and (isinstance(revision, bool) or not isinstance(revision, int)):
        raise BridgeContractError("statusRevision must be an integer")
    return BridgeInterpretation(
        DispatchObservation(STAGE_STATES[stage], reason),
        reuse_command=True,
        stage=stage,
        status_revision=None if revision is None else revision,
    )
