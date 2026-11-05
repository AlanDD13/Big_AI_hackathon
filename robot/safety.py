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
    def __init__(self, code, http_status=409, http_code=None):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.http_code = http_code or code


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
    write_timeout_s: float = 0.10
    stop_confirmation_s: float = 1.5
    stationary_span_s: float = 0.2
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
        if self.write_timeout_s > self.silence_s / 2:
            raise ValueError("Write timeout must stay bounded below the silence gate")
        if self.stationary_span_s < 0.2:
            raise ValueError("Stationary confirmation span may not be shortened below 200 ms")
        if self.max_x > 0.35 or self.max_y > 0.35 or self.max_yaw > 45:
            raise ValueError("Velocity caps may not exceed the initial slice caps")


@dataclass
class _WriteRequest:
    velocity: tuple
    is_zero: bool
    generation: int
    command_id: str | None
    requested_at: float
    done: threading.Event
    error: str | None = None
    cancelled: bool = False
    completed_at: float | None = None


class SafetyController:
    """Serializes intent, stop and rearm; uses local monotonic expiry internally.

    Output writes execute on a bounded background worker outside `_lock` so a
    stalled SDK transport cannot block `stop()`, `tick()`, or `status()`.
    """

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
        self._last_sample_id = None
        self._stop_sent_at = self._last_zero_at = None
        self._quiet_since = None
        self._quiet_count = 0
        self._velocity = (0.0, 0.0, 0.0)
        self._transport_error = None
        self._write_stuck = False
        self._stop_unconfirmed = False
        self._release_pending = False
        self._stop_requested_at = self._clock()
        self._revision = 1
        self._watchdog_alive = False
        self._watchdog_error = None

        self._worker_wake = threading.Event()
        self._pending_request = None
        self._in_flight_request = None
        self._in_flight_deadline = None
        self._worker = threading.Thread(
            target=self._write_worker_loop,
            name="rover-safety-output",
            daemon=True,
        )
        self._worker.start()

    def _fresh(self, timestamp, age):
        return timestamp is not None and 0 <= self._clock() - timestamp < age

    def _set_stage(self, stage):
        if stage != self._stage:
            self._stage = stage
            self._revision += 1

    def _write_worker_loop(self):
        while True:
            self._worker_wake.wait()
            with self._lock:
                req = self._pending_request
                self._pending_request = None
                if req is None:
                    self._worker_wake.clear()
                    continue
                if not req.is_zero:
                    if (not self._armed or self._stage == "UNKNOWN"
                            or self._generation != req.generation
                            or self._active != req.command_id):
                        req.cancelled = True
                        req.done.set()
                        continue
                self._in_flight_request = req
                self._in_flight_deadline = time.monotonic() + self.limits.write_timeout_s

            err_name = None
            try:
                self._write(*req.velocity)
            except Exception as exc:
                err_name = type(exc).__name__

            with self._lock:
                completed_clock = self._clock()
                req.completed_at = completed_clock
                req.error = err_name
                if self._in_flight_request is req:
                    self._in_flight_request = None
                    self._in_flight_deadline = None
                if err_name is not None:
                    self._transport_error = err_name
                    self._write_stuck = False
                    self._stop_sent_at = None
                    self._quiet_count, self._quiet_since = 0, None
                    self._release_pending = False
                    self._set_stage("UNKNOWN")
                elif req.is_zero:
                    # Only clear stuck/error state once a zero write actually finishes
                    # with no newer pending failure.
                    self._write_stuck = False
                    self._transport_error = None
                    self._last_zero_at = completed_clock
                    if self._stop_sent_at is None:
                        self._stop_sent_at = req.requested_at
                else:
                    self._write_stuck = False
                    if not self._armed or self._generation != req.generation:
                        # A stop or fault occurred while the non-zero write was in flight.
                        # Enqueue an immediate zero write to supersede the physical command.
                        if self._pending_request is None or not self._pending_request.is_zero:
                            self._pending_request = _WriteRequest(
                                velocity=(0.0, 0.0, 0.0),
                                is_zero=True,
                                generation=self._generation,
                                command_id=None,
                                requested_at=completed_clock,
                                done=threading.Event(),
                            )
                            self._worker_wake.set()
                req.done.set()

    def _submit_write(self, velocity, is_zero, generation, command_id=None):
        with self._lock:
            now = self._clock()
            req = _WriteRequest(
                velocity=velocity,
                is_zero=is_zero,
                generation=generation,
                command_id=command_id,
                requested_at=now,
                done=threading.Event(),
            )
            if is_zero:
                self._last_zero_at = now
                if self._pending_request is not None and not self._pending_request.is_zero:
                    self._pending_request.cancelled = True
                    self._pending_request.done.set()
                self._pending_request = req
                self._worker_wake.set()
                if self._write_stuck and self._in_flight_request is not None:
                    # Worker is already known stuck in an earlier call; do not block stop/tick.
                    self._transport_error = "WRITE_TIMEOUT"
                    self._stop_sent_at = None
                    self._quiet_count, self._quiet_since = 0, None
                    self._release_pending = False
                    self._set_stage("UNKNOWN")
                    return req, True
            else:
                if self._write_stuck and self._in_flight_request is not None:
                    self._transport_error = "WRITE_TIMEOUT"
                    return req, True
                if self._pending_request is not None and self._pending_request.is_zero:
                    req.cancelled = True
                    req.done.set()
                    return req, False
                self._pending_request = req
                self._worker_wake.set()

            wait_s = self.limits.write_timeout_s
            if self._in_flight_deadline is not None:
                remaining = self._in_flight_deadline - time.monotonic()
                wait_s = max(0.0, min(wait_s, remaining))

        finished = req.done.wait(timeout=wait_s)
        if not finished:
            with self._lock:
                if not req.done.is_set():
                    self._write_stuck = True
                    self._transport_error = "WRITE_TIMEOUT"
                    self._stop_sent_at = None
                    self._quiet_count, self._quiet_since = 0, None
                    self._release_pending = False
                    self._set_stage("UNKNOWN")
                    return req, True
        return req, False

    def heartbeat(self):
        with self._lock:
            self._heartbeat_at = self._clock()

    def observe(self, vx, vy, yaw_rate, sample_id=None):
        """Observe fresh telemetry; request zero OUTSIDE the controller lock.

        Confirmation still needs three fresh quiet samples spanning >=200ms.
        Fault authority is latched atomically before releasing the lock.
        """
        with self._lock:
            fault = self._observe_sample_locked(vx, vy, yaw_rate, sample_id)
            if fault:
                self._latch_fault_locked(fault)
        if fault:
            self._send_zero()

    def _observe_sample_locked(self, vx, vy, yaw_rate, sample_id):
        if not all(finite(v) for v in (vx, vy, yaw_rate)):
            self._telemetry_at = None
            return "INVALID_TELEMETRY"
        if sample_id is not None and (type(sample_id) is not int or sample_id < 0):
            self._telemetry_at = None
            return "INVALID_TELEMETRY"
        now = self._clock()
        previous_at = self._telemetry_at
        self._telemetry_at = now
        quiet = abs(vx) <= 0.025 and abs(vy) <= 0.025 and abs(yaw_rate) <= 3
        if not quiet:
            self._quiet_count, self._quiet_since = 0, None
            if (self._stage == "STOPPED"
                    or (self._stage == "IDLE" and self._active is None)
                    or (self._release_pending
                        and now - self._stop_requested_at >= self.limits.stop_confirmation_s)):
                return "UNEXPECTED_MOTION"
            # Clean release stays UNKNOWN/disarmed during physical braking.
            # Motion resets quiet evidence; tick still repeats zero. Motion
            # past the existing confirmation bound faults and cannot later
            # auto-return to IDLE. Stationary thresholds are unchanged.
            return
        # Duplicate quiet observations cannot manufacture stop evidence;
        # unexpected motion above is still processed immediately.
        if sample_id is not None:
            if self._last_sample_id is not None and sample_id <= self._last_sample_id:
                return
            self._last_sample_id = sample_id
            if self._last_observation_at is not None and now < self._last_observation_at:
                return
        else:
            if self._last_observation_at is not None and now <= self._last_observation_at:
                return
        self._last_observation_at = now
        if previous_at is not None and now - previous_at >= self.limits.telemetry_s:
            self._quiet_count, self._quiet_since = 0, None
        if (self._stop_sent_at is not None
                and now > self._stop_sent_at
                and not self._write_stuck
                and self._transport_error is None):
            self._quiet_since = now if self._quiet_since is None else self._quiet_since
            self._quiet_count += 1
            if self._quiet_count >= 3 and now - self._quiet_since >= self.limits.stationary_span_s:
                if self._stage == "UNKNOWN":
                    if self._release_pending and self._fresh(self._heartbeat_at, self.limits.heartbeat_s):
                        self._release_pending = False
                        self._generation += 1
                        self._armed = True
                        self._active = None
                        self._sequence = 0
                        self._reason = None
                        self._stop_unconfirmed = False
                        self._set_stage("IDLE")
                    else:
                        self._release_pending = False
                        self._set_stage("STOPPED")
                        self._stop_unconfirmed = False

    def _send_zero(self):
        with self._lock:
            gen = self._generation
        self._submit_write((0.0, 0.0, 0.0), is_zero=True, generation=gen)

    def _latch_fault_locked(self, reason):
        already_latched = (not self._armed and self._stage in ("UNKNOWN", "STOPPED")
                           and not self._release_pending
                           and reason not in ("UNEXPECTED_MOTION", "INVALID_TELEMETRY"))
        if self._active is not None:
            self._retired.add(self._active)
        self._active = None
        self._armed = False
        self._release_pending = False
        self._velocity = (0.0, 0.0, 0.0)
        if not already_latched or self._stop_sent_at is None:
            self._reason = reason
            self._stop_sent_at = None
            self._quiet_count, self._quiet_since = 0, None
            self._stop_unconfirmed = False
            self._stop_requested_at = self._clock()
            self._set_stage("UNKNOWN")

    def _trip(self, reason):
        with self._lock:
            self._latch_fault_locked(reason)
        self._send_zero()

    def _force_fault_state(self, reason, error_detail=None):
        """Last-resort fail-closed latch if `stop()` itself raises in a watchdog hook."""
        with self._lock:
            self._latch_fault_locked(reason)
            if error_detail:
                self._watchdog_error = error_detail
                if self._transport_error is None:
                    self._transport_error = "WATCHDOG_FAILURE"

    def _set_watchdog_health(self, alive, error=None):
        with self._lock:
            self._watchdog_alive = bool(alive)
            self._watchdog_error = error

    def stop(self, reason="OPERATOR_STOP"):
        self._trip(reason)
        return self.status()

    def release(self, reason="user_release"):
        """Voluntary manual release (`contracts/manual-control-v1.md`).

        Immediately commands zero velocity and enters `UNKNOWN`. Unlike a fault
        or operator stop, 3 subsequent fresh stationary observations transition
        `UNKNOWN -> IDLE` without requiring `rearm()`. If confirmation times out,
        stage stays `UNKNOWN` rather than claiming `STOPPED`.
        """
        with self._lock:
            if self._active is not None:
                self._retired.add(self._active)
            self._active = None
            state_can_release = (
                self._stage in ("IDLE", "DRIVING")
                or (self._stage == "UNKNOWN" and self._release_pending)
            )
            if not state_can_release or self._transport_error is not None or self._write_stuck:
                # Cannot cleanly release from a latched fault; remain fail-closed.
                self._latch_fault_locked(self._reason or reason)
            else:
                self._armed = False
                self._release_pending = True
                self._velocity = (0.0, 0.0, 0.0)
                self._reason = reason
                self._stop_sent_at = None
                self._quiet_count, self._quiet_since = 0, None
                self._stop_unconfirmed = False
                self._stop_requested_at = self._clock()
                self._set_stage("UNKNOWN")
        self._send_zero()
        return self.status()

    def _fault(self):
        if self._write_stuck:
            return "TRANSPORT_ERROR"
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
        need_zero = False
        fault = None
        with self._lock:
            if self._armed:
                fault = self._fault()
                if fault:
                    self._latch_fault_locked(fault)
                    need_zero = True
            elif self._stage == "STOPPED" and not self._fresh(self._telemetry_at, self.limits.telemetry_s):
                self._set_stage("UNKNOWN")
                self._reason = "TELEMETRY_STALE"
                self._stop_sent_at = None
                self._stop_requested_at = self._clock()
                self._quiet_count, self._quiet_since = 0, None
            if self._stage == "UNKNOWN":
                now = self._clock()
                if self._last_zero_at is None or now - self._last_zero_at >= 0.1:
                    need_zero = True
                if now - self._stop_requested_at >= self.limits.stop_confirmation_s:
                    self._stop_unconfirmed = True
        if need_zero:
            self._send_zero()
        return self.status()

    def rearm(self):
        self.tick()
        with self._lock:
            if self._stage != "STOPPED" or self._transport_error is not None or self._write_stuck:
                raise SafetyError("STOP_NOT_CONFIRMED", http_status=409, http_code="NOT_STOPPED")
            if not self._fresh(self._telemetry_at, self.limits.telemetry_s):
                raise SafetyError("TELEMETRY_STALE", http_status=409, http_code="NOT_STOPPED")
            if not self._fresh(self._heartbeat_at, self.limits.heartbeat_s):
                raise SafetyError("HEARTBEAT_LOST", http_status=409, http_code="NOT_STOPPED")
            self._release_pending = False
            self._generation += 1
            self._armed = True
            self._active = None
            self._sequence = 0
            self._reason = None
            self._set_stage("IDLE")
            return self._generation

    def drive(self, command_id, generation, sequence, issued_at, expires_at, x, y, yaw, boot_id=None):
        """Validate and issue a bounded velocity command.

        Fail-closed policy: any rejected drive reaching this controller during an
        active session immediately revokes authority and commands zero velocity.
        """
        self.tick()
        with self._lock:
            if not self._armed:
                if self._stage == "UNKNOWN" or not self._fresh(self._telemetry_at, self.limits.telemetry_s):
                    raise SafetyError("STOP_LOCKED", http_status=503, http_code="TELEMETRY_STALE")
                raise SafetyError("STOP_LOCKED", http_status=409, http_code="STOP_LOCKED")
            wall_now, mono_now = self._wall(), self._clock()
            error = None
            http_status, http_code = 409, None
            if not isinstance(command_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", command_id):
                error, http_status, http_code = "INVALID_COMMAND_ID", 422, "INVALID_COMMAND_ID"
            elif boot_id != self.boot_id:
                error, http_status, http_code = "STALE_BOOT", 409, "STALE_INTENT"
            elif type(generation) is not int or generation != self._generation:
                error, http_status, http_code = "STALE_GENERATION", 409, "STALE_INTENT"
            elif command_id in self._retired or (self._active is not None and command_id != self._active):
                error, http_status, http_code = "AUTHORITY_CONFLICT", 409, "LEASE_HELD"
            elif type(sequence) is not int or sequence <= self._sequence:
                error, http_status, http_code = "STALE_SEQUENCE", 409, "STALE_INTENT"
            elif not all(finite(v) for v in (issued_at, expires_at, x, y, yaw)):
                error, http_status, http_code = "INVALID_NUMBER", 422, "INVALID_NUMBER"
            elif not (0 < expires_at - issued_at <= self.limits.max_intent_s) or issued_at > wall_now or expires_at <= wall_now:
                error, http_status, http_code = "EXPIRED_OR_INVALID_DEADLINE", 409, "LEASE_EXPIRED"
            elif abs(x) > self.limits.max_x or abs(y) > self.limits.max_y or abs(yaw) > self.limits.max_yaw:
                error, http_status, http_code = "VELOCITY_LIMIT", 422, "VELOCITY_LIMIT"
            elif len(self._retired) >= 1024:
                error, http_status, http_code = "RESTART_REQUIRED", 503, "RESTART_REQUIRED"

            if error:
                self._latch_fault_locked(error)
            else:
                self._active = command_id
                self._sequence = sequence
                self._intent_at = mono_now
                self._deadline = mono_now + (expires_at - wall_now)
                self._velocity = (x, y, yaw)
                target_gen = self._generation

        if error:
            self._send_zero()
            raise SafetyError(error, http_status=http_status, http_code=http_code)

        req, timed_out = self._submit_write(
            (x, y, yaw),
            is_zero=False,
            generation=target_gen,
            command_id=command_id,
        )
        if timed_out or req.error is not None:
            with self._lock:
                self._latch_fault_locked("TRANSPORT_ERROR")
                if timed_out:
                    self._write_stuck = True
                    self._transport_error = "WRITE_TIMEOUT"
            self._send_zero()
            raise SafetyError("TRANSPORT_ERROR", http_status=503, http_code="TELEMETRY_STALE")

        with self._lock:
            if req.cancelled or not self._armed or self._generation != target_gen or self._active != command_id:
                http_s = 503 if self._stage == "UNKNOWN" else 409
                http_c = "TELEMETRY_STALE" if self._stage == "UNKNOWN" else "STOP_LOCKED"
                raise SafetyError("STOP_LOCKED", http_status=http_s, http_code=http_c)
            self._set_stage("DRIVING" if any(self._velocity) else "IDLE")
            return self.status()

    def manual_http_error(self, error):
        """Translate a `SafetyError` into `(http_status, wire_error_code)` per `manual-control-v1.md`."""
        if isinstance(error, SafetyError):
            return error.http_status, error.http_code
        return 500, "INTERNAL_ERROR"

    def status(self):
        with self._lock:
            return {
                "bridgeBootId": self.boot_id,
                "statusRevision": self._revision,
                "stage": self._stage,
                "reason": self._reason,
                "armed": self._armed,
                "generation": self._generation,
                "commandId": self._active,
                "lastSequence": self._sequence,
                "lastSampleId": self._last_sample_id,
                "requestedVelocity": self._velocity,
                "telemetryFresh": self._fresh(self._telemetry_at, self.limits.telemetry_s),
                "heartbeatFresh": self._fresh(self._heartbeat_at, self.limits.heartbeat_s),
                "stationarySamples": self._quiet_count,
                "stopConfirmed": self._stage == "STOPPED",
                "stopUnconfirmed": self._stop_unconfirmed,
                "releasePending": self._release_pending,
                "stopRequestedMonotonic": self._stop_requested_at,
                "transportError": self._transport_error,
                "writeStuck": self._write_stuck,
                "watchdogAlive": self._watchdog_alive,
                "watchdogError": self._watchdog_error,
            }


class Watchdog:
    """Separate host thread: does not depend on incoming request execution."""

    def __init__(self, controller):
        self.controller = controller
        self._done = threading.Event()
        self.last_error = None
        self._thread = threading.Thread(target=self._run, name="rover-watchdog", daemon=True)

    @property
    def alive(self):
        return self._thread.is_alive()

    @property
    def healthy(self):
        return self._thread.is_alive() and self.last_error is None

    def _run(self):
        self.controller._set_watchdog_health(True, None)
        try:
            while not self._done.is_set():
                try:
                    self.controller.tick()
                    if self.last_error is not None:
                        self.last_error = None
                        self.controller._set_watchdog_health(True, None)
                except Exception as exc:
                    err = f"{type(exc).__name__}: {exc}"
                    self.last_error = err
                    self.controller._set_watchdog_health(True, err)
                    try:
                        self.controller.stop("WATCHDOG_EXCEPTION")
                    except Exception as stop_exc:
                        fallback_err = f"{err}; stop failed with {type(stop_exc).__name__}: {stop_exc}"
                        self.last_error = fallback_err
                        self.controller._force_fault_state("WATCHDOG_EXCEPTION", fallback_err)
                self._done.wait(self.controller.limits.poll_s)
        finally:
            self.controller._set_watchdog_health(False, self.last_error)
            if not self._done.is_set():
                self.controller._force_fault_state("WATCHDOG_DIED", self.last_error or "Watchdog exited unexpectedly")

    def start(self):
        self._thread.start()

    def close(self):
        self._done.set()
        stop_error = None
        try:
            self.controller.stop("SHUTDOWN")
        except Exception as exc:
            stop_error = exc
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.controller._force_fault_state("SHUTDOWN", self.last_error)
        finally:
            if self._thread.ident is not None:
                self._thread.join(timeout=1)
        if self._thread.is_alive():
            self.controller._force_fault_state("SHUTDOWN", "Watchdog did not exit")
            raise RuntimeError("Watchdog did not exit; stop is unconfirmed")
        if stop_error is not None:
            raise RuntimeError(f"Watchdog shutdown stop failed: {stop_error}") from stop_error
