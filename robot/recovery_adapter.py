"""P4 physical chassis adapter proposal; no SDK ownership or graph gate bypass.

The caller supplies the sole running PhysicalBridgeRuntime, backend authority,
trusted perception, and calibrated deterministic plans. None may be Grok output.
Only approach/align are implemented. No arm or autonomous transport is enabled.
"""

from dataclasses import dataclass
import math
from pathlib import Path
import sqlite3
import threading
import time


@dataclass(frozen=True)
class Receipt:
    command_id: str
    outcome: str


@dataclass(frozen=True)
class ChassisPlan:
    """Trusted planner result bound to the exact command/evidence snapshot."""
    command_id: str
    object_id: str
    target_revision: int
    frame_id: int
    identity_evidence_id: str
    calibrated: bool
    x_mps: float
    y_mps: float
    yaw_deg_s: float
    duration_s: float


class Journal:
    """Local no-replay protection, additional to P3's durable mission authority.

    An interrupted ATTEMPTING remains UNKNOWN forever until external reconciliation.
    No automatic reset, command resend, mission release, or successful replay.
    """
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS mission (id TEXT PRIMARY KEY)")
        self.db.execute("CREATE TABLE IF NOT EXISTS command (id TEXT PRIMARY KEY, outcome TEXT NOT NULL)")
        self.lock = threading.Lock()

    def reserve(self, mission_id):
        with self.lock:
            # One mission for this journal, including across restarts.
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if self.db.execute("SELECT 1 FROM mission LIMIT 1").fetchone():
                    self.db.execute("ROLLBACK")
                    return False
                self.db.execute("INSERT INTO mission VALUES (?)", (mission_id,))
                self.db.execute("COMMIT")
                return True
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def attempt(self, command_id):
        with self.lock:
            try:
                self.db.execute("INSERT INTO command VALUES (?, 'UNKNOWN')", (command_id,))
                return True
            except sqlite3.IntegrityError:
                return False

    def finish(self, command_id, outcome):
        with self.lock:
            self.db.execute("UPDATE command SET outcome=? WHERE id=?", (outcome, command_id))

    def close(self):
        self.db.close()


class PhysicalRecoveryAdapter:
    mode = "physical"

    def __init__(self, runtime, *, authority, observations, planner, journal_path,
                 now=time.time, monotonic=time.monotonic, sleep=time.sleep):
        # authority.reserve(id) must durably reserve through P3.
        # authority.allows(command) must check current operator/mission authority.
        # observations(mission) returns a real integration.recovery Observation.
        # planner(command, observation) returns ChassisPlan from calibrated inputs.
        self.runtime, self.authority = runtime, authority
        self.observations, self.planner = observations, planner
        self.journal = Journal(journal_path)
        self.now, self.monotonic, self.sleep = now, monotonic, sleep
        self.mission = None
        self._lock = threading.RLock()
        self._last_frame = None
        self._camera = None
        self._uncertain = False

    def begin(self, mission_id):
        with self._lock:
            if self.mission is not None or not self.journal.reserve(mission_id):
                return False
            # Reserve locally first. Lost backend ACK cannot enable a replay.
            if self.authority.reserve(mission_id) is not True:
                return False
            self.mission = mission_id
            return True

    def _ready(self):
        status = self.runtime.controller.status()
        if (status["stage"] != "IDLE" or not status["armed"]
                or not status["telemetryFresh"] or not status["heartbeatFresh"]
                or not status["watchdogAlive"] or status["watchdogError"]
                or status["releasePending"] or self._uncertain):
            raise ValueError("physical_safety_not_ready")

    def _validate_observation(self, obs, target):
        self._ready()
        now = self.now()
        if any(not isinstance(t, (int, float)) or isinstance(t, bool)
               or not math.isfinite(t) or not 0 <= now - t <= 0.5
               for t in (obs.captured_at, obs.telemetry_at)):
            raise ValueError("stale_physical_evidence")
        if (not obs.identity_confirmed or not obs.identity_evidence_id
                or obs.object_id != target.object_id or obs.target_revision != target.revision
                or obs.living_obstacle or not obs.stationary or obs.safety != "READY"):
            raise ValueError("physical_identity_or_safety_unconfirmed")
        camera = (obs.camera_id, obs.session_id)
        if self._camera is not None and self._camera != camera:
            raise ValueError("physical_camera_session_changed")
        if type(obs.frame_id) is not int or obs.frame_id < 1:
            raise ValueError("invalid_frame")
        if self._last_frame is not None and obs.frame_id <= self._last_frame:
            raise ValueError("replayed_physical_frame")
        self._camera, self._last_frame = camera, obs.frame_id

    def observe(self, mission):
        with self._lock:
            if mission.mission_id != self.mission:
                raise ValueError("mission_not_reserved")
            obs = self.observations(mission)
            self._validate_observation(obs, mission.target)
            return obs

    def execute(self, command):
        with self._lock:
            cid = command.command_id
            if not self.journal.attempt(cid):
                # Even a prior successful ACK must never cause another output.
                return Receipt(cid, "UNKNOWN")
            attempted = False
            lease = None
            try:
                self._ready()
                if (command.mission_id != self.mission or command.action not in ("approach", "align")
                        or self.authority.allows(command) is not True):
                    raise ValueError("physical_action_not_authorized_or_implemented")
                if (type(command.max_duration_ms) is not int
                        or not 1 <= command.max_duration_ms <= 300
                        or not math.isfinite(command.expires_at)
                        or not 0 < command.expires_at - self.now() <= 0.3):
                    raise ValueError("invalid_physical_deadline")
                # Recheck independent evidence after model latency, never trust
                # a Grok flag or reuse the observation that prompted its call.
                target = type("TargetBinding", (), {
                    "object_id": command.object_id, "revision": command.target_revision})()
                obs = self.observations(command)
                self._validate_observation(obs, target)
                if obs.frame_id < command.frame_id:
                    raise ValueError("evidence_predates_command")
                if command.action == "align" and not obs.near_target:
                    raise ValueError("alignment_pose_not_ready")
                plan = self.planner(command, obs)
                if (not isinstance(plan, ChassisPlan) or plan.calibrated is not True
                        or plan.command_id != cid or plan.object_id != command.object_id
                        or plan.target_revision != command.target_revision
                        or plan.frame_id != obs.frame_id
                        or plan.identity_evidence_id != obs.identity_evidence_id):
                    raise ValueError("calibrated_bound_plan_required")
                values = (plan.x_mps, plan.y_mps, plan.yaw_deg_s, plan.duration_s)
                if (not all(type(v) in (int, float) and math.isfinite(v) for v in values)
                        or abs(plan.x_mps) > 0.05 or abs(plan.y_mps) > 0.05
                        or abs(plan.yaw_deg_s) > 5
                        or not 0 < plan.duration_s <= min(0.15, command.max_duration_ms / 1000)
                        or self.now() + plan.duration_s > command.expires_at):
                    raise ValueError("physical_test_plan_limit")
                # Shared bridge arbitration: never bypass manual lease ownership.
                code, lease = self.runtime.bridge.manual_lease({})
                if code != 200:
                    raise ValueError("bridge_lease_denied")
                issued = self.now()
                attempted = True
                code, _ = self.runtime.bridge.manual_drive({
                    "leaseId": lease["leaseId"], "generation": lease["generation"],
                    "sequence": 1, "issuedAt": issued,
                    "expiresAt": min(command.expires_at, issued + plan.duration_s),
                    "xMps": plan.x_mps, "yMps": plan.y_mps, "zDegps": plan.yaw_deg_s,
                })
                if code != 200:
                    raise RuntimeError("physical_output_unconfirmed")
                # Release strictly before expiry; watchdog/deadline remains an
                # independent backup if this thread stalls. No keepalive loop.
                self.sleep(max(0, plan.duration_s * 0.8))
                self.runtime.bridge.manual_release()
                lease = None
                deadline = self.monotonic() + 2
                while self.monotonic() < deadline:
                    status = self.runtime.controller.status()
                    if status["stage"] == "IDLE" and not status["releasePending"]:
                        self.journal.finish(cid, "ACKNOWLEDGED")
                        return Receipt(cid, "ACKNOWLEDGED")
                    if status["stage"] == "STOPPED":
                        raise RuntimeError("physical_action_fault")
                    self.sleep(0.01)
                raise RuntimeError("physical_stop_unconfirmed")
            except Exception:
                outcome = "UNKNOWN" if attempted else "REJECTED"
                self._uncertain = self._uncertain or attempted
                # Stop even on failed validation; never silently leave an armed
                # adapter after a malformed or stale physical proposal.
                try:
                    self.runtime.controller.stop("RECOVERY_ADAPTER_HALT")
                except Exception:
                    self._uncertain = True
                    outcome = "UNKNOWN"
                self.journal.finish(cid, outcome)
                return Receipt(cid, outcome)
            finally:
                if lease is not None:
                    self.runtime.bridge.manual_release()

    def stop(self, mission_id):
        with self._lock:
            if mission_id != self.mission:
                return "UNKNOWN"
            self.runtime.controller.stop("RECOVERY_STOP")
            deadline = self.monotonic() + 2
            while self.monotonic() < deadline:
                if self.runtime.controller.status()["stopConfirmed"]:
                    return "STOPPED"
                self.sleep(0.01)
            return "UNKNOWN"

    def close(self):
        # Does not release the durable mission or start/close a foreign SDK.
        self.journal.close()
