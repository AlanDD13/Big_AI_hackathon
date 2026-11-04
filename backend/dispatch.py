"""Coordinate a committed dispatch claim with an injected robot bridge."""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID


ClaimOutcome = Literal[
    "claimed", "already_claimed", "rover_busy", "not_found", "not_queued", "preflight_failed"
]
BridgeState = Literal["dispatching", "running", "holding", "unknown",
                      "succeeded", "failed", "stopped", "cancelled"]
TERMINAL_STATES = {"succeeded", "failed", "stopped", "cancelled"}
BRIDGE_STATES = {"dispatching", "running", "holding", "unknown"} | TERMINAL_STATES


@dataclass(frozen=True)
class DispatchClaim:
    outcome: ClaimOutcome
    mission_id: UUID | None
    mission_state: str | None
    command_id: UUID | None
    preflighted_plan: object | None = None
    status_revision: int | None = None


@dataclass(frozen=True)
class BridgeReply:
    mission_id: UUID
    command_id: UUID
    state: BridgeState


@dataclass(frozen=True)
class DispatchObservation:
    state: BridgeState
    reason: str | None = None


@dataclass(frozen=True)
class DispatchResult:
    claim: DispatchClaim
    observation: DispatchObservation | None = None
    outcome_recorded: bool = False


class DispatchStore(Protocol):
    def claim(self, mission_id: UUID) -> DispatchClaim:
        """Return a committed claim with the exact preflighted plan on first claim."""
        ...

    def record_outcome(
        self, claim: DispatchClaim, observation: DispatchObservation
    ) -> bool:
        """Commit for the original command if the observed state is still current.

        Apply state transition checks atomically. Keep the rover slot reserved
        for unknown outcomes. Return false if another writer has advanced it.
        """
        ...


class RobotBridge(Protocol):
    def send(self, mission_id: UUID, command_id: UUID, plan: object) -> BridgeReply:
        """Send once and normalize bridge status to an internal mission state."""
        ...

    def query(self, mission_id: UUID, command_id: UUID) -> BridgeReply | None:
        """Look up the original command. None represents unavailable status."""
        ...


def advance_dispatch(
    mission_id: UUID, *, store: DispatchStore, bridge: RobotBridge
) -> DispatchResult:
    """Send once after a new claim or reconcile a previous claim by querying."""
    claim = store.claim(mission_id)
    if claim.outcome not in {"claimed", "already_claimed"}:
        return DispatchResult(claim)
    if claim.mission_id != mission_id or claim.command_id is None:
        raise ValueError("Dispatch claim has invalid mission or command identity")
    if claim.outcome == "claimed" and (
        claim.mission_state != "dispatching" or claim.preflighted_plan is None
    ):
        raise ValueError("A new dispatch claim requires dispatching state and a preflighted plan")
    if claim.mission_state in TERMINAL_STATES:
        return DispatchResult(claim)

    try:
        if claim.outcome == "claimed":
            reply = bridge.send(mission_id, claim.command_id, claim.preflighted_plan)
        else:
            reply = bridge.query(mission_id, claim.command_id)
    except TimeoutError:
        observation = DispatchObservation("unknown", "bridge_timeout")
    except ConnectionError:
        observation = DispatchObservation("unknown", "bridge_connection_error")
    else:
        if reply is None:
            observation = DispatchObservation("unknown", "bridge_status_unavailable")
        elif reply.mission_id != mission_id or reply.command_id != claim.command_id:
            observation = DispatchObservation("unknown", "bridge_identity_mismatch")
        elif not isinstance(reply.state, str) or reply.state not in BRIDGE_STATES:
            observation = DispatchObservation("unknown", "bridge_state_invalid")
        else:
            observation = DispatchObservation(reply.state)

    recorded = store.record_outcome(claim, observation)
    return DispatchResult(claim, observation, recorded)
