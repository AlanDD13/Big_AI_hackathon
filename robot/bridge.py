"""Closed tool surface and HTTP bridge for P4 (robot/**).

Implements:
- contracts/robot-simulator-v1.md & contracts/fixtures/robot-simulator.json
  (POST /commands/pickup, POST /commands/{id}/hold, POST /commands/{id}/stop,
   POST /commands/{id}/rearm, GET /commands/{id}, POST /stop)
- contracts/manual-control-v1.md & contracts/fixtures/manual-control.json
  (POST /manual/lease, POST /manual/drive, POST /manual/release,
   POST /manual/rearm, GET /manual/status)
- GET /camera/frame & GET /health (via RoverFrameBuffer from robot/camera.py)

All motion and state transitions sit behind SafetyController + Watchdog.
Callers never receive the DJI SDK, a raw velocity socket, or a path around stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
import re
import threading
import time
from typing import Callable
from urllib.parse import parse_qs, urlsplit

from camera import RoverFrameBuffer, verify_bearer_header
from safety import Limits, SafetyController, SafetyError, Watchdog

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
OBJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}:[A-Za-z0-9_-]{1,64}:[A-Za-z0-9_-]{1,64}$")
PRE_PICKUP_STAGES = ("NAVIGATING", "ALIGNING")
ALL_COMMAND_STAGES = ("NAVIGATING", "ALIGNING", "HELD", "PICKING", "STOPPED")
PICKUP_MAX_TTL_S = 3.0
DEFAULT_LEASE_TTL_MS = 2000
MAX_LEASE_TTL_MS = 10000


def _valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(ID_RE.match(value))


def _valid_object_id(value: object) -> bool:
    return isinstance(value, str) and bool(OBJECT_ID_RE.match(value))


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


@dataclass
class CommandRecord:
    command_id: str
    mission_id: str
    object_id: str | None = None
    stage: str = "NAVIGATING"
    status_revision: int = 1
    held: bool = False
    stopped: bool = False
    rearmed: bool = False
    binding: dict[str, object] | None = None
    stop_pending: bool = False

    def snapshot(self, boot_id: str, *, acted: bool | None = None, motion: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "commandId": self.command_id,
            "missionId": self.mission_id,
            "objectId": self.object_id,
            "stage": self.stage,
            "statusRevision": self.status_revision,
            "held": self.held,
            "stopped": self.stopped,
            "rearmed": self.rearmed,
            "motion": motion,
            "bridgeBootId": boot_id,
            "binding": dict(self.binding) if self.binding is not None else None,
        }
        if acted is not None:
            payload["acted"] = acted
        return payload


@dataclass
class ManualLease:
    lease_id: str
    rover_id: str
    generation: int
    issued_at: float
    expires_at: float
    ttl_s: float
    last_sequence: int = 0
    driving: bool = False


class RobotBridge:
    """In-memory command state machine and manual control bridge guarded by SafetyController."""

    def __init__(
        self,
        controller: SafetyController,
        frame_buffer: RoverFrameBuffer | None = None,
        *,
        rover_id: str = "rover-1",
        wall_clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        allow_implicit_commands: bool = True,
        physical: bool = False,
    ) -> None:
        self.controller = controller
        self.frame_buffer = frame_buffer or RoverFrameBuffer(
            camera_id=rover_id,
            wall_clock=wall_clock,
            monotonic_clock=monotonic_clock,
        )
        self.rover_id = rover_id
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self.allow_implicit_commands = allow_implicit_commands
        self.physical = physical
        self._manual_interlock = False
        self._watchdog: Watchdog | None = None
        self._lock = threading.Lock()
        self._commands: dict[str, CommandRecord] = {}
        self._retired_command_ids: set[str] = set()
        self._lease: ManualLease | None = None
        self._lease_counter = 0
        self._last_pose_received_at: float | None = None

    def start(self) -> None:
        if self._watchdog is None:
            self._watchdog = Watchdog(self.controller)
            self._watchdog.start()
        elif not self._watchdog.alive:
            raise RuntimeError("Bridge watchdog exited; explicit recovery required")

    def close(self) -> None:
        if self._watchdog is not None:
            watchdog, self._watchdog = self._watchdog, None
            watchdog.close()
        else:
            self.controller.stop("SHUTDOWN")

    def _sync_stop_unlocked(self, rec: CommandRecord) -> None:
        if rec.stop_pending and self.controller.tick().get("stopConfirmed"):
            rec.stage = "STOPPED"
            rec.stop_pending = False
            rec.status_revision += 1

    @property
    def boot_id(self) -> str:
        return self.controller.boot_id

    def observe_pose_timestamp(self, received_at: float | None = None) -> None:
        with self._lock:
            self._last_pose_received_at = self._wall_clock() if received_at is None else float(received_at)

    def restart_InMemory_state(self, new_controller: SafetyController) -> str:
        """Simulate a bridge process restart: new boot_id, prior commands become 404 UNKNOWN_COMMAND."""
        with self._lock:
            self._retired_command_ids.update(self._commands.keys())
            self._commands.clear()
            self._lease = None
            self.controller = new_controller
            self.frame_buffer.new_session()
            return self.controller.boot_id

    def seed_command(
        self,
        command_id: str,
        mission_id: str = "mission-1",
        object_id: str | None = None,
        *,
        stage: str = "NAVIGATING",
    ) -> dict[str, object]:
        if not _valid_id(command_id) or not _valid_id(mission_id):
            raise ValueError("Invalid commandId or missionId")
        if object_id is not None and not _valid_object_id(object_id):
            raise ValueError("Invalid objectId")
        if stage not in ALL_COMMAND_STAGES:
            raise ValueError(f"Invalid stage {stage!r}")
        with self._lock:
            rec = CommandRecord(
                command_id=command_id,
                mission_id=mission_id,
                object_id=object_id,
                stage=stage,
                status_revision=1,
                held=(stage == "HELD"),
                stopped=(stage == "STOPPED"),
            )
            self._commands[command_id] = rec
            self._retired_command_ids.discard(command_id)
            return rec.snapshot(self.boot_id)

    def _has_active_autonomous_command_unlocked(self) -> bool:
        for rec in self._commands.values():
            if rec.stage in ("NAVIGATING", "ALIGNING", "PICKING", "HELD", "UNKNOWN"):
                return True
        return False

    def _has_active_manual_lease_unlocked(self, now_wall: float) -> bool:
        if self._lease is None:
            return False
        if now_wall > self._lease.expires_at:
            self._lease = None
            return False
        return True

    def _get_or_init_command_unlocked(
        self,
        command_id: str,
        mission_id: str,
        object_id: str | None = None,
    ) -> CommandRecord | None:
        if command_id in self._commands:
            return self._commands[command_id]
        if command_id in self._retired_command_ids or not self.allow_implicit_commands:
            return None
        rec = CommandRecord(
            command_id=command_id,
            mission_id=mission_id,
            object_id=object_id,
            stage="NAVIGATING",
            status_revision=1,
        )
        self._commands[command_id] = rec
        return rec

    @staticmethod
    def _error_response(
        http_status: int,
        code: str,
        message: str,
        extra: dict[str, object] | None = None,
    ) -> tuple[int, dict[str, object]]:
        body: dict[str, object] = {"error": {"code": code, "message": message}}
        if extra:
            body.update(extra)
        return http_status, body

    # ------------------------------------------------------------------
    # Mission Command Endpoints (contracts/robot-simulator-v1.md)
    # ------------------------------------------------------------------

    def get_command(self, command_id: str) -> tuple[int, dict[str, object]]:
        if not _valid_id(command_id):
            return self._error_response(422, "VALIDATION_ERROR", "Invalid commandId")
        with self._lock:
            rec = self._commands.get(command_id)
            if rec is None:
                return self._error_response(
                    404,
                    "UNKNOWN_COMMAND",
                    f"Command {command_id!r} is unknown on bridgeBootId {self.boot_id}",
                )
            self._sync_stop_unlocked(rec)
            return 200, rec.snapshot(self.boot_id)

    def pickup(
        self,
        payload: dict[str, object],
        *,
        now_override: float | None = None,
    ) -> tuple[int, dict[str, object]]:
        if not isinstance(payload, dict):
            return self._error_response(422, "VALIDATION_ERROR", "JSON object body required")

        command_id = payload.get("commandId")
        mission_id = payload.get("missionId")
        object_id = payload.get("objectId")
        target_rev = payload.get("targetRevision")
        evidence_id = payload.get("confirmationEvidenceId")
        expires_at = payload.get("expiresAt")

        if (
            not _valid_id(command_id)
            or not _valid_id(mission_id)
            or not _valid_object_id(object_id)
            or not _valid_id(evidence_id)
            or not isinstance(target_rev, int)
            or isinstance(target_rev, bool)
            or target_rev < 1
            or not _is_finite_number(expires_at)
        ):
            return self._error_response(
                422,
                "VALIDATION_ERROR",
                "pickup requires valid commandId, missionId, objectId, targetRevision, confirmationEvidenceId, and expiresAt",
            )

        now_wall = self._wall_clock() if now_override is None else float(now_override)
        exp_val = float(expires_at)
        binding = {
            "commandId": command_id,
            "missionId": mission_id,
            "objectId": object_id,
            "targetRevision": target_rev,
            "confirmationEvidenceId": evidence_id,
            "expiresAt": exp_val,
        }

        with self._lock:
            if self._has_active_manual_lease_unlocked(now_wall):
                return self._error_response(
                    409,
                    "MANUAL_LEASE_ACTIVE",
                    "Cannot authorize autonomous pickup while a manual lease is active",
                )

            ctrl_status = self.controller.tick() if (self.physical or self._manual_interlock) else self.controller.status()
            if (self.physical or self._manual_interlock) and (
                ctrl_status.get("stage") != "IDLE" or ctrl_status.get("releasePending")
                or not ctrl_status.get("telemetryFresh") or not ctrl_status.get("heartbeatFresh")
            ):
                if ctrl_status.get("stage") == "STOPPED":
                    return self._error_response(409, "STOP_LOCKED", "Confirmed stop requires explicit rearm")
                return self._error_response(503, "TELEMETRY_STALE", "Manual/hardware stop is unconfirmed or authority is not idle")

            rec = self._get_or_init_command_unlocked(command_id, mission_id, object_id)
            if rec is None:
                return self._error_response(
                    404,
                    "UNKNOWN_COMMAND",
                    f"Command {command_id!r} is unknown after bridge restart",
                )

            if rec.stopped or rec.stage == "STOPPED":
                return self._error_response(
                    409,
                    "STOPPED",
                    "Command is latched STOPPED",
                    rec.snapshot(self.boot_id, acted=False),
                )

            if rec.held or rec.stage == "HELD":
                return self._error_response(
                    409,
                    "HELD",
                    "Hold already won before pickup",
                    rec.snapshot(self.boot_id, acted=False),
                )

            if rec.stage == "PICKING":
                if rec.binding == binding:
                    return 200, rec.snapshot(self.boot_id, acted=False)
                return self._error_response(
                    409,
                    "CONFLICT",
                    "Conflicting pickup authorization for existing commandId",
                    rec.snapshot(self.boot_id, acted=False),
                )

            if now_wall > exp_val:
                return self._error_response(
                    409,
                    "EXPIRED",
                    "Pickup authorization expiresAt has passed",
                    rec.snapshot(self.boot_id, acted=False),
                )
            if exp_val - now_wall > PICKUP_MAX_TTL_S + 1e-6:
                return self._error_response(
                    422,
                    "VALIDATION_ERROR",
                    f"pickup expiresAt exceeds {PICKUP_MAX_TTL_S}s maximum TTL",
                    rec.snapshot(self.boot_id, acted=False),
                )

            if rec.stage not in PRE_PICKUP_STAGES:
                return self._error_response(
                    409,
                    "CONFLICT",
                    f"Pickup is not legal from stage {rec.stage}",
                    rec.snapshot(self.boot_id, acted=False),
                )

            rec.object_id = object_id
            rec.binding = binding
            rec.stage = "PICKING"
            rec.status_revision += 1
            return 200, rec.snapshot(self.boot_id, acted=True)

    def hold_command(self, command_id: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        if not isinstance(payload, dict):
            return self._error_response(422, "VALIDATION_ERROR", "JSON object body required")
        body_cid = payload.get("commandId", command_id)
        mission_id = payload.get("missionId")
        reason = payload.get("reason")
        if (
            not _valid_id(command_id)
            or body_cid != command_id
            or not _valid_id(mission_id)
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            return self._error_response(422, "VALIDATION_ERROR", "hold requires matching commandId, missionId, and reason")

        with self._lock:
            rec = self._get_or_init_command_unlocked(command_id, mission_id)
            if rec is None:
                return self._error_response(
                    404,
                    "UNKNOWN_COMMAND",
                    f"Command {command_id!r} is unknown after bridge restart",
                )
            if rec.stopped or rec.stage == "STOPPED":
                return self._error_response(
                    409,
                    "STOPPED",
                    "Command is already STOPPED",
                    rec.snapshot(self.boot_id, acted=False),
                )
            if rec.stage == "PICKING":
                return self._error_response(
                    409,
                    "ALREADY_PICKING",
                    "Cannot hold a command that has already entered PICKING",
                    rec.snapshot(self.boot_id, acted=False),
                )
            if rec.stage == "HELD" and rec.held:
                return 200, rec.snapshot(self.boot_id, acted=False)

            rec.stage = "HELD"
            rec.held = True
            rec.status_revision += 1
            return 200, rec.snapshot(self.boot_id, acted=True)

    def stop_command(self, command_id: str, payload: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        body = payload or {}
        if not isinstance(body, dict):
            return self._error_response(422, "VALIDATION_ERROR", "JSON object body required")
        body_cid = body.get("commandId", command_id)
        mission_id = body.get("missionId", "mission-1")
        reason = str(body.get("reason") or "OPERATOR_STOP")
        if not _valid_id(command_id) or body_cid != command_id or not _valid_id(mission_id):
            return self._error_response(422, "VALIDATION_ERROR", "stop requires valid commandId and missionId")

        ctrl_status = self.controller.stop(reason)

        with self._lock:
            self._lease = None
            rec = self._get_or_init_command_unlocked(command_id, mission_id)
            if rec is None:
                return self._error_response(
                    404,
                    "UNKNOWN_COMMAND",
                    f"Command {command_id!r} is unknown after bridge restart",
                )
            stage = "UNKNOWN" if self.physical and not ctrl_status.get("stopConfirmed") else "STOPPED"
            if rec.stage != stage or not rec.stopped or rec.rearmed:
                rec.stage = stage
                rec.stopped = True
                rec.stop_pending = stage == "UNKNOWN"
                rec.rearmed = False
                rec.status_revision += 1
            return 200, rec.snapshot(self.boot_id, motion=False)

    def rearm_command(self, command_id: str, payload: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        body = payload or {}
        if not isinstance(body, dict):
            return self._error_response(422, "VALIDATION_ERROR", "JSON object body required")
        body_cid = body.get("commandId", command_id)
        mission_id = body.get("missionId")
        if not _valid_id(command_id) or body_cid != command_id or not _valid_id(mission_id):
            return self._error_response(422, "VALIDATION_ERROR", "rearm requires matching commandId and missionId")

        with self._lock:
            rec = self._commands.get(command_id)
            if rec is None:
                return self._error_response(
                    404,
                    "UNKNOWN_COMMAND",
                    f"Command {command_id!r} is unknown",
                )
            self._sync_stop_unlocked(rec)
            if rec.stage != "STOPPED" or not rec.stopped:
                return self._error_response(
                    409,
                    "NOT_STOPPED",
                    "rearm is legal only when the command is STOPPED",
                    rec.snapshot(self.boot_id, motion=False),
                )
            ctrl_status = self.controller.status()
            if self.physical and not rec.rearmed and not ctrl_status.get("stopConfirmed"):
                return self._error_response(409, "NOT_STOPPED", "Physical stop is not confirmed", rec.snapshot(self.boot_id))
            if rec.rearmed:
                return 200, rec.snapshot(self.boot_id, motion=False)
            if ctrl_status.get("stage") == "STOPPED":
                try:
                    self.controller.rearm()
                except SafetyError as exc:
                    return self._error_response(409, "NOT_STOPPED", f"Safety core rejected rearm: {exc.code}", rec.snapshot(self.boot_id))
            if not rec.rearmed:
                rec.rearmed = True
                rec.status_revision += 1
            return 200, rec.snapshot(self.boot_id, motion=False)

    def emergency_stop(self, payload: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        body = payload or {}
        reason = str(body.get("reason") or "operator_stop") if isinstance(body, dict) else "operator_stop"
        rover_id = str(body.get("roverId") or self.rover_id) if isinstance(body, dict) else self.rover_id
        ctrl_state = self.controller.stop(reason)
        with self._lock:
            self._lease = None
            stopped_ids = []
            for rec in self._commands.values():
                stage = "UNKNOWN" if self.physical and not ctrl_state.get("stopConfirmed") else "STOPPED"
                if rec.stage != stage or not rec.stopped or rec.rearmed:
                    rec.stage = stage
                    rec.stopped = True
                    rec.stop_pending = stage == "UNKNOWN"
                    rec.rearmed = False
                    rec.status_revision += 1
                stopped_ids.append(rec.command_id)
            return 200, {
                "roverId": rover_id,
                "bridgeBootId": self.boot_id,
                "stage": ctrl_state["stage"],
                "zeroVelocityRequested": True,
                "stationaryConfirmed": bool(ctrl_state["stopConfirmed"]),
                "stationarySamples": int(ctrl_state["stationarySamples"]),
                "motion": False,
                "stoppedCommands": stopped_ids,
            }

    # ------------------------------------------------------------------
    # Manual Control Endpoints (contracts/manual-control-v1.md)
    # ------------------------------------------------------------------

    def _caps_dict(self) -> dict[str, object]:
        lim: Limits = self.controller.limits
        return {
            "maxAbsXMps": lim.max_x,
            "maxAbsYMps": lim.max_y,
            "maxAbsZDegps": lim.max_yaw,
            "watchdogTimeoutMs": 300,
            "maxIntentTtlMs": int(round(lim.max_intent_s * 1000)),
            "maxTelemetryAgeMs": int(round(lim.telemetry_s * 1000)),
            "stationarySamplesRequired": 3,
            "stationarySpanMs": int(round(lim.stationary_span_s * 1000)),
        }

    def _manual_stage_unlocked(self, ctrl_status: dict[str, object], now_wall: float) -> str:
        c_stage = str(ctrl_status.get("stage"))
        if c_stage in ("STOPPED", "UNKNOWN"):
            return c_stage
        if not ctrl_status.get("telemetryFresh"):
            return "UNKNOWN"
        if self._has_active_manual_lease_unlocked(now_wall):
            assert self._lease is not None
            return "MANUAL_DRIVING" if (c_stage == "DRIVING" or self._lease.driving) else "MANUAL_IDLE"
        return "IDLE"

    def manual_lease(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        if not isinstance(payload, dict):
            return self._error_response(422, "VALIDATION_ERROR", "JSON object body required")
        rover_id = payload.get("roverId", self.rover_id)
        req_ttl_ms = payload.get("requestedTtlMs", DEFAULT_LEASE_TTL_MS)
        if rover_id != self.rover_id or not _is_finite_number(req_ttl_ms):
            return self._error_response(422, "VALIDATION_ERROR", "Invalid roverId or requestedTtlMs")
        ttl_ms = int(float(req_ttl_ms))
        if ttl_ms <= 0 or ttl_ms > MAX_LEASE_TTL_MS:
            return self._error_response(422, "VALIDATION_ERROR", f"requestedTtlMs must be in 1..{MAX_LEASE_TTL_MS}")

        ctrl_status = self.controller.tick()
        now_wall = self._wall_clock()

        with self._lock:
            if self._has_active_autonomous_command_unlocked():
                return self._error_response(
                    409,
                    "AUTONOMOUS_ACTIVE",
                    "Cannot grant manual lease while an autonomous command is active",
                )
            if ctrl_status["stage"] == "STOPPED":
                return self._error_response(
                    409,
                    "STOP_LOCKED",
                    "Rover is latched in STOPPED; explicit rearm required before lease",
                )
            if ctrl_status["stage"] == "UNKNOWN" or not ctrl_status["telemetryFresh"]:
                return self._error_response(
                    503,
                    "TELEMETRY_STALE",
                    "Rover stage is UNKNOWN or telemetry is stale",
                )
            if self._has_active_manual_lease_unlocked(now_wall):
                return self._error_response(
                    409,
                    "LEASE_HELD",
                    "Manual lease is already held by an active session",
                )

            self._lease_counter += 1
            lease_id = f"lease-{self._lease_counter}"
            gen = int(ctrl_status["generation"])
            ttl_s = ttl_ms / 1000.0
            lease = ManualLease(
                lease_id=lease_id,
                rover_id=self.rover_id,
                generation=gen,
                issued_at=now_wall,
                expires_at=now_wall + ttl_s,
                ttl_s=ttl_s,
            )
            self._lease = lease
            self._manual_interlock = True
            return 200, {
                "leaseId": lease.lease_id,
                "roverId": lease.rover_id,
                "generation": lease.generation,
                "leaseGeneration": lease.generation,
                "issuedAt": lease.issued_at,
                "expiresAt": lease.expires_at,
                "bridgeBootId": self.boot_id,
                "mode": "MANUAL_CHASSIS_SLICE_1",
                "overheadNavigationMode": "SIMULATED_ONLY",
                "manipulationMode": "SIMULATED_ONLY",
                "caps": self._caps_dict(),
            }

    def manual_drive(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        if not isinstance(payload, dict):
            return self._error_response(422, "VALIDATION_ERROR", "JSON object body required")

        rover_id = payload.get("roverId", self.rover_id)
        lease_id = payload.get("leaseId")
        lease_gen = payload.get("leaseGeneration", payload.get("generation"))
        sequence = payload.get("sequence")
        issued_at = payload.get("issuedAt")
        expires_at = payload.get("expiresAt")
        x_mps = payload.get("xMps")
        y_mps = payload.get("yMps")
        z_degps = payload.get("zDegps", payload.get("yawDegS"))
        frame = payload.get("frame", "BODY")

        if (
            rover_id != self.rover_id
            or not _valid_id(lease_id)
            or not isinstance(lease_gen, int)
            or isinstance(lease_gen, bool)
            or not isinstance(sequence, int)
            or isinstance(sequence, bool)
            or sequence < 1
            or frame != "BODY"
            or not _is_finite_number(issued_at)
            or not _is_finite_number(expires_at)
            or not _is_finite_number(x_mps)
            or not _is_finite_number(y_mps)
            or not _is_finite_number(z_degps)
        ):
            self.controller.stop("INVALID_MANUAL_DRIVE")
            with self._lock:
                self._lease = None
            return self._error_response(422, "VALIDATION_ERROR", "Malformed or non-finite manual drive payload")

        now_wall = self._wall_clock()
        with self._lock:
            if self._has_active_autonomous_command_unlocked():
                return self._error_response(
                    409,
                    "AUTONOMOUS_ACTIVE",
                    "Cannot execute manual drive while an autonomous command is active",
                )
            if self._lease is not None and now_wall > self._lease.expires_at:
                self._lease = None
                self.controller.stop("LEASE_EXPIRED")
                return self._error_response(409, "LEASE_EXPIRED", "Manual lease has expired")
            if self._lease is None or self._lease.lease_id != lease_id or self._lease.generation != lease_gen:
                self.controller.stop("AUTHORITY_CONFLICT")
                self._lease = None
                return self._error_response(409, "LEASE_HELD", "Manual lease does not match active session")

        try:
            status = self.controller.drive(
                str(lease_id),
                int(lease_gen),
                int(sequence),
                float(issued_at),
                float(expires_at),
                float(x_mps),
                float(y_mps),
                float(z_degps),
                boot_id=self.boot_id,
            )
        except SafetyError as err:
            http_status, http_code = self.controller.manual_http_error(err)
            if http_status == 422:
                http_code = "VALIDATION_ERROR"
            with self._lock:
                self._lease = None
            return self._error_response(http_status, http_code, f"Drive rejected by safety core: {err.code}")

        with self._lock:
            if self._lease is not None and self._lease.lease_id == lease_id and self._lease.generation == lease_gen:
                self._lease.expires_at = now_wall + self._lease.ttl_s
                self._lease.last_sequence = int(sequence)
                self._lease.driving = any(abs(v) > 0 for v in (float(x_mps), float(y_mps), float(z_degps)))
            pose_ts = self._last_pose_received_at if self._last_pose_received_at is not None else now_wall
            return 200, {
                "roverId": self.rover_id,
                "bridgeBootId": self.boot_id,
                "stage": "MANUAL_DRIVING" if status["stage"] == "DRIVING" else "MANUAL_IDLE",
                "leaseId": str(lease_id),
                "leaseGeneration": int(lease_gen),
                "lastSequence": int(sequence),
                "appliedVelocity": {
                    "xMps": float(x_mps),
                    "yMps": float(y_mps),
                    "zDegps": float(z_degps),
                },
                "telemetryFresh": bool(status["telemetryFresh"]),
                "poseReceivedAt": pose_ts,
                "watchdogTimeoutMs": 300,
            }

    def manual_release(self, payload: dict[str, object] | None = None, *, require_authority=False) -> tuple[int, dict[str, object]]:
        body = payload or {}
        if not isinstance(body, dict):
            return self._error_response(422, "VALIDATION_ERROR", "JSON object body required")
        reason = str(body.get("reason") or "user_release")
        if reason not in ("user_release", "blur", "tab_hidden"):
            reason = "user_release"
        with self._lock:
            if require_authority:
                generation = body.get("leaseGeneration")
                if (body.get("roverId") != self.rover_id or not _valid_id(body.get("leaseId"))
                        or type(generation) is not int or generation < 1):
                    return self._error_response(422, "VALIDATION_ERROR", "Release requires rover, lease and generation")
                if (self._lease is None or self._lease.lease_id != body["leaseId"]
                        or self._lease.generation != generation
                        or self._wall_clock() > self._lease.expires_at):
                    return self._error_response(409, "LEASE_EXPIRED", "Release authority no longer matches")
            self._manual_interlock = True
            # Publish the core's pending stop before freeing the lease. Pickup
            # uses this same lock and cannot slip into an idle-looking gap.
            try:
                self.controller.release(reason=reason)
            finally:
                self._lease = None
        return 200, {
            "roverId": self.rover_id,
            "released": True,
            "bridgeBootId": self.boot_id,
            "stage": "UNKNOWN",
            "zeroVelocityRequested": True,
            "stationaryConfirmed": False,
            "stationarySamples": 0,
            "motion": False,
        }

    def manual_rearm(self, payload: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        body = payload or {}
        if not isinstance(body, dict):
            return self._error_response(422, "VALIDATION_ERROR", "JSON object body required")
        try:
            self.controller.rearm()
        except SafetyError as err:
            http_status, http_code = self.controller.manual_http_error(err)
            return self._error_response(http_status, http_code, f"Rearm rejected: {err.code}")
        status = self.controller.status()
        with self._lock:
            self._lease = None
        return 200, {
            "roverId": self.rover_id,
            "bridgeBootId": self.boot_id,
            "stage": status["stage"],
            "rearmed": True,
            "motion": False,
            "stationaryConfirmed": True,
        }

    def manual_status(self, rover_id: str | None = None) -> tuple[int, dict[str, object]]:
        if rover_id is not None and rover_id != self.rover_id:
            return self._error_response(404, "UNKNOWN_ROVER", f"Unknown roverId {rover_id!r}")
        ctrl_status = self.controller.tick()
        now_wall = self._wall_clock()
        with self._lock:
            stage = self._manual_stage_unlocked(ctrl_status, now_wall)
            lease = self._lease if self._has_active_manual_lease_unlocked(now_wall) else None
            cmd_vel = ctrl_status.get("requestedVelocity", (0.0, 0.0, 0.0))
            return 200, {
                "roverId": self.rover_id,
                "bridgeBootId": self.boot_id,
                "stage": stage,
                "leaseId": lease.lease_id if lease else None,
                "leaseGeneration": lease.generation if lease else None,
                "lastSequence": lease.last_sequence if lease else None,
                "appliedVelocity": {
                    "xMps": float(cmd_vel[0]),
                    "yMps": float(cmd_vel[1]),
                    "zDegps": float(cmd_vel[2]),
                },
                "telemetryFresh": bool(ctrl_status["telemetryFresh"]),
                "poseReceivedAt": self._last_pose_received_at,
                "stationaryConfirmed": bool(ctrl_status["stopConfirmed"] or stage in ("IDLE", "MANUAL_IDLE")),
                "stationarySamples": int(ctrl_status["stationarySamples"]),
                "caps": self._caps_dict(),
                "watchdogAlive": bool(ctrl_status.get("watchdogAlive")),
                "watchdogError": ctrl_status.get("watchdogError"),
            }


class PhysicalChassisAdapter:
    """Real chassis output, using the same caps as the safety controller."""

    def __init__(self, rover, *, max_x=0.35, max_y=0.35, max_yaw=45.0):
        self.rover = rover
        self.limits = Limits(max_x=max_x, max_y=max_y, max_yaw=max_yaw)

    def __call__(self, x, y, yaw):
        if not all(_is_finite_number(v) for v in (x, y, yaw)):
            raise ValueError("Non-finite chassis velocity")
        if abs(x) > self.limits.max_x or abs(y) > self.limits.max_y or abs(yaw) > self.limits.max_yaw:
            raise ValueError("Physical chassis velocity exceeds configured caps")
        if any((x, y, yaw)):
            self.rover.chassis.drive_speed(x=x, y=y, z=yaw, timeout=1.0)
        else:
            timer = getattr(self.rover.chassis, "_auto_timer", None)
            if timer is not None and timer.is_alive():
                timer.cancel()
            self.rover.chassis.drive_speed(x=0.0, y=0.0, z=0.0)


class PhysicalBridgeRuntime:
    """One passed-in SDK session; telemetry and watchdog have explicit ownership."""

    def __init__(self, rover, frame_buffer=None, *, rover_id="rover-1", output=None):
        from watchdog_bench import Telemetry

        self.output = output if output is not None else PhysicalChassisAdapter(rover)
        self.controller = SafetyController(self.output, limits=self.output.limits)
        self.bridge = RobotBridge(self.controller, frame_buffer, rover_id=rover_id, physical=True)
        controller, bridge = self.controller, self.bridge

        class BridgeTelemetry(Telemetry):
            def velocity(self, value):
                previous_sample = self._sample_id
                super().velocity(value)
                # Only a NEW coherent sample feeds heartbeat and pose freshness.
                if self._sample_id != previous_sample:
                    controller.heartbeat()
                    bridge.observe_pose_timestamp()

        self.telemetry = BridgeTelemetry(rover, self.controller)

    def start(self):
        try:
            self.controller.stop("STARTUP")
            self.telemetry.start()
            self.bridge.start()
        except Exception:
            self.close()
            raise

    def close(self):
        try:
            self.bridge.close()
        finally:
            self.telemetry.close()


class BridgeHTTPServer:
    """HTTP server exposing the closed P4 tool surface (/camera/frame, /commands/*, /manual/*, /stop)."""

    def __init__(
        self,
        bridge: RobotBridge,
        *,
        host: str = "127.0.0.1",
        port: int = 8004,
        peer_token: str | None = None,
    ) -> None:
        self.bridge = bridge
        self.host = host
        self.port = port
        self.peer_token = peer_token if peer_token is not None else os.getenv("PEER_TOKEN")
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def _make_handler(self):
        parent = self
        cmd_sub_re = re.compile(r"^/commands/([A-Za-z0-9_-]{1,128})(?:/(hold|stop|rearm))?$")

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args) -> None:
                pass

            def _send_json(self, status_code: int, payload: dict[str, object]) -> None:
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(raw)

            def _check_auth(self) -> bool:
                ok, status, code, msg = verify_bearer_header(
                    self.headers.get("Authorization"),
                    parent.peer_token,
                )
                if not ok:
                    self._send_json(status, {"error": {"code": code, "message": msg}})
                    return False
                return True

            def _read_json_body(self) -> tuple[dict[str, object] | None, bool]:
                length_str = self.headers.get("Content-Length", "0")
                try:
                    length = int(length_str)
                except ValueError:
                    self._send_json(422, {"error": {"code": "VALIDATION_ERROR", "message": "Invalid Content-Length"}})
                    return None, False
                if length <= 0:
                    return {}, True
                if length > 65536:
                    self._send_json(413, {"error": {"code": "PAYLOAD_TOO_LARGE", "message": "Request body too large"}})
                    return None, False
                raw = self.rfile.read(length)
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except Exception:
                    self._send_json(422, {"error": {"code": "VALIDATION_ERROR", "message": "Malformed JSON body"}})
                    return None, False
                if not isinstance(parsed, dict):
                    self._send_json(422, {"error": {"code": "VALIDATION_ERROR", "message": "JSON object required"}})
                    return None, False
                return parsed, True

            def do_GET(self) -> None:
                parsed_url = urlsplit(self.path)
                path = parsed_url.path

                if path == "/health":
                    health = parent.bridge.frame_buffer.health_snapshot()
                    _, m_status = parent.bridge.manual_status()
                    health["bridgeBootId"] = parent.bridge.boot_id
                    health["manualStage"] = m_status.get("stage")
                    health["executionMode"] = "physical" if parent.bridge.physical else "simulator"
                    health["manualCaps"] = m_status.get("caps")
                    health["manualBootFence"] = "bridgeBootId-v1"
                    health["watchdogAlive"] = m_status.get("watchdogAlive", False)
                    health["watchdogError"] = m_status.get("watchdogError")
                    self._send_json(200, health)
                    return

                if not self._check_auth():
                    return

                if path == "/camera/frame":
                    frame, reason = parent.bridge.frame_buffer.get_fresh_frame()
                    if frame is None:
                        self._send_json(
                            503,
                            {"error": {"code": "STALE_FRAME", "message": reason or "Camera frame unavailable"}},
                        )
                        return
                    self.send_response(200)
                    for k, v in frame.response_headers().items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(frame.jpeg_bytes)
                    return

                if path == "/manual/status":
                    qs = parse_qs(parsed_url.query)
                    rover_id = qs.get("roverId", [None])[0]
                    status, body = parent.bridge.manual_status(rover_id)
                    self._send_json(status, body)
                    return

                m = cmd_sub_re.match(path)
                if m and m.group(2) is None:
                    status, body = parent.bridge.get_command(m.group(1))
                    self._send_json(status, body)
                    return

                self._send_json(404, {"error": {"code": "NOT_FOUND", "message": f"Unknown route {path}"}})

            def do_POST(self) -> None:
                if not self._check_auth():
                    return
                path = urlsplit(self.path).path
                body, ok = self._read_json_body()
                if not ok or body is None:
                    return

                # A server instance's boot is immutable. Validate BEFORE any
                # controller/lease mutation; no status-then-write restart race.
                if path in ("/manual/drive", "/manual/release", "/manual/rearm"):
                    expected_boot = body.get("bridgeBootId")
                    if not isinstance(expected_boot, str) or not re.fullmatch(r"[a-f0-9]{32}", expected_boot):
                        self._send_json(422, {"error": {"code": "VALIDATION_ERROR", "message": "bridgeBootId is required"}})
                        return
                    if expected_boot != parent.bridge.boot_id:
                        self._send_json(409, {"error": {"code": "BRIDGE_BOOT_MISMATCH", "message": "Bridge restarted; reconcile authority"}})
                        return

                if path == "/commands/pickup":
                    status, resp = parent.bridge.pickup(body)
                    self._send_json(status, resp)
                    return

                if path == "/stop":
                    status, resp = parent.bridge.emergency_stop(body)
                    self._send_json(status, resp)
                    return

                if path == "/manual/lease":
                    status, resp = parent.bridge.manual_lease(body)
                    self._send_json(status, resp)
                    return

                if path == "/manual/drive":
                    status, resp = parent.bridge.manual_drive(body)
                    self._send_json(status, resp)
                    return

                if path == "/manual/release":
                    status, resp = parent.bridge.manual_release(body, require_authority=True)
                    self._send_json(status, resp)
                    return

                if path == "/manual/rearm":
                    status, resp = parent.bridge.manual_rearm(body)
                    self._send_json(status, resp)
                    return

                m = cmd_sub_re.match(path)
                if m and m.group(2) is not None:
                    cid, action = m.group(1), m.group(2)
                    if action == "hold":
                        status, resp = parent.bridge.hold_command(cid, body)
                    elif action == "stop":
                        status, resp = parent.bridge.stop_command(cid, body)
                    else:
                        status, resp = parent.bridge.rearm_command(cid, body)
                    self._send_json(status, resp)
                    return

                self._send_json(404, {"error": {"code": "NOT_FOUND", "message": f"Unknown route {path}"}})

        return Handler

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            return f"http://{self.host}:{self.port}"
        addr, bound_port = self._httpd.server_address[:2]
        return f"http://{addr}:{bound_port}"

    def start(self) -> str:
        if self._httpd is not None:
            raise RuntimeError("BridgeHTTPServer already started")
        self._httpd = ThreadingHTTPServer((self.host, self.port), self._make_handler())
        try:
            self.bridge.start()
        except Exception:
            self._httpd.server_close()
            self._httpd = None
            raise
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="rover-bridge-http",
            daemon=True,
        )
        self._thread.start()
        return self.base_url

    def close(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self.bridge.close()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("simulator", "physical"), default="simulator")
    parser.add_argument("--sn", help="14-character robot serial (required for --mode physical)")
    parser.add_argument("--rover-id", default="rover-1")
    parser.add_argument("--resolution", choices=("360p", "720p"), default="360p")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8004)
    parser.add_argument("--allow-motion", action="store_true", help="Required for physical chassis actuation")
    parser.add_argument("--max-x-mps", type=float, default=0.35)
    parser.add_argument("--max-y-mps", type=float, default=0.35)
    parser.add_argument("--max-yaw-degps", type=float, default=45.0)
    args = parser.parse_args()
    try:
        configured_limits = Limits(max_x=args.max_x_mps, max_y=args.max_y_mps,
                                   max_yaw=args.max_yaw_degps)
    except ValueError as exc:
        parser.error(str(exc))

    peer_token = os.getenv("PEER_TOKEN")
    if not peer_token:
        raise SystemExit("PEER_TOKEN environment variable is required")

    if args.mode == "simulator":
        ctrl = SafetyController(lambda x, y, yaw: True, limits=configured_limits)
        buf = RoverFrameBuffer(camera_id=args.rover_id, max_age_s=1.0)
        bridge = RobotBridge(ctrl, buf, rover_id=args.rover_id)
        server = BridgeHTTPServer(bridge, host=args.host, port=args.port, peer_token=peer_token)
        base_url = server.start()
        print(
            json.dumps(
                {
                    "status": "SERVING",
                    "mode": "simulator",
                    "baseUrl": base_url,
                    "roverId": args.rover_id,
                    "bridgeBootId": bridge.boot_id,
                }
            ),
            flush=True,
        )
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            server.close()
        return 0

    if not args.sn:
        parser.error("--sn is required with --mode physical")
    if not args.allow_motion:
        parser.error("--mode physical requires --allow-motion")

    from camera import EPStreamCapture
    from check_connection import rover_session

    with rover_session(args.sn) as (rover, identity):
        buf = RoverFrameBuffer(camera_id=args.rover_id, max_age_s=1.0)
        output = PhysicalChassisAdapter(rover, max_x=configured_limits.max_x,
                                       max_y=configured_limits.max_y,
                                       max_yaw=configured_limits.max_yaw)
        runtime = PhysicalBridgeRuntime(rover, buf, rover_id=args.rover_id, output=output)
        bridge = runtime.bridge
        capture = EPStreamCapture(rover, buf, resolution=args.resolution, subscribe_distance=True)
        server = BridgeHTTPServer(bridge, host=args.host, port=args.port, peer_token=peer_token)
        try:
            runtime.start()
            capture.start(wait_first_frame_s=5.0)
            base_url = server.start()
            print(
                json.dumps(
                    {
                        "status": "SERVING",
                        "mode": "physical",
                        "baseUrl": base_url,
                        "roverId": args.rover_id,
                        "bridgeBootId": bridge.boot_id,
                        "identity": identity,
                    }
                ),
                flush=True,
            )
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            try:
                server.close()
            finally:
                try:
                    runtime.close()
                finally:
                    capture.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
