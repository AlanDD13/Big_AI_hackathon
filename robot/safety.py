"""Transport-independent chassis safety core. No network API or DJI session.

The output callback must perform a bounded velocity write. Its return is NOT a
physical acknowledgement. Confirmation comes only from subsequent telemetry.
This host process cannot guarantee a stop after host death or network loss.
"""

from dataclasses import dataclass
import math
import re
import threading
import time
import uuid


class SafetyError(RuntimeError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def finite(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class Limits:
    # Guard at 250 ms, leaving 50 ms scheduling headroom for the 300 ms target.
    # Desktop scheduling is not hard realtime; acceptance records actual times.
    silence_s: float = 0.25
    max_intent_s: float = 0.5
    telemetry_s: float = 0.5
    heartbeat_s: float = 1.0
    poll_s: float = 0.02
    stop_confirmation_s: float = 1.5
    max_x: float = 0.35
    max_y: float = 0.35
    max_yaw: float = 45.0

    def __post_init__(self):
        if not all(finite(v) and v > 0 for v in vars(self).values()):
            raise ValueError("Limits must be finite and positive")
        if self.silence_s > 0.3 or self.max_intent_s > 0.5 or self.telemetry_s > 0.5:
            raise ValueError("Limits may not weaken the initial safety gates")
        if self.poll_s > self.silence_s / 2:
            raise ValueError("Watchdog polling must be faster than the silence gate")
        if self.max_x > 0.35 or self.max_y > 0.35 or self.max_yaw > 45:
            raise ValueError("Velocity caps may not exceed the initial slice caps")


class SafetyController:
    """Serializes intent, stop and rearm; uses local monotonic expiry internally."""

    def __init__(self, write_velocity, limits=None, clock=time.monotonic, wall=time.time):
        self.limits = limits or Limits()
        self._write = write_velocity
        self._clock, self._wall = clock, wall
        self._lock = threading.RLock()
        self.boot_id = uuid.uuid4().hex
        self._stage, self._reason = "UNKNOWN", "STARTUP"
        self._armed = False
        self._generation = 0
        self._active = None
        self._retired = set()
        self._sequence = 0
        self._intent_at = self._deadline = None
        self._telemetry_at = self._heartbeat_at = None
        self._last_observation_at = None
        self._stop_sent_at = self._last_zero_at = None
        self._quiet_since = None
        self._quiet_count = 0
        self._velocity = (0.0, 0.0, 0.0)
        self._transport_error = None
        self._stop_unconfirmed = False
        self._stop_requested_at = self._clock()
        self._revision = 1

    def _fresh(self, timestamp, age):
        return timestamp is not None and 0 <= self._clock() - timestamp < age

    def _set_stage(self, stage):
        if stage != self._stage:
            self._stage = stage
            self._revision += 1

    def heartbeat(self):
        with self._lock:
            self._heartbeat_at = self._clock()

    def observe(self, vx, vy, yaw_rate):
        """Call only on a NEW coherent, locally fresh pose/velocity/attitude sample."""
        with self._lock:
            if not all(finite(v) for v in (vx, vy, yaw_rate)):
                self._telemetry_at = None
                self._trip("INVALID_TELEMETRY")
                return
            now = self._clock()
            previous_at = self._telemetry_at
            self._telemetry_at = now
            quiet = abs(vx) <= 0.025 and abs(vy) <= 0.025 and abs(yaw_rate) <= 3
            if not quiet:
                self._quiet_count, self._quiet_since = 0, None
                if self._stage == "STOPPED" or (self._stage == "IDLE" and self._active is None):
                    self._trip("UNEXPECTED_MOTION")
                return
            # Duplicate quiet observations cannot manufacture stop evidence;
            # unexpected motion above is still processed immediately.
            if now == self._last_observation_at:
                return
            self._last_observation_at = now
            if previous_at is not None and now - previous_at >= self.limits.telemetry_s:
                self._quiet_count, self._quiet_since = 0, None
            if self._stop_sent_at is not None and now > self._stop_sent_at:
                self._quiet_since = now if self._quiet_since is None else self._quiet_since
                self._quiet_count += 1
                if self._quiet_count >= 3 and now - self._quiet_since >= 0.2:
                    if self._stage == "UNKNOWN" and self._transport_error is None:
                        self._set_stage("STOPPED")
                        self._stop_unconfirmed = False

    def _send_zero(self):
        now = self._clock()
        self._last_zero_at = now
        try:
            self._write(0.0, 0.0, 0.0)
            self._transport_error = None
            if self._stop_sent_at is None:
                self._stop_sent_at = now
        except Exception as exc:
            self._transport_error = type(exc).__name__
            self._stop_sent_at = None
            self._quiet_count, self._quiet_since = 0, None
            self._set_stage("UNKNOWN")

    def _trip(self, reason):
        already_latched = (not self._armed and self._stage in ("UNKNOWN", "STOPPED")
                           and reason not in ("UNEXPECTED_MOTION", "INVALID_TELEMETRY"))
        if self._active is not None:
            self._retired.add(self._active)
        self._active = None
        self._armed = False
        self._velocity = (0.0, 0.0, 0.0)
        if not already_latched or self._stop_sent_at is None:
            self._reason = reason
            self._stop_sent_at = None
            self._quiet_count, self._quiet_since = 0, None
            self._stop_unconfirmed = False
            self._stop_requested_at = self._clock()
            self._set_stage("UNKNOWN")
        self._send_zero()

    def stop(self, reason="OPERATOR_STOP"):
        with self._lock:
            self._trip(reason)
            return self.status()

    def _fault(self):
        if not self._fresh(self._telemetry_at, self.limits.telemetry_s):
            return "TELEMETRY_STALE"
        if not self._fresh(self._heartbeat_at, self.limits.heartbeat_s):
            return "HEARTBEAT_LOST"
        if self._active is not None:
            now = self._clock()
            if now >= self._deadline:
                return "COMMAND_EXPIRED"
            if now - self._intent_at >= self.limits.silence_s:
                return "INPUT_SILENCE"
        return None

    def tick(self):
        with self._lock:
            if self._armed:
                fault = self._fault()
                if fault:
                    self._trip(fault)
            elif self._stage == "STOPPED" and not self._fresh(self._telemetry_at, self.limits.telemetry_s):
                self._set_stage("UNKNOWN")
                self._reason = "TELEMETRY_STALE"
                self._stop_sent_at = None
                self._stop_requested_at = self._clock()
                self._quiet_count, self._quiet_since = 0, None
            if self._stage == "UNKNOWN":
                now = self._clock()
                if self._last_zero_at is None or now - self._last_zero_at >= 0.1:
                    self._send_zero()
                if now - self._stop_requested_at >= self.limits.stop_confirmation_s:
                    self._stop_unconfirmed = True
            return self.status()

    def rearm(self):
        with self._lock:
            self.tick()
            if self._stage != "STOPPED" or self._transport_error is not None:
                raise SafetyError("STOP_NOT_CONFIRMED")
            if not self._fresh(self._telemetry_at, self.limits.telemetry_s):
                raise SafetyError("TELEMETRY_STALE")
            if not self._fresh(self._heartbeat_at, self.limits.heartbeat_s):
                raise SafetyError("HEARTBEAT_LOST")
            self._generation += 1
            self._armed = True
            self._active = None
            self._sequence = 0
            self._reason = None
            self._set_stage("IDLE")
            return self._generation

    def drive(self, command_id, generation, sequence, issued_at, expires_at, x, y, yaw, boot_id=None):
        with self._lock:
            self.tick()
            if not self._armed:
                raise SafetyError("STOP_LOCKED")
            wall_now, mono_now = self._wall(), self._clock()
            error = None
            if not isinstance(command_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", command_id):
                error = "INVALID_COMMAND_ID"
            elif boot_id != self.boot_id:
                error = "STALE_BOOT"
            elif type(generation) is not int or generation != self._generation:
                error = "STALE_GENERATION"
            elif command_id in self._retired or (self._active is not None and command_id != self._active):
                error = "AUTHORITY_CONFLICT"
            elif type(sequence) is not int or sequence <= self._sequence:
                error = "STALE_SEQUENCE"
            elif not all(finite(v) for v in (issued_at, expires_at, x, y, yaw)):
                error = "INVALID_NUMBER"
            elif not (0 < expires_at - issued_at <= self.limits.max_intent_s) or issued_at > wall_now or expires_at <= wall_now:
                error = "EXPIRED_OR_INVALID_DEADLINE"
            elif abs(x) > self.limits.max_x or abs(y) > self.limits.max_y or abs(yaw) > self.limits.max_yaw:
                error = "VELOCITY_LIMIT"
            elif len(self._retired) >= 1024:
                error = "RESTART_REQUIRED"
            if error:
                self._trip(error)
                raise SafetyError(error)
            self._active = command_id
            self._sequence = sequence
            self._intent_at = mono_now
            self._deadline = mono_now + (expires_at - wall_now)
            self._velocity = (x, y, yaw)
            try:
                self._write(x, y, yaw)
            except Exception:
                self._trip("TRANSPORT_ERROR")
                raise SafetyError("TRANSPORT_ERROR")
            self._set_stage("DRIVING" if any(self._velocity) else "IDLE")
            return self.status()

    def status(self):
        with self._lock:
            return {"bridgeBootId": self.boot_id, "statusRevision": self._revision,
                    "stage": self._stage, "reason": self._reason, "armed": self._armed,
                    "generation": self._generation, "commandId": self._active,
                    "lastSequence": self._sequence, "requestedVelocity": self._velocity,
                    "telemetryFresh": self._fresh(self._telemetry_at, self.limits.telemetry_s),
                    "heartbeatFresh": self._fresh(self._heartbeat_at, self.limits.heartbeat_s),
                    "stopConfirmed": self._stage == "STOPPED",
                    "stopUnconfirmed": self._stop_unconfirmed,
                    "stopRequestedMonotonic": self._stop_requested_at,
                    "transportError": self._transport_error}


class Watchdog:
    """Separate host thread: does not depend on incoming request execution."""

    def __init__(self, controller):
        self.controller = controller
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name="rover-watchdog", daemon=True)

    def _run(self):
        while not self._done.is_set():
            try:
                self.controller.tick()
            except Exception:
                self.controller.stop("WATCHDOG_EXCEPTION")
            self._done.wait(self.controller.limits.poll_s)

    def start(self):
        self._thread.start()

    def close(self):
        self.controller.stop("SHUTDOWN")
        self._done.set()
        self._thread.join(timeout=1)
        if self._thread.is_alive():
            raise RuntimeError("Watchdog did not exit; stop is unconfirmed")
