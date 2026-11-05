"""Supervised P4 acceptance; no HTTP listener, policy calls or manipulation.

Default: disarmed startup, telemetry and camera, with zero commands only.
--allow-motion: one 0.05 m/s forward intent, then silence/watchdog acceptance.
Zero command timing is host dispatch evidence, not physical stop latency.
"""

import argparse
import json
from pathlib import Path
import socket
import time

from bridge import PhysicalBridgeRuntime, PhysicalChassisAdapter
from camera import EPStreamCapture
from check_connection import rover_session


class RecordingOutput(PhysicalChassisAdapter):
    def __init__(self, rover):
        super().__init__(rover, max_x=0.05, max_y=0.05, max_yaw=1)
        self.events = []

    def __call__(self, x, y, yaw):
        if y != 0 or yaw != 0 or x not in (0, 0.05):
            raise ValueError("Acceptance permits only zero or 0.05 m/s forward")
        event = {"at": time.monotonic(), "velocity": [x, y, yaw]}
        super().__call__(x, y, yaw)
        event["returnedAt"] = time.monotonic()
        self.events.append(event)


def wait_status(controller, predicate, timeout=4):
    """Read status only: never tick, heartbeat, or fabricate sensor evidence."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = controller.status()
        if predicate(status):
            return status
        time.sleep(0.01)
    raise RuntimeError(f"Acceptance timeout: {controller.status()}")


def require_response(response, expected, code=None):
    status, body = response
    if status != expected or (code and body.get("error", {}).get("code") != code):
        raise RuntimeError(f"Unexpected bridge response: {response}")
    return body


def check_no_camera_server():
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", 8004)) == 0:
            raise RuntimeError("Port 8004 is occupied; hand off its SDK owner first")


def run(serial, motion=False):
    check_no_camera_server()
    report = {"result": "FAILED", "motionAuthorized": motion,
              "manipulation": "SIMULATED_ONLY", "grokCalls": 0,
              "scope": "chassis-silence" if motion else "disarmed-camera-startup",
              "remainingPhysicalGates": ["release-pickup-arbitration",
                                         "independent-pickup-evidence", "transport", "placement"]}
    runtime = capture = output = None
    try:
        with rover_session(serial) as (rover, identity):
            report["identity"] = {k: identity[k] for k in ("firmware", "connection")}
            output = RecordingOutput(rover)
            runtime = PhysicalBridgeRuntime(rover, output=output)
            capture = EPStreamCapture(rover, runtime.bridge.frame_buffer)
            ctrl, bridge = runtime.controller, runtime.bridge
            try:
                runtime.start()
                report["startup"] = wait_status(ctrl, lambda s: s["stopConfirmed"])
                if runtime.telemetry.battery is None or runtime.telemetry.battery < 20:
                    raise RuntimeError("Battery missing or below 20 percent")
                capture.start()
                report["camera"] = bridge.frame_buffer.health_snapshot()
                if not report["camera"]["frameFresh"]:
                    raise RuntimeError("Camera frame is not fresh")
                # Observe the camera-only stage while remaining disarmed.
                # These thresholds cannot rule out very slow physical drift.
                until = time.monotonic() + 2
                while time.monotonic() < until:
                    status = ctrl.status()
                    if not status["stopConfirmed"] or not status["telemetryFresh"]:
                        raise RuntimeError("Stationary confirmation lost during camera observation")
                    time.sleep(0.02)
                report["cameraStationaryObservationS"] = 2
                # A redundant stop while already latched STOPPED legitimately
                # preserves confirmation. Rearm without driving first so this
                # is a NEW stop requiring fresh post-stop observations.
                require_response(bridge.manual_rearm(), 200)
                ctrl.stop("EARLY_REARM_CHECK")
                if ctrl.status()["stage"] != "UNKNOWN":
                    raise RuntimeError("Stop was confirmed without fresh post-stop samples")
                require_response(bridge.manual_rearm(), 409, "NOT_STOPPED")
                require_response(bridge.manual_lease({}), 503, "TELEMETRY_STALE")
                report["earlyRearmRejected"] = True
                report["freshStop"] = wait_status(ctrl, lambda s: s["stopConfirmed"])
                if motion:
                    require_response(bridge.manual_rearm(), 200)
                    lease = require_response(bridge.manual_lease({}), 200)
                    now = time.time()
                    intent_started = time.monotonic()
                    require_response(bridge.manual_drive({
                        "leaseId": lease["leaseId"], "generation": lease["generation"],
                        "sequence": 1, "issuedAt": now, "expiresAt": now + 0.5,
                        "xMps": 0.05, "yMps": 0, "zDegps": 0,
                    }), 200)
                    # No requests/status polling during the cutoff interval.
                    time.sleep(0.35)
                    moving = next(e for e in output.events if any(e["velocity"]))
                    zero = next((e for e in output.events
                                 if e["at"] > moving["at"] and not any(e["velocity"])), None)
                    report["silenceToZeroDispatchS"] = None if zero is None else zero["at"] - intent_started
                    if zero is None or report["silenceToZeroDispatchS"] > 0.3:
                        raise RuntimeError("Silence-to-zero dispatch exceeded 300 ms")
                    report["confirmedStop"] = wait_status(ctrl, lambda s: s["stopConfirmed"])
                    if report["confirmedStop"]["reason"] != "INPUT_SILENCE":
                        raise RuntimeError("Stopped for a different fault")
                    count = len(output.events)
                    require_response(bridge.manual_rearm(), 200)
                    time.sleep(0.15)
                    if any(any(e["velocity"]) for e in output.events[count:]):
                        raise RuntimeError("Rearm resumed motion")
                    report["rearmNoResume"] = True
                report["result"] = "PASS"
            finally:
                # Confirm zero before tearing down telemetry or the SDK session.
                ctrl.stop("ACCEPTANCE_SHUTDOWN")
                try:
                    report["shutdownStop"] = wait_status(ctrl, lambda s: s["stopConfirmed"])
                finally:
                    try:
                        capture.close()
                    finally:
                        runtime.close()
    except Exception as exc:
        report["result"] = "FAILED"
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if output:
            report["outputs"] = output.events
        if runtime:
            report["telemetry"] = runtime.telemetry.diagnostics
        report["physicalOutcome"] = "OPERATOR_CONFIRMATION_REQUIRED"
        destination = Path(__file__).parent / ".local" / "acceptance"
        destination.mkdir(parents=True, exist_ok=True)
        path = destination / f"{time.time_ns()}.json"
        path.write_text(json.dumps(report, indent=2))
        print(json.dumps({"result": report["result"], "report": str(path),
                          "error": report.get("error")}), flush=True)
    return 0 if report["result"] == "PASS" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sn", required=True)
    parser.add_argument("--allow-motion", action="store_true")
    args = parser.parse_args()
    if len(args.sn) != 14 or not args.sn.isascii() or not args.sn.isalnum():
        parser.error("Serial must be 14 ASCII letters/digits")
    check_no_camera_server()
    print("One SDK owner only. Clear rover surroundings; keep physical power switch accessible.")
    expected = "ALLOW FORWARD TEST" if args.allow_motion else "ALLOW ZERO ONLY"
    if input(f"Operator: type {expected} to start: ").strip() != expected:
        parser.error("Operator did not confirm; no SDK connection opened")
    return run(args.sn, args.allow_motion)


if __name__ == "__main__":
    raise SystemExit(main())
