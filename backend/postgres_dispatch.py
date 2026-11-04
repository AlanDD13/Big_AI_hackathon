"""Postgres persistence for the internal dispatch coordinator."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, cast
from uuid import UUID

from backend.dispatch import ClaimOutcome, DispatchClaim, DispatchObservation

if TYPE_CHECKING:
    from psycopg import Connection


class PostgresDispatchStore:
    def __init__(
        self,
        connect: Callable[[], Connection],
        preflight: Callable[[Connection, UUID], object | None],
    ) -> None:
        """Use fresh connections and a local preflight that locks current evidence.

        Preflight must inspect the proposed route and current object evidence in
        this transaction. It returns the exact validated plan or None. Resolve
        remote inputs before entering this transaction.
        """
        self._connect = connect
        self._preflight = preflight

    def claim(self, mission_id: UUID) -> DispatchClaim:
        with self._connect() as connection, connection.transaction():
            mission = connection.execute(
                "SELECT state FROM rover_backend.missions WHERE id = %s", (mission_id,)
            ).fetchone()
            if mission is None:
                return DispatchClaim("not_found", None, None, None)

            plan = None
            if mission[0] == "queued":
                plan = self._preflight(connection, mission_id)
                if plan is None:
                    return DispatchClaim("preflight_failed", mission_id, "queued", None)

            row = connection.execute(
                "SELECT * FROM rover_backend.claim_dispatch(%s)", (mission_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError("Dispatch claim did not return an outcome")
            outcome, claimed_id, state, command_id = row
            if command_id is None:
                return DispatchClaim(cast(ClaimOutcome, outcome), claimed_id, state, None)
            if outcome == "claimed" and plan is None:
                raise RuntimeError("Dispatch was claimed without preflight")

            revision = connection.execute(
                "SELECT status_revision FROM rover_backend.missions WHERE id = %s",
                (claimed_id,),
            ).fetchone()
            if revision is None:
                raise RuntimeError("Claimed mission has no status revision")
            return DispatchClaim(
                cast(ClaimOutcome, outcome),
                claimed_id,
                state,
                command_id,
                preflighted_plan=plan if outcome == "claimed" else None,
                status_revision=revision[0],
            )

    def record_outcome(
        self, claim: DispatchClaim, observation: DispatchObservation
    ) -> bool:
        if claim.mission_id is None or claim.command_id is None or claim.status_revision is None:
            raise ValueError("Persisting dispatch status requires a revisioned command claim")
        with self._connect() as connection, connection.transaction():
            row = connection.execute(
                "SELECT outcome FROM rover_backend.record_dispatch_outcome(%s, %s, %s, %s, %s)",
                (
                    claim.mission_id,
                    claim.command_id,
                    claim.status_revision,
                    observation.state,
                    observation.reason,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("Dispatch update did not return an outcome")
            return row[0] == "recorded"
