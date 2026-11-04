"""Real SDK watchdog acceptance. Defaults to ZERO velocity; motion is opt-in.

No Wi-Fi disconnect or process crash is performed. Output reports are local.
"""

import argparse
import json
import logging
import math
from pathlib import Path
import subprocess
import sys
import threading
import time

from check_connection import rover_session
from safety import Limits, SafetyController, SafetyError, Watchdog
from telemetry import YawRate


class DJIOutput:
    def __init__(self, rover, max_speed=0.05):
        self.rover, self.max_speed = rover, max_speed
        self.events = []

    def __call__(self, x, y, yaw):
        if abs(x) > self.max_speed or abs(y) > self.max_speed or yaw != 0:
            raise ValueError("Bench only permits bounded translation, no rotation")
        sent_at = time.monotonic()
        # Deliberately longer than the 300 ms watchdog for this acceptance
        # test, so the short watchdog stop is not merely the SDK timer firing.
        # Still only a HOST timer; it cannot protect a crashed Mac.
        self.rover.chassis.drive_speed(x=x, y=y, z=yaw, timeout=1.0)
        self.events.append({"at": sent_at, "velocity": [x, y, yaw],
                            "write_duration_s": time.monotonic() - sent_at})


class Telemetry:
    """Only forwards coherent fresh LOCAL chassis telemetry, not P5 map pose."""

    def __init__(self, rover, controller):
        self.rover, self.controller = rover, controller
        self._lock = threading.RLock()
        self.position_at = self.attitude_at = None
        self.yaw = self.yaw_rate = None
        self._yaw_estimator = YawRate()
        self._last_position = self._last_attitude = 0
        self.observations = []
        self.forwarding = True
        self.battery = None
        self.peak_speed = 0.0
        self._cleanup = []

    def position(self, value):
        with self._lock:
            if all(math.isfinite(v) for v in value):
                self.position_at = time.monotonic()

    def attitude(self, value):
        with self._lock:
            now = time.monotonic()
            if not all(math.isfinite(v) for v in value):
                self.yaw_rate = None
                return
            self.yaw_rate = self._yaw_estimator.update(now, value[0])
            self.yaw, self.attitude_at = value[0], now

    def velocity(self, value):
        with self._lock:
            now = time.monotonic()
            if not self.forwarding or self.yaw_rate is None:
                return
            if self.position_at is None or now - self.position_at >= 0.3:
                return
            if self.attitude_at is None or now - self.attitude_at >= 0.3:
                return
            if self.position_at <= self._last_position or self.attitude_at <= self._last_attitude:
                return
            self._last_position, self._last_attitude = self.position_at, self.attitude_at
            self.peak_speed = max(self.peak_speed, abs(value[3]), abs(value[4]))
            self.observations.append([now, value[3], value[4], self.yaw_rate])
            self.observations = self.observations[-200:]
            self.controller.observe(value[3], value[4], self.yaw_rate)

    def battery_info(self, value):
        self.battery = value

    def start(self):
        subscriptions = [
            (self.rover.chassis.sub_position, self.position, self.rover.chassis.unsub_position),
            (self.rover.chassis.sub_attitude, self.attitude, self.rover.chassis.unsub_attitude),
            (self.rover.chassis.sub_velocity, self.velocity, self.rover.chassis.unsub_velocity),
            (self.rover.battery.sub_battery_info, self.battery_info, self.rover.battery.unsub_battery_info),
        ]
        for subscribe, callback, unsubscribe in subscriptions:
            if not subscribe(freq=10, callback=callback):
                raise RuntimeError("Required telemetry subscription rejected")
            self._cleanup.append(unsubscribe)

    def close(self):
        for unsubscribe in reversed(self._cleanup):
            unsubscribe()


def wait_for(controller, predicate, timeout=3, heartbeat=True):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if heartbeat:
            controller.heartbeat()
        status = controller.status()
        if predicate(status):
            return status
        time.sleep(0.02)
    raise RuntimeError(f"Timed out waiting for state: {controller.status()}")


def run(serial, scenario, motion):
    from robomaster import logger
    report = {"scenario": scenario, "motion_authorized": motion, "result": "FAILED"}
    output_dir = Path(__file__).resolve().parent / ".local" / "watchdog"
    output_dir.mkdir(parents=True, exist_ok=True)
    with rover_session(serial) as (rover, identity):
        logger.setLevel(logging.ERROR)
        output = DJIOutput(rover)
        controller = SafetyController(output, limits=Limits())
        telemetry = Telemetry(rover, controller)
        watchdog = Watchdog(controller)
        report["identity"] = {k: identity[k] for k in ("firmware", "connection")}
        try:
            controller.stop("STARTUP")
            telemetry.start()
            watchdog.start()
            wait_for(controller, lambda s: s["stage"] == "STOPPED")
            if telemetry.battery is None or telemetry.battery < 20:
                raise RuntimeError("Battery unavailable or below 20%; bench refused")
            generation = controller.rearm()
            speed = 0.05 if motion else 0.0
            sequence = 1
            def intent(ttl=0.5):
                nonlocal sequence
                now = time.time()
                status = controller.drive("bench", generation, sequence,
                                          now, now + ttl, speed, 0, 0, boot_id=controller.boot_id)
                sequence += 1
                return status
            started = time.monotonic()
            intent(ttl=0.12 if scenario == "deadline" else 0.5)
            expected = {"silence": "INPUT_SILENCE", "deadline": "COMMAND_EXPIRED",
                        "telemetry": "TELEMETRY_STALE", "heartbeat": "HEARTBEAT_LOST",
                        "operator-stop": "OPERATOR_STOP", "stale-intent": "STALE_SEQUENCE"}[scenario]
            if scenario == "operator-stop":
                time.sleep(0.15)
                controller.stop()
            elif scenario == "stale-intent":
                now = time.time()
                try:
                    controller.drive("bench", generation, 1, now, now + 0.5, speed, 0, 0,
                                     boot_id=controller.boot_id)
                    raise RuntimeError("Duplicate sequence was accepted")
                except SafetyError as exc:
                    if exc.code != "STALE_SEQUENCE":
                        raise
            elif scenario in ("telemetry", "heartbeat"):
                if scenario == "telemetry":
                    telemetry.forwarding = False
                until = time.monotonic() + 1.5
                while time.monotonic() < until and controller.status()["armed"]:
                    if scenario != "heartbeat":
                        controller.heartbeat()
                    try:
                        intent()
                    except SafetyError:
                        break
                    time.sleep(0.08)
            latched = wait_for(controller, lambda s: not s["armed"], timeout=1.5,
                               heartbeat=scenario != "heartbeat")
            report["fault_state"] = latched
            if latched["reason"] != expected:
                raise RuntimeError(f"Wrong fault: expected {expected}")
            if scenario == "telemetry":
                # While forwarding is off, do not turn a zero write into a
                # STOPPED claim. Then restore observation, not movement.
                if latched["stage"] != "UNKNOWN":
                    raise RuntimeError("Missing telemetry falsely confirmed stop")
                telemetry.forwarding = True
            stopped = wait_for(controller, lambda s: s["stage"] == "STOPPED", timeout=3)
            report["confirmed_stop"] = stopped
            report["confirmed_after_s"] = time.monotonic() - started
            commands = [e for e in output.events if e["at"] >= started]
            zeros = [e for e in commands if not any(e["velocity"])
                     and e["at"] >= latched["stopRequestedMonotonic"]]
            report["fault_detected_after_s"] = latched["stopRequestedMonotonic"] - started
            report["zero_write_after_s"] = zeros[0]["at"] - started if zeros else None
            if scenario == "silence":
                report["silence_target_300ms_met"] = (report["zero_write_after_s"] is not None
                    and report["zero_write_after_s"] <= 0.3)
                if not report["silence_target_300ms_met"]:
                    raise RuntimeError("Measured zero write exceeded the 300 ms silence target")
            report["max_measured_speed_mps"] = telemetry.peak_speed
            # Recovery is explicit and does not issue a nonzero write.
            count = len(output.events)
            controller.heartbeat()
            new_generation = controller.rearm()
            time.sleep(0.12)
            if any(any(e["velocity"]) for e in output.events[count:]):
                raise RuntimeError("Rearm resumed motion")
            if new_generation <= generation:
                raise RuntimeError("Rearm did not revoke the old generation")
            report["rearm_no_motion"] = True
            report["new_generation"] = new_generation
            controller.stop("BENCH_COMPLETE")
            wait_for(controller, lambda s: s["stage"] == "STOPPED")
            report["final_status"] = controller.status()
            report["result"] = "PASS"
        except Exception as exc:
            report["error"] = str(exc)
            controller.stop("BENCH_ERROR")
        finally:
            telemetry.forwarding = True
            try:
                controller.stop("SHUTDOWN")
                wait_for(controller, lambda s: s["stage"] == "STOPPED", timeout=2)
            except Exception as exc:
                report["cleanup_error"] = str(exc)
                report["result"] = "FAILED"
            try:
                # If setup failed before thread start, direct zero still ran.
                if watchdog._thread.ident is not None:
                    watchdog.close()
            finally:
                telemetry.close()
                report["outputs"] = output.events
                report["observations"] = telemetry.observations
                report["max_measured_speed_mps"] = telemetry.peak_speed
                filename = f"{scenario}-{'motion' if motion else 'stationary'}.json"
                (output_dir / filename).write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k not in ("outputs", "observations")}, indent=2), flush=True)
    return 0 if report["result"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sn", required=True)
    parser.add_argument("--scenario", choices=("silence", "deadline", "telemetry", "heartbeat", "operator-stop", "stale-intent"), required=True)
    parser.add_argument("--allow-motion", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if len(args.sn) != 14 or not args.sn.isascii() or not args.sn.isalnum():
        parser.error("Serial must be 14 ASCII letters/digits")
    if args.allow_motion and args.scenario not in ("silence", "operator-stop"):
        parser.error("Only silence/operator-stop are authorized motion bench scenarios")
    if args.worker:
        return run(args.sn, args.scenario, args.allow_motion)
    command = [sys.executable, __file__, "--sn", args.sn, "--scenario", args.scenario, "--worker"]
    if args.allow_motion:
        command.append("--allow-motion")
    try:
        return subprocess.run(command, timeout=20).returncode
    except subprocess.TimeoutExpired:
        print("TIMEOUT: worker terminated; physical stop unconfirmed. Operator must ensure power-off. No automatic retry.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
