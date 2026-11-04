"""Staged real hardware checks. Actuator stages require --allow-motion.

Software cleanup and host timers are not independent physical fail-stops.
Keep the area clear and the operator at the rover's power control.
"""

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time

from check_connection import rover_session


class Samples:
    def __init__(self):
        self._lock = threading.Lock()
        self._values = {}

    def callback(self, name):
        def receive(value):
            with self._lock:
                self._values[name] = (time.monotonic(), value)
        return receive

    def read(self, name, max_age=0.7):
        with self._lock:
            stamp, value = self._values.get(name, (0, None))
        if time.monotonic() - stamp > max_age:
            raise RuntimeError(f"Missing/stale {name} telemetry")
        return value

    def snapshot(self):
        with self._lock:
            return {name: {"age_s": round(time.monotonic() - stamp, 3),
                           "value": value}
                    for name, (stamp, value) in self._values.items()}


def subscribe(rover, samples):
    subscriptions = [
        ("position", rover.chassis.sub_position, rover.chassis.unsub_position),
        ("attitude", rover.chassis.sub_attitude, rover.chassis.unsub_attitude),
        ("velocity", rover.chassis.sub_velocity, rover.chassis.unsub_velocity),
        ("battery", rover.battery.sub_battery_info, rover.battery.unsub_battery_info),
        ("distance_mm", rover.sensor.sub_distance, rover.sensor.unsub_distance),
        ("arm_mm", rover.robotic_arm.sub_position, rover.robotic_arm.unsub_position),
        ("gripper", rover.gripper.sub_status, rover.gripper.unsub_status),
    ]
    results, cleanup = {}, []
    for name, start, stop in subscriptions:
        try:
            results[name] = bool(start(freq=10, callback=samples.callback(name)))
            if results[name]:
                cleanup.append(stop)
        except Exception as exc:
            results[name] = str(exc)
    return results, cleanup


def wait_stopped(rover, samples):
    # ProtoChassisSpeedMode is a PUSH with no acknowledgement. The legacy
    # wrapper returns False even after sending; verify actual fresh telemetry.
    rover.chassis.drive_speed(x=0, y=0, z=0)
    deadline, stable = time.monotonic() + 1.5, 0
    previous_yaw = samples.read("attitude", max_age=0.3)[0]
    while time.monotonic() < deadline:
        time.sleep(0.12)
        velocity = samples.read("velocity", max_age=0.3)
        if any(not math.isfinite(v) for v in velocity):
            raise RuntimeError("Invalid velocity telemetry")
        quiet = all(abs(velocity[i]) <= 0.025 for i in (0, 1, 3, 4)) and all(
            abs(velocity[i]) <= 3 for i in (2, 5))
        yaw = samples.read("attitude", max_age=0.3)[0]
        yaw_delta = abs((yaw - previous_yaw + 180) % 360 - 180)
        previous_yaw = yaw
        quiet = quiet and yaw_delta <= 0.4
        stable = stable + 1 if quiet else 0
        if stable >= 3:
            return velocity
        rover.chassis.drive_speed(x=0, y=0, z=0)
    raise RuntimeError(f"Stationary telemetry not confirmed within 1.5 s: {velocity}")


def chassis_check(rover, samples):
    results = []
    wait_stopped(rover, samples)
    for name, x, y, z in [
        ("forward", 0.05, 0, 0), ("backward", -0.05, 0, 0),
        ("right", 0, 0.05, 0), ("left", 0, -0.05, 0),
        ("rotate_right", 0, 0, 5), ("rotate_left", 0, 0, -5),
    ]:
        print(f"Beginning bounded pulse: {name}", flush=True)
        before = samples.read("position")
        yaw_before = samples.read("attitude")[0]
        deadline = time.monotonic() + 0.6
        measured = []
        try:
            while time.monotonic() < deadline:
                samples.read("position", max_age=0.5)
                samples.read("velocity", max_age=0.5)
                rover.chassis.drive_speed(x=x, y=y, z=z, timeout=0.25)
                time.sleep(0.1)
                measured.append(samples.read("velocity"))
        finally:
            stopped = wait_stopped(rover, samples)
        after = samples.read("position")
        yaw_after = samples.read("attitude")[0]
        displacement = math.hypot(after[0] - before[0], after[1] - before[1])
        yaw_change = (yaw_after - yaw_before + 180) % 360 - 180
        observed = displacement > 0.003 if not z else abs(yaw_change) > 0.3
        results.append({"test": name, "command": [x, y, z],
                        "position_delta_m": [after[i] - before[i] for i in range(2)],
                        "yaw_delta_deg": yaw_change, "velocity_samples": measured,
                        "movement_detected": observed, "stopped_velocity": stopped})
        if not observed:
            raise RuntimeError(f"{name}: no corresponding movement in telemetry; stopping sequence")
        print(json.dumps(results[-1]), flush=True)
        time.sleep(0.3)
    # Test SDK host-timer expiration separately from the explicit stop sequence.
    rover.chassis.drive_speed(x=0.05, y=0, z=0, timeout=0.25)
    time.sleep(0.7)
    expired_velocity = samples.read("velocity")
    stopped = wait_stopped(rover, samples)
    expiry_observed = all(abs(expired_velocity[i]) <= 0.025 for i in (0, 1, 3, 4))
    results.append({"test": "SDK_host_timer_expiry", "velocity_before_explicit_stop": expired_velocity,
                    "expiry_stop_observed": expiry_observed, "stopped_velocity": stopped,
                    "independent_hardware_watchdog": "NOT TESTED"})
    if not expiry_observed:
        raise RuntimeError("Host-timer expiry did not establish stationary telemetry")
    return results


def run_stage(serial, stage, output):
    output.mkdir(parents=True, exist_ok=True)
    report = {"stage": stage, "status": "FAILED", "checks": {}}
    samples = Samples()
    with rover_session(serial) as (rover, identity):
        report["identity"] = identity
        report["identity"].pop("actuator_commands", None)
        report["identity"].pop("camera_and_sensor_checks", None)
        cleanup = []
        try:
            report["subscriptions"], cleanup = subscribe(rover, samples)
            time.sleep(1.5)
            report["telemetry"] = samples.snapshot()
            if stage == "passive":
                for name in ("position", "attitude", "velocity", "battery", "arm_mm", "gripper", "distance_mm"):
                    try:
                        report["checks"][name] = {"status": "DATA_RECEIVED", "value": samples.read(name)}
                    except RuntimeError as exc:
                        report["checks"][name] = {"status": "UNVERIFIED", "reason": str(exc)}
                try:
                    import cv2
                    if not rover.camera.start_video_stream(display=False, resolution="360p"):
                        raise RuntimeError("Camera start not acknowledged")
                    frame = rover.camera.read_cv2_image(timeout=5, strategy="newest")
                    if frame is None or not frame.size:
                        raise RuntimeError("No decoded camera image")
                    if not cv2.imwrite(str(output / "camera.jpg"), frame):
                        raise RuntimeError("Camera snapshot write failed")
                    report["checks"]["camera"] = {"status": "REAL_FRAME_DECODED", "shape": list(frame.shape)}
                except Exception as exc:
                    report["checks"]["camera"] = {"status": "UNVERIFIED", "reason": str(exc)}
                finally:
                    rover.camera.stop_video_stream()
            else:
                battery = samples.read("battery")
                if battery < 20:
                    raise RuntimeError("Battery below 20%; actuator tests skipped")
                wait_stopped(rover, samples)
                if stage == "chassis":
                    report["checks"]["chassis"] = chassis_check(rover, samples)
                elif stage == "arm":
                    def arm_position():
                        return [v - 2**32 if v >= 2**31 else v for v in samples.read("arm_mm")]
                    report["checks"]["arm_raw_before"] = samples.read("arm_mm")
                    before = arm_position()
                    # A small upward movement avoids blindly homing toward the floor.
                    if not (0 <= before[0] <= 300 and -100 <= before[1] <= 140):
                        raise RuntimeError(f"Arm pose outside conservative jog window: {before}")
                    action = rover.robotic_arm.move(x=0, y=5)
                    if not action.wait_for_completed(timeout=3) or action.has_failed:
                        raise RuntimeError("Arm jog not confirmed; do not issue another arm command")
                    time.sleep(0.3)
                    raised = arm_position()
                    if not 1 <= raised[1] - before[1] <= 10:
                        raise RuntimeError("Arm position did not confirm bounded upward jog")
                    action = rover.robotic_arm.move(x=0, y=-5)
                    if not action.wait_for_completed(timeout=3) or action.has_failed:
                        raise RuntimeError("Arm return jog not confirmed")
                    time.sleep(0.3)
                    after = arm_position()
                    report["checks"]["arm"] = {"before_mm": before, "raised_mm": raised,
                        "after_mm": after, "pickup": "NOT TESTED",
                        "return_within_3mm": all(abs(after[i] - before[i]) <= 3 for i in range(2)),
                        "coordinate_decode": "signed32 interpretation of SDK unsigned fields"}
                elif stage == "gripper":
                    report["checks"]["gripper"] = []
                    for name, method in [("open", rover.gripper.open), ("close", rover.gripper.close)]:
                        before = samples.read("gripper")
                        try:
                            if not method(power=10):
                                raise RuntimeError(f"Gripper {name} command not acknowledged")
                            time.sleep(0.4)
                        finally:
                            if not rover.gripper.pause():
                                raise RuntimeError("Gripper pause not acknowledged")
                        time.sleep(0.2)
                        report["checks"]["gripper"].append({"command": name,
                            "before": before, "after": samples.read("gripper"),
                            "power": 10, "pulse_s": 0.4,
                            "physical_travel": "OPERATOR CONFIRMATION REQUIRED"})
            report["status"] = "COMPLETED"
        except Exception as exc:
            report["error"] = str(exc)
        finally:
            if stage != "passive":
                try:
                    report["final_stop_velocity"] = wait_stopped(rover, samples)
                    report["gripper_pause_ack"] = bool(rover.gripper.pause())
                except Exception as exc:
                    report["cleanup_error"] = str(exc)
                    report["status"] = "FAILED"
            for stop in reversed(cleanup):
                try:
                    stop()
                except Exception:
                    pass
            (output / f"{stage}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["status"] == "COMPLETED" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sn", required=True)
    parser.add_argument("--stage", choices=("passive", "chassis", "arm", "gripper"), required=True)
    parser.add_argument("--allow-motion", action="store_true")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if len(args.sn) != 14 or not args.sn.isascii() or not args.sn.isalnum():
        parser.error("Serial must be 14 ASCII letters/digits")
    if args.stage != "passive" and not args.allow_motion:
        parser.error("Actuator stages require explicit --allow-motion")
    output = Path(__file__).resolve().parent / ".local" / "hardware-check"
    if args.worker:
        return run_stage(args.sn, args.stage, output)
    command = [sys.executable, __file__, "--sn", args.sn, "--stage", args.stage, "--worker"]
    if args.allow_motion:
        command.append("--allow-motion")
    try:
        return subprocess.run(command, timeout=40).returncode
    except subprocess.TimeoutExpired:
        print("HARDWARE TEST TIMEOUT: cleanup is unconfirmed; operator must ensure rover is stopped. No automatic retries.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
