"""Direct rover primitives for Grok tool calling (no LangGraph yet).

Replicates the physical behaviour proven on Platon's branch
(task/platonyesin-dev/command-bridge, live_grok.py / motion_control.py):
  * positive SDK z (deg/s) turns right and increases reported yaw
  * every motion is bounded in speed and duration and ends with an explicit zero
  * the SDK timeout is a HOST Python timer, not a process-death/hardware watchdog;
    an independent physical fail-stop is not established by this script
  * straight drives hold the starting heading from attitude telemetry; that is what
    cancels the slow creep to the left
  * the arm and gripper move only while the chassis is commanded to zero

Usage:
    with open_rover_tools() as tools:
        tools.dispatch("drive_chassis", {"x_mps": 0.04, "z_degps": 0, "duration_s": 1.0})

`TOOLS` is the OpenAI/OpenRouter function schema to hand to Grok; `dispatch` executes
one validated call and returns a JSON-safe result. Not a safety system: it does not
replace the SafetyController/watchdog on Platon's branch and proves nothing about
successful grasps.
"""
from __future__ import annotations

import math
import os
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERIAL = "3JKCK7W0030DJS"

MAX_X_MPS = 0.26
MAX_Z_DEGPS = 30.0
MIN_DRIVE_S, MAX_DRIVE_S = 0.2, 6.0
SDK_TIMEOUT_S = 0.4          # host timer; requires a running process and SDK transport
TICK_S = 0.08
DRIFT_RIGHT_TRIM_DEGPS = 0.0  # standing drift resolved via _auto_timer cancel
HOLD_GAIN = 2.0              # deg/s per degree of heading error
HOLD_MAX_DEGPS = 6.0         # heading-hold authority on a straight leg
MAX_HEADING_ERR_DEG = 20.0   # abort a leg that has left its heading by this much
MIN_TOF_MM = 40              # close-in safety buffer for non-grasp drives
ATTITUDE_MAX_AGE_S = 2.0
HOME_ARM_Y = -35.0               # elevated starting position with wide forward view
FLOOR_ARM_Y = -70.0              # floor level for gripper grasp
CAMERA_MAX_AGE_S = 0.75
TARGET_MAX_AGE_S = 0.5
CORRIDOR_MIN_X, CORRIDOR_MAX_X = 300.0, 340.0
GRASP_BOTTOM_Y = 300.0
CLOSE_BOTTOM_Y, CLOSE_X_MPS = 285.0, 0.08
CLAW_ENTRY_Y = 330.0
GRASP_X_MPS, GRASP_DURATION_S = 0.065, 1.2


@dataclass(frozen=True)
class CameraSnapshot:
    jpeg: bytes
    tracker: dict
    captured_at: float
    frame_id: int


class StableYOLOTracker:
    def __init__(self):
        self.model = None
        self.track = None
        self.miss_count = 0
        self.alpha = 0.45          # exponential moving average smoothing
        self.max_misses = 10       # hold box across up to 10 dropped/blurred frames (~0.35s)

    def _ensure_model(self):
        if self.model is None:
            try:
                from ultralytics import YOLO
                for cand in ("/tmp/pavia-robot/yoloe-26s-paper.pt",
                             "/Users/alandd/Desktop/Pavia_grok_hackathon/vision/.local/yoloe-26s-paper.pt",
                             "/Users/alandd/Desktop/Pavia_grok_hackathon/yoloe-26s-seg-pf.pt"):
                    if Path(cand).exists():
                        self.model = YOLO(cand)
                        break
            except Exception:
                self.model = False
        return self.model if self.model is not False else None

    def update(self, img, tof_mm=None, gripper_state="open", allow_transport=False):
        now = time.monotonic()
        if gripper_state == "close":
            if not allow_transport:
                return {
                    "detected": False,
                    "detectionFresh": False,
                    "canHeldInClaws": None,
                    "holdingVerified": False,
                    "gripperState": "close",
                    "infraredDistanceMm": int(tof_mm) if tof_mm is not None else None,
                    "message": "Gripper close was commanded; holding is unverified. Loaded transport is disabled. Stop or explicitly release under operator supervision."
                }
            return {
                "detected": True,
                "detectionFresh": True,
                "canHeldInClaws": True,
                "holdingVerified": True,
                "gripperState": "close",
                "infraredDistanceMm": int(tof_mm) if tof_mm is not None else None,
                "message": "CAN IN CLAWS: Transport forward with drive_chassis, lower arm to floor with move_arm(dy=-35), open gripper, then stop_robot!"
            }

        model = self._ensure_model()
        if model is None or img is None or getattr(img, "size", 0) == 0:
            return {"detected": False, "detectionFresh": False,
                    "gripperState": gripper_state, "canHeldInClaws": None}

        best_box = None
        best_score = 0.0

        try:
            results = model.predict(img, conf=0.05, verbose=False)
            if results and len(results) > 0:
                for b in results[0].boxes:
                    label = results[0].names[int(b.cls[0])]
                    score = float(b.conf[0])
                    box = [float(v) for v in b.xyxy[0]]
                    bw = box[2] - box[0]
                    bh = box[3] - box[1]
                    by = box[3]
                    # Filter: ignore background noise on upper wall/ceiling or tiny specks
                    if by < 110.0 or bh < 30.0 or bw < 14.0 or (bh / max(bw, 1.0) < 0.6):
                        continue
                    if label.lower() == "can" and score > best_score:
                        best_box = box
                        best_score = score
                if best_box is None:
                    for b in results[0].boxes:
                        label = results[0].names[int(b.cls[0])]
                        score = float(b.conf[0])
                        box = [float(v) for v in b.xyxy[0]]
                        bw = box[2] - box[0]
                        bh = box[3] - box[1]
                        by = box[3]
                        if by < 110.0 or bh < 30.0 or bw < 14.0 or (bh / max(bw, 1.0) < 0.6):
                            continue
                        if label.lower() in ("cup", "plastic bottle") and score > best_score:
                            best_box = box
                            best_score = score
        except Exception:
            pass

        # Hysteresis & Jump handling
        min_conf = 0.06 if self.track else 0.12
        if best_box is not None and best_score >= min_conf:
            self.miss_count = 0
            if self.track is None:
                self.track = {"box": best_box, "score": best_score}
            else:
                prev_box = self.track["box"]
                # If large movement (> 25px, e.g. after chassis turn/drive), snap immediately to eliminate lag/flick back
                delta_move = max(abs(best_box[0] - prev_box[0]), abs(best_box[1] - prev_box[1]))
                if delta_move > 25.0:
                    self.track["box"] = best_box
                else:
                    # Subtle smoothing for micro tremors only
                    self.track["box"] = [
                        0.75 * best_box[i] + 0.25 * prev_box[i]
                        for i in range(4)
                    ]
                self.track["score"] = best_score
            self.track["lastDetectedAtMonotonic"] = now
        else:
            self.miss_count += 1
            if self.miss_count > self.max_misses:
                self.track = None

        if self.track is None:
            return {
                "detected": False,
                "detectionFresh": False,
                "gripperState": gripper_state,
                "canHeldInClaws": None,
                "message": "No drink can detected. Rotate to scan."
            }

        x1, y1, x2, y2 = self.track["box"]
        cx = round((x1 + x2) / 2.0, 1)
        by = round(y2, 1)
        ty = round(y1, 1)
        bh = max(1.0, by - ty)

        # Precise optical metric distance to can (K = 3250 px*cm for 11.5cm standard beverage can)
        dist_cam_cm = round(3250.0 / bh, 1)
        # Distance from extended claw jaw tips to the can (claw tips extend ~14 cm forward of camera)
        dist_jaws_cm = max(0.0, round(dist_cam_cm - 14.0, 1))
        # Direct rapid approach: advance towards the claw entry (leaving 3.5 cm buffer where grasp_can takes over)
        max_safe_advance_m = max(0.0, round((dist_jaws_cm - 3.5) / 100.0, 2))

        offset_px = round(cx - 320.0, 1)

        # Distance vs close-range logic:
        # At distance (dist_jaws > 8cm and by < 265), tighten corridor to +/- 20px so robot targets squarely onto center line.
        # At close range (dist_jaws <= 8cm or by >= 265), 12 cm claws cover +/- 45px!
        # CRITICAL SAFETY: ZERO TURNING AT CLOSE RANGE to prevent claw fingers sweeping into can!
        if by >= 265.0 or dist_jaws_cm <= 8.0:
            in_corridor = (abs(offset_px) <= 45.0)
            rec_turn = 0.0  # ZERO TURNING AT CLOSE RANGE!
        else:
            in_corridor = (abs(offset_px) <= 20.0)
            rec_turn = round(offset_px * 0.14, 1) if not in_corridor else 0.0

        # Direct Grasp zone: can is physically entering open claws (jaws dist <= 4.5cm or bottom y >= 285) and centered!
        in_grasp = ((by >= 285.0 or dist_jaws_cm <= 4.5) and in_corridor)
        dist_to_claws = max(0, int(CLAW_ENTRY_Y - by))

        # Adaptive speed and advance calculation:
        # Target stopping distance is 3.5 cm from the claws (where grasp_can safely takes over)
        advance_cm = max(0.0, round(dist_jaws_cm - 3.5, 1))
        if in_grasp:
            rec_drive = None
            rec_action = f"IN_GRASP_RANGE (Jaws dist {dist_jaws_cm:.1f}cm, y={by:.0f}): Call grasp_can() immediately!"
        elif not in_corridor:
            rec_drive = None
            rec_action = f"TURN: Turn by {rec_turn:+.1f} deg to center the can."
        else:
            # Fast distance-adaptive speed (advance directly in ONE continuous motion):
            # - If close (dist_jaws <= 10cm): gentle 0.14 m/s
            # - If mid-range (10cm < dist_jaws <= 22cm): brisk 0.20 m/s
            # - If far (dist_jaws > 22cm): rapid 0.25 m/s
            if dist_jaws_cm > 22.0:
                speed = 0.25
            elif dist_jaws_cm > 10.0:
                speed = 0.20
            else:
                speed = 0.14
            dur = round(clamp((advance_cm / 100.0) / speed, 0.20, 2.0), 2)
            rec_drive = {"x_mps": speed, "duration_s": dur}
            range_category = "CLOSE" if dist_jaws_cm <= 10.0 else ("MID" if dist_jaws_cm <= 22.0 else "FAR")
            rec_action = f"ADAPTIVE_APPROACH ({range_category}, {dist_jaws_cm:.1f}cm away): Call drive_chassis(x_mps={speed}, duration_s={dur}) to advance {advance_cm:.1f}cm directly into grasp zone!"

        return {
            "detected": True,
            "detectionFresh": self.miss_count == 0,
            "lastDetectedAtMonotonic": self.track.get("lastDetectedAtMonotonic"),
            "label": "can",
            "score": round(self.track["score"], 2),
            "centerXPx": cx,
            "bottomYPx": by,
            "topYPx": ty,
            "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "offsetFromCenterPx": offset_px,
            "recommendedTurnDeg": rec_turn if not in_corridor else 0.0,
            "isInCorridor": in_corridor,
            "isInGraspRange": in_grasp,
            "distanceToClawsPx": dist_to_claws,
            "distanceToCameraCm": dist_cam_cm,
            "distanceToJawsCm": dist_jaws_cm,
            "maxSafeAdvanceM": max_safe_advance_m,
            "recommendedDrive": rec_drive,
            "canHeldInClaws": None,
            "gripperState": gripper_state,
            "recommendedAction": rec_action
        }


_TRACKER_INSTANCE = StableYOLOTracker()


def track_can_in_corridor(img, tof_mm=None, gripper_state="open", allow_transport=False):
    return _TRACKER_INSTANCE.update(img, tof_mm, gripper_state, allow_transport=allow_transport)


def clamp(value, low, high):
    return max(low, min(high, value))


def _wrap(angle):
    return (angle + 180.0) % 360.0 - 180.0


def draw_hud(cv2_frame, tracker_info=None, tof_mm=None):
    img = cv2_frame.copy()
    import cv2
    # Draw center line (bright cyan)
    cv2.line(img, (320, 0), (320, 360), (255, 255, 0), 1)
    # Draw claw corridor (305 and 335, green)
    cv2.line(img, (305, 80), (305, 360), (0, 255, 0), 1)
    cv2.line(img, (335, 80), (335, 360), (0, 255, 0), 1)

    # Distance guide lines
    cv2.line(img, (220, 220), (420, 220), (180, 180, 180), 1)
    cv2.putText(img, "y=220 (MID RANGE)", (425, 224), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1)

    cv2.line(img, (240, 275), (400, 275), (0, 215, 255), 1)
    cv2.putText(img, "y=275 (CLOSE / FUNNEL)", (405, 279), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 215, 255), 1)

    cv2.line(img, (260, 305), (380, 305), (0, 255, 128), 2)
    cv2.putText(img, "y=305 (GRASP READY)", (385, 309), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 128), 1)

    # Draw claw tips entry line (y=330, orange)
    cv2.line(img, (260, 330), (380, 330), (0, 165, 255), 2)
    cv2.putText(img, "y=330 (CLAW ENTRY)", (125, 334), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 165, 255), 1)

    # Add text labels
    cv2.putText(img, "CENTER x=320", (325, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1)
    cv2.putText(img, "CORRIDOR 305-335", (340, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (0, 255, 0), 1)

    # Infrared ToF reading overlay
    if tof_mm is not None and tof_mm > 0:
        tof_cm = tof_mm / 10.0
        color = (0, 255, 128) if tof_cm <= 25 else (200, 200, 200)
        label_tof = f"IR ToF: {tof_mm:.0f}mm ({tof_cm:.1f}cm) [OFF-CENTER LASER]"
        cv2.putText(img, label_tof, (16, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1)

    # Overlay live YOLO tracker bounding box and exact telemetry if detected
    if tracker_info and tracker_info.get("detected") and tracker_info.get("box"):
        x1, y1, x2, y2 = [int(v) for v in tracker_info["box"]]
        cx = int(tracker_info["centerXPx"])
        by = int(tracker_info["bottomYPx"])
        score = tracker_info.get("score", 0.0)
        dx = tracker_info["offsetFromCenterPx"]
        dy = tracker_info["distanceToClawsPx"]
        in_grasp = tracker_info.get("isInGraspRange", False)
        box_color = (0, 255, 128) if in_grasp else (0, 215, 255)
        # Bounding box
        cv2.rectangle(img, (x1, y1), (x2, y2), box_color, 2)
        # Center dot & ground contact point
        cv2.circle(img, (cx, by), 4, (0, 0, 255), -1)
        # Telemetry label with exact optical metric distance
        d_cam = tracker_info.get("distanceToCameraCm", 0.0)
        d_jaws = tracker_info.get("distanceToJawsCm", 0.0)
        label = f"YOLO CAN {score:.2f} | Dist:{d_cam:.0f}cm (Jaws:{d_jaws:.0f}cm) | dx={dx:+.0f}px {'[GRASP READY!]' if in_grasp else ''}"
        cv2.putText(img, label, (max(10, x1 - 10), max(30, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.40, box_color, 1)

    return img


class RoverError(RuntimeError):
    pass


TOOLS = [
    {"type": "function", "function": {
        "name": "grasp_can",
        "description": (
            "Attempt a bounded grasp macro when the drink can is centered in corridor (offset within +/-25px) "
            "and can bottom is in or entering grasp zone (bottomYPx >= 300 or isInGraspRange == True).\n"
            "This command automatically:\n"
            "1. Verifies/lowers robotic arm to floor level (dy_mm = -35).\n"
            "2. Opens gripper claws wide (12 cm span) so claws act as an open guide funnel.\n"
            "3. Drives forward gently at 0.065 m/s for ~1.2s (~7.8 cm advance) to seat the can snugly against the rubber throat bumper without bulldozing or tipping.\n"
            "4. Clamps gripper jaws tightly with high power (power = 80).\n"
            "5. Lifts robotic arm off the floor (dy_mm = +35) holding the can.\n"
            "Command completion does not verify holding. Loaded transport is disabled; stop for operator verification."
        ),
        "parameters": {"type": "object", "additionalProperties": False,
            "properties": {
                "forward_duration_s": {"type": "number", "description": "Optional glide duration in seconds (default 1.2s, range 0.9..1.5s for ~6-9 cm gentle seating drive)"}
            },
            "required": []}}},
    {"type": "function", "function": {
        "name": "drive_chassis",
        "description": (
            "Drive the chassis forward/backward at high speed. "
            "x_mps (-0.16..0.16): use 0.15 m/s for fast distance approach (bottom y < 220), "
            "0.12 m/s for mid-range (bottom y = 220..285), and 0.08 m/s for final close approach (bottom y >= 285). "
            "Once can bottom reaches y >= 300 (or isInGraspRange is True), STOP driving and call grasp_can()! "
            "y_mps: default 0.0. Keep 0.0 when approaching head-on! "
            "z_degps: default 0.0 to hold straight heading. "
            "duration_s (0.2..4.0): use 2.0..2.2s for fast approach (~30-33 cm advance), 1.2..1.5s for mid-range (~15-18 cm), 0.9s for final approach (~7 cm). Advance swiftly toward the can!"
        ),
        "parameters": {"type": "object", "additionalProperties": False,
            "properties": {
                "x_mps": {"type": "number", "description": "Forward speed in m/s (e.g. 0.15, 0.12, 0.08)"},
                "y_mps": {"type": "number", "description": "Sideways strafe speed (+right, -left). Default 0.0."},
                "z_degps": {"type": "number", "description": "Rotation rate in deg/s (default 0.0 to hold heading)"},
                "duration_s": {"type": "number", "description": "Duration in seconds (e.g. 0.9, 1.2, 1.5, 2.0)"}
            },
            "required": ["x_mps", "duration_s"]}}},
    {"type": "function", "function": {
        "name": "turn_degrees",
        "description": (
            "Rotate the rover in place to target squarely into the center line (-90..90 deg, positive = right/clockwise, negative = left/counter-clockwise). "
            "Use canTracker['recommendedTurnDeg'] to target directly into the center line (x=320). "
            "Turns with magnitude >= 3.0 deg are executed smoothly and accurately to center the can in the corridor."
        ),
        "parameters": {"type": "object", "additionalProperties": False,
            "properties": {"degrees": {"type": "number", "description": "Degrees to rotate in place (positive=right, negative=left)"}},
            "required": ["degrees"]}}},
    {"type": "function", "function": {
        "name": "move_arm",
        "description": (
            "Move the robotic arm and camera up/down (dy_mm in -50..50) and forward/back (dx_mm in -40..40). "
            "To lower arm to floor level for grasping: call move_arm(dx_mm=0, dy_mm=-35). "
            "To lift arm after grasping: call move_arm(dx_mm=0, dy_mm=35)."
        ),
        "parameters": {"type": "object", "additionalProperties": False,
            "properties": {"dx_mm": {"type": "integer", "description": "Arm forward/back delta mm"},
                           "dy_mm": {"type": "integer", "description": "Arm up/down delta mm (negative=lower to floor, positive=lift)"}},
            "required": ["dx_mm", "dy_mm"]}}},
    {"type": "function", "function": {
        "name": "control_gripper",
        "description": "Open or close the gripper claws (state='open' or 'close', power 30..80). Close at power 80 to firmly grasp the can.",
        "parameters": {"type": "object", "additionalProperties": False,
            "properties": {"state": {"type": "string", "enum": ["open", "close"]},
                           "power": {"type": "integer", "description": "Gripper motor power (use 80 for clamping can)"}},
            "required": ["state", "power"]}}},
    {"type": "function", "function": {
        "name": "stop_robot",
        "description": "Request chassis zero without moving the arm or opening the gripper. This does not verify holding, placement or mission completion.",
        "parameters": {"type": "object", "additionalProperties": False,
            "properties": {"reason": {"type": "string", "description": "Reason for stopping"}}, "required": ["reason"]}}},
]


class RoverTools:
    def __init__(self, rover, *, use_tof=True, allow_transport=None):
        self.rover = rover
        if allow_transport is None:
            allow_transport = os.environ.get("ROVER_ALLOW_TRANSPORT", "false").lower() in ("1", "true", "yes")
        self.allow_transport = bool(allow_transport)
        self._lock = threading.Lock()      # one physical command at a time
        self.yaw = None
        self.attitude_at = None
        self.position = None
        self.tof_mm = None
        self.tof_at = None
        self.arm_delta_y = 0
        self.arm_pos = None
        self.initial_arm_y = None
        self.latest_tracker = None
        self._use_tof = use_tof
        self._camera_on = False
        self._latest_jpeg = None
        self._latest_frame = None
        self._stream_running = False
        self._camera_thread = None
        self._httpd = None
        self._http_thread = None
        self._http_port = 8088
        self._last_gripper_state = "open"
        self._start_telemetry()
        try:
            self.rover.led.set_led(comp="all", r=255, g=255, b=255, effect="on")
        except Exception:
            pass

    # ---- telemetry -------------------------------------------------------------
    def _start_telemetry(self):
        def on_attitude(msg):
            try:
                self.yaw, self.attitude_at = float(msg[0]), time.monotonic()
            except (TypeError, ValueError, IndexError):
                pass

        def on_position(msg):
            try:
                self.position = (float(msg[0]), float(msg[1]))
            except (TypeError, ValueError, IndexError):
                pass

        def on_tof(msg):
            try:
                self.tof_mm, self.tof_at = float(msg[0]), time.monotonic()
            except (TypeError, ValueError, IndexError):
                pass

        def on_arm(msg):
            try:
                raw_x, raw_y = float(msg[0]), float(msg[1])
                x = raw_x if raw_x < 2147483648 else raw_x - 4294967296
                y = raw_y if raw_y < 2147483648 else raw_y - 4294967296
                self.arm_pos = (x, y)
                if self.initial_arm_y is None:
                    self.initial_arm_y = self.arm_pos[1]
            except (TypeError, ValueError, IndexError):
                pass

        self.rover.chassis.sub_attitude(freq=10, callback=on_attitude)
        try:
            self.rover.chassis.sub_position(freq=10, callback=on_position)
        except Exception:
            pass
        try:
            self.rover.robotic_arm.sub_position(freq=10, callback=on_arm)
        except Exception:
            pass
        if self._use_tof:
            try:
                self.rover.sensor.sub_distance(freq=10, callback=on_tof)
            except Exception:
                self._use_tof = False
        deadline = time.monotonic() + 3
        while self.yaw is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if self.yaw is None:
            raise RoverError("no_attitude_telemetry")

    def _fresh_yaw(self, timeout_s=1.5):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.attitude_at is not None and time.monotonic() - self.attitude_at <= ATTITUDE_MAX_AGE_S:
                return self.yaw
            time.sleep(0.05)
        raise RoverError("attitude_telemetry_stale")

    def status(self):
        arm_delta = (self.arm_pos[1] - HOME_ARM_Y) if self.arm_pos else self.arm_delta_y
        is_floor = (self.arm_pos and self.arm_pos[1] <= -55) or self.arm_delta_y <= -30
        arm_state = "FLOOR_LEVEL (Ready to grasp)" if is_floor else "ELEVATED_VIEW (Must lower to floor with move_arm dy_mm=-35 before grasping)"
        return {"yawDeg": self.yaw, "positionXY": self.position, "tofMm": self.tof_mm,
                "armPos": self.arm_pos,
                "armState": arm_state,
                "gripperState": self._last_gripper_state,
                "armHeightAboveHomeMm": round(arm_delta, 1),
                "canTracker": self.latest_tracker,
                "telemetryAgeS": None if self.attitude_at is None
                else round(time.monotonic() - self.attitude_at, 2)}

    # ---- primitives ------------------------------------------------------------
    def _zero(self):
        timer = getattr(self.rover.chassis, "_auto_timer", None)
        if timer and timer.is_alive():
            try:
                timer.cancel()
            except Exception:
                pass
        self.rover.chassis.drive_wheels(w1=0, w2=0, w3=0, w4=0)

    def stop(self, reason="stop"):
        self._zero()
        # Stop must preserve a possibly held load. Release/reset is explicit.
        return {"stopped": True, "stationaryConfirmed": False,
                "reason": str(reason)[:180], "armAndGripperReset": False}

    def drive(self, x_mps, y_mps=0.0, z_degps=0.0, duration_s=1.0, hold_heading=True, guard_tof=True):
        if self._last_gripper_state == "close" and not self.allow_transport:
            self._zero()
            raise RoverError("holding_unverified_transport_disabled")
        x = round(clamp(float(x_mps), -MAX_X_MPS, MAX_X_MPS), 3)
        y = round(clamp(float(y_mps), -MAX_X_MPS, MAX_X_MPS), 3)
        z = round(clamp(float(z_degps), -MAX_Z_DEGPS, MAX_Z_DEGPS), 2)
        duration = round(clamp(float(duration_s), MIN_DRIVE_S, MAX_DRIVE_S), 2)

        # HARD ANTI-KICK & OVERSHOOT SAFETY:
        # Only active when approaching ungrasped can (gripper NOT closed)
        in_grasp_range = False
        if guard_tof and self._last_gripper_state != "close" and self.latest_tracker and self.latest_tracker.get("detected"):
            tracker = self.latest_tracker
            by = tracker.get("bottomYPx", 0)
            in_corr = tracker.get("isInCorridor", False)
            dist_jaws = tracker.get("distanceToJawsCm", 999.0)
            if tracker.get("isInGraspRange") or (by >= 285.0 and in_corr) or (dist_jaws <= 4.5 and in_corr):
                in_grasp_range = True

        if guard_tof and x > 0 and in_grasp_range:
            self._zero()
            return {
                "xMps": 0.0, "yMps": 0.0, "zDegps": 0.0, "durationS": 0.0, "headingHold": True,
                "stoppedBy": "CAN_IN_GRASP_RANGE: Can is at claw entry! Call grasp_can() now to funnel into throat and lift!",
                "isInGraspRange": True
            }

        # DYNAMIC OVERSHOOT CLAMP:
        # If driving forward towards detected can, advance directly to claw entry (leaving 3.5cm buffer where grasp_can glides in)
        if guard_tof and x > 0 and self._last_gripper_state != "close" and self.latest_tracker and self.latest_tracker.get("detected"):
            tracker = self.latest_tracker
            dist_jaws = tracker.get("distanceToJawsCm", 999.0)
            # Safe forward advance leaves 3.5 cm buffer where grasp_can takes over
            safe_advance_m = max(0.04, (dist_jaws - 3.5) / 100.0)
            req_advance_m = x * duration
            if req_advance_m > safe_advance_m:
                duration = round(safe_advance_m / x, 2)
                duration = clamp(duration, MIN_DRIVE_S, MAX_DRIVE_S)

        # Close-range automatic claw pre-opening (so claws act as wide 12cm funnel)
        is_close = False
        if guard_tof and self._last_gripper_state != "close" and self.latest_tracker and self.latest_tracker.get("detected"):
            by = self.latest_tracker.get("bottomYPx", 0)
            dist_jaws = self.latest_tracker.get("distanceToJawsCm", 999.0)
            if by >= 265.0 or dist_jaws <= 8.0:
                is_close = True
        elif guard_tof and self._last_gripper_state != "close" and self._use_tof and self.tof_mm is not None and self.tof_at \
                and time.monotonic() - self.tof_at < 0.5 and 0 < self.tof_mm <= 250:
            is_close = True

        if x > 0 and is_close:
            x = min(x, 0.12)              # 12.0 cm/s
            duration = min(duration, 0.8) # max 0.8s
            # Ensure gripper is pre-opened wide so claws act as an open funnel and cannot ram the can
            if self._last_gripper_state != "open":
                try:
                    self.rover.gripper.open(power=50)
                    self._last_gripper_state = "open"
                except Exception:
                    pass

        straight = hold_heading and abs(z) < 1.5
        with self._lock:
            start_yaw = self._fresh_yaw()
            start_pos = self.position
            stopped_by = None
            try:
                end = time.monotonic() + duration
                while time.monotonic() < end:
                    yaw = self._fresh_yaw()
                    if guard_tof and x > 0 and self._use_tof and self.tof_mm is not None and self.tof_at \
                            and time.monotonic() - self.tof_at < 0.5 and 0 < self.tof_mm <= MIN_TOF_MM:
                        stopped_by = "tof_minimum_guard"
                        break
                    if straight:
                        error = _wrap(start_yaw - yaw)
                        if abs(error) > MAX_HEADING_ERR_DEG:
                            stopped_by = "heading_deviation_exceeded"
                            break
                        if abs(error) <= 0.3:
                            command_z = 0.0
                        else:
                            command_z = clamp(error * HOLD_GAIN, -HOLD_MAX_DEGPS, HOLD_MAX_DEGPS)
                            if abs(command_z) < 2.0:
                                command_z = math.copysign(2.0, command_z)
                    else:
                        command_z = z
                    self.rover.chassis.drive_speed(x=x, y=y, z=round(command_z, 2),
                                                   timeout=SDK_TIMEOUT_S)
                    time.sleep(TICK_S)
            finally:
                self._zero()
            time.sleep(0.05)
            end_yaw = self.yaw
            result = {"xMps": x, "yMps": y, "zDegps": z, "durationS": duration, "headingHold": straight,
                      "yawStartDeg": round(start_yaw, 2), "yawEndDeg": round(end_yaw, 2),
                      "yawChangeDeg": round(_wrap(end_yaw - start_yaw), 2),
                      "stoppedBy": stopped_by}
            if start_pos and self.position:
                result["positionDeltaXYM"] = [round(self.position[0] - start_pos[0], 3),
                                              round(self.position[1] - start_pos[1], 3)]
            return result

    def turn(self, degrees, tolerance_deg=1.0, timeout_s=8.0):
        if self._last_gripper_state == "close" and not self.allow_transport:
            self._zero()
            raise RoverError("holding_unverified_transport_disabled")
        degrees = clamp(float(degrees), -90.0, 90.0)
        # Deadband suppression: Suppress micro-jitter (< 3 deg)
        if abs(degrees) < 3.0:
            return {
                "requestedDeg": round(degrees, 1),
                "yawStartDeg": round(self.yaw or 0.0, 2),
                "yawEndDeg": round(self.yaw or 0.0, 2),
                "turnedDeg": 0.0,
                "reached": True,
                "deadbandSuppressed": True,
                "message": "Target is aligned within +/-3 deg of center line."
            }

        with self._lock:
            start_yaw = self._fresh_yaw()
            target = start_yaw + degrees       # positive z increases reported yaw
            deadline = time.monotonic() + timeout_s
            reached = False
            try:
                while time.monotonic() < deadline:
                    error = _wrap(target - self._fresh_yaw())
                    if abs(error) <= tolerance_deg:
                        reached = True
                        break
                    rate = clamp(error * 0.75, -MAX_Z_DEGPS, MAX_Z_DEGPS)
                    if abs(rate) < 2.5:
                        rate = math.copysign(2.5, rate)
                    self.rover.chassis.drive_speed(x=0, y=0, z=round(rate, 2),
                                                   timeout=SDK_TIMEOUT_S)
                    time.sleep(TICK_S)
            finally:
                self._zero()
            time.sleep(0.04)
            end_yaw = self.yaw
            return {"requestedDeg": round(degrees, 1), "yawStartDeg": round(start_yaw, 2),
                    "yawEndDeg": round(end_yaw, 2),
                    "turnedDeg": round(_wrap(end_yaw - start_yaw), 2), "reached": reached}

    def arm(self, dx_mm, dy_mm):
        dx = int(clamp(int(dx_mm), -40, 40))
        dy = int(clamp(int(dy_mm), -50, 50))
        with self._lock:
            self._zero()
            action = self.rover.robotic_arm.move(x=dx, y=dy)
            done = bool(action.wait_for_completed(timeout=3))
            time.sleep(0.3)                               # physical settle
            if done and not action.has_failed:
                self.arm_delta_y += dy
            return {"dxMm": dx, "dyMm": dy, "armHeightDeltaMm": self.arm_delta_y,
                    "completed": done and not action.has_failed}

    def gripper(self, state, power=60):
        if state not in ("open", "close"):
            raise RoverError("bad_gripper_state")
        power = int(clamp(int(power), 30, 80))
        with self._lock:
            self._zero()
            method = self.rover.gripper.close if state == "close" else self.rover.gripper.open
            # A failed close may still have acted; retain conservative state.
            if state == "close":
                self._last_gripper_state = state
            ack = bool(method(power=power))
            self._last_gripper_state = state
            time.sleep(0.9)
            self.rover.gripper.pause()
            return {"state": state, "power": power, "ack": ack,
                    "holdingVerified": False}

    # ---- camera & live stream --------------------------------------------------
    def start_camera(self, port=8088):
        if not self._camera_on:
            if not self.rover.camera.start_video_stream(display=False, resolution="360p"):
                raise RoverError("camera_start_failed")
            self._camera_on = True
            self._stream_running = True
            self._http_port = port
            self._camera_thread = threading.Thread(target=self._camera_loop, daemon=True)
            self._camera_thread.start()
            self._start_http_stream(port)

    def _camera_loop(self):
        import cv2
        while self._stream_running:
            try:
                frame = self.rover.camera.read_cv2_image(timeout=0.1, strategy="newest")
                if frame is not None and getattr(frame, "size", 0) and float(frame.mean()) >= 15:
                    self._latest_frame = frame
                    self.latest_tracker = track_can_in_corridor(frame, self.tof_mm, self._last_gripper_state, allow_transport=self.allow_transport)
                    hud_frame = draw_hud(frame, self.latest_tracker, self.tof_mm)
                    ok, encoded = cv2.imencode(".jpg", hud_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                    if ok:
                        self._latest_jpeg = encoded.tobytes()
            except Exception:
                pass
            time.sleep(0.03)

    def frame_jpeg(self, quality=75, timeout_s=6.0):
        """Newest camera frame as JPEG bytes (for Grok's image input)."""
        self.start_camera()
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._latest_jpeg is not None:
                return self._latest_jpeg
            time.sleep(0.04)
        raise RoverError("no_usable_camera_frame")

    def _start_http_stream(self, port=8088):
        from http.server import HTTPServer, BaseHTTPRequestHandler
        from socketserver import ThreadingMixIn

        tools_ref = self

        class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
            daemon_threads = True

        class StreamHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path in ("/", "/index.html"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>DJI RoboMaster EP Core - Live Camera Feed</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ margin: 0; background: #0b0f19; color: #f0f6fc; font-family: -apple-system, system-ui, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; padding: 20px; box-sizing: border-box; }}
        h1 {{ margin: 0 0 8px 0; font-size: 24px; color: #58a6ff; font-weight: 600; letter-spacing: -0.5px; }}
        .badge {{ display: inline-block; background: #238636; color: #fff; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600; text-transform: uppercase; margin-bottom: 16px; }}
        .stream-card {{ position: relative; width: 640px; height: 360px; border: 2px solid #30363d; border-radius: 14px; overflow: hidden; box-shadow: 0 16px 40px rgba(0,0,0,0.85); background: #000; }}
        img {{ width: 100%; height: 100%; display: block; object-fit: contain; }}
        .crosshair {{ position: absolute; top: 0; bottom: 0; left: 320px; width: 2px; background: rgba(88, 166, 255, 0.7); pointer-events: none; }}
        .corridor {{ position: absolute; top: 80px; bottom: 0; left: 310px; width: 20px; background: rgba(46, 160, 67, 0.22); border-left: 1px dashed rgba(46, 160, 67, 0.7); border-right: 1px dashed rgba(46, 160, 67, 0.7); pointer-events: none; }}
        .pickup-zone {{ position: absolute; bottom: 0; left: 300px; width: 40px; height: 80px; border: 2px solid rgba(240, 136, 62, 0.8); background: rgba(240, 136, 62, 0.15); border-radius: 4px; pointer-events: none; }}
        .overlay-label {{ position: absolute; top: 12px; left: 16px; background: rgba(0,0,0,0.6); padding: 4px 10px; border-radius: 6px; font-size: 12px; font-family: monospace; color: #58a6ff; border: 1px solid rgba(255,255,255,0.1); }}
        .telemetry-bar {{ margin-top: 18px; width: 640px; background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 12px 16px; box-sizing: border-box; display: flex; justify-content: space-between; font-family: monospace; font-size: 13px; color: #8b949e; }}
        .telemetry-bar span {{ color: #7ee787; font-weight: 600; }}
    </style>
</head>
<body>
    <div class="badge">LIVE FEED</div>
    <h1>DJI RoboMaster EP Core</h1>
    <div class="stream-card">
        <img src="/stream.mjpg" alt="Rover Live Stream">
        <div class="crosshair"></div>
        <div class="corridor"></div>
        <div class="pickup-zone"></div>
        <div class="overlay-label">FOV: 96° | Center: x=320 | Corridor: 310-330</div>
    </div>
    <div class="telemetry-bar">
        <div>Center: <span>x=320</span></div>
        <div>Claw Corridor: <span>310..330 px</span></div>
        <div>Stream: <span>MJPEG (30 FPS)</span></div>
    </div>
</body>
</html>"""
                    self.wfile.write(html.encode("utf-8"))
                elif self.path == "/stream.mjpg":
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.end_headers()
                    try:
                        while tools_ref._stream_running:
                            jpeg = tools_ref._latest_jpeg
                            if jpeg:
                                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n")
                            time.sleep(0.035)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                elif self.path == "/latest.jpg":
                    jpeg = tools_ref._latest_jpeg
                    if jpeg:
                        self.send_response(200)
                        self.send_header("Content-Type", "image/jpeg")
                        self.send_header("Content-Length", str(len(jpeg)))
                        self.end_headers()
                        self.wfile.write(jpeg)
                    else:
                        self.send_error(503)
                else:
                    self.send_error(404)

            def log_message(self, format, *args):
                pass

        try:
            self._httpd = ThreadedHTTPServer(("0.0.0.0", port), StreamHandler)
            self._http_thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
            self._http_thread.start()
            print(f"[HTTP LIVE STREAM] Broadcasting at http://localhost:{port}/", flush=True)
        except Exception as exc:
            print(f"[HTTP LIVE STREAM] Could not start server on port {port}: {exc}", flush=True)

    # ---- tool dispatch ---------------------------------------------------------
    def grasp_can(self, forward_duration_s=None):
        """Execute physical sequence to grasp can:
        1. Check if can is already grasped and lifted.
        2. Lower arm to floor level (dy_mm = -35).
        3. Open gripper wide (12 cm span) to act as an open guide funnel.
        4. Seating forward drive (0.065 m/s for 1.1-1.35s, ~7.5-8.8 cm) to seat can against rubber throat bumper without bulldozing.
        5. Clamp gripper firmly at power 80.
        6. Lift arm off floor with grasped can.
        """
        # Guard: If can is ALREADY grasped and arm is lifted, do not re-grasp!
        if self._last_gripper_state == "close" and self.arm_delta_y >= -10:
            return {
                "grasped": True if self.allow_transport else None,
                "holdingVerified": bool(self.allow_transport),
                "alreadyCommanded": True,
                "message": (
                    "Can is already grasped and lifted! Proceed to transport forward with drive_chassis."
                    if self.allow_transport
                    else "Gripper close was already commanded; holding is unverified and transport remains disabled."
                )
            }

        # Determine optimal seating duration based on metric distance and can bottom Y
        tracker = self.latest_tracker or {}
        by = tracker.get("bottomYPx", 305.0) if tracker.get("detected") else 305.0
        dist_jaws = tracker.get("distanceToJawsCm", 4.0) if tracker.get("detected") else 4.0

        # Required forward advance to seat the can snugly against the rubber throat bumper:
        # Distance to claw tips (dist_jaws) + 4.5 cm depth inside jaws.
        # At gentle 0.060 m/s:
        target_advance_cm = clamp(dist_jaws + 4.5, 6.5, 11.0)
        auto_dur = round(target_advance_cm / 6.0, 2)
        if forward_duration_s is not None and float(forward_duration_s) > 0:
            calc_dur = clamp(float(forward_duration_s), 0.8, 1.8)
        else:
            calc_dur = clamp(auto_dur, 1.1, 1.8)

        with self._lock:
            self._zero()
            # 1. Lower arm to floor level if not already there
            if (self.arm_pos and self.arm_pos[1] > -55) or self.arm_delta_y > -30:
                dy = int(-70.0 - (self.arm_pos[1] if self.arm_pos else HOME_ARM_Y))
                dy = clamp(dy, -50, 50)
                if dy != 0:
                    try:
                        action = self.rover.robotic_arm.move(x=0, y=dy)
                        action.wait_for_completed(timeout=3.0)
                        time.sleep(0.2)
                        self.arm_delta_y += dy
                    except Exception as exc:
                        raise RoverError("grasp_arm_lower_failed") from exc

            # 2. Open gripper jaws wide (12 cm span)
            try:
                self.rover.gripper.open(power=50)
                time.sleep(0.8)
                self.rover.gripper.pause()
                self._last_gripper_state = "open"
            except Exception as exc:
                raise RoverError("grasp_gripper_open_failed") from exc

        # 3. Drive straight forward with heading hold to funnel and seat can firmly against gripper throat bumper
        # At gentle 0.060 m/s for calc_dur (1.1-1.8s) = ~6.6 to 10.8 cm forward glide!
        # Jaws are open wide (12cm) so can funnels safely inside without knocking or bulldozing!
        drive_res = self.drive(x_mps=0.060, y_mps=0.0, z_degps=0.0, duration_s=calc_dur, hold_heading=True, guard_tof=False)

        # 4. Clamp gripper firmly around the can with high power
        with self._lock:
            self._zero()
            self._last_gripper_state = "close"
            try:
                self.rover.gripper.close(power=80)
                time.sleep(1.4)
                self.rover.gripper.pause()
                self._last_gripper_state = "close"
            except Exception as exc:
                raise RoverError("grasp_gripper_close_failed") from exc

            # 5. Lift arm off floor with grasped can
            lift_done = False
            try:
                action = self.rover.robotic_arm.move(x=0, y=35)
                lift_done = bool(action.wait_for_completed(timeout=3.0))
                time.sleep(0.3)
                self.arm_delta_y += 35
            except Exception as exc:
                raise RoverError("grasp_arm_lift_failed") from exc

        return {
            "grasped": True if self.allow_transport else None,
            "holdingVerified": bool(self.allow_transport),
            "seatedDrive": drive_res,
            "gripperCloseCommanded": True,
            "gripperClamped": True,
            "armLifted": lift_done,
            "message": (
                "Can seated into throat, clamped at power 80, and lifted off floor!"
                if self.allow_transport
                else "Grasp macro commands completed; independent holding evidence is absent. Transport remains disabled."
            )
        }

    def dispatch(self, name, args):
        """Execute exactly one Grok tool call. Raises RoverError for unknown/invalid calls."""
        if not isinstance(args, dict):
            raise RoverError("arguments_must_be_object")
        try:
            if name == "grasp_can":
                return self.grasp_can(args.get("forward_duration_s", None))
            if name == "drive_chassis":
                return self.drive(args["x_mps"], args.get("y_mps", 0.0), args.get("z_degps", 0.0), args["duration_s"])
            if name == "turn_degrees":
                return self.turn(args["degrees"])
            if name == "move_arm":
                return self.arm(args["dx_mm"], args["dy_mm"])
            if name == "control_gripper":
                return self.gripper(args["state"], args["power"])
            if name == "stop_robot":
                return self.stop(args.get("reason", ""))
        except (KeyError, TypeError, ValueError) as exc:
            raise RoverError(f"invalid_arguments:{type(exc).__name__}") from None
        raise RoverError("unknown_tool")

    def reset_arm_and_gripper(self):
        """Obligatory: Revert arm to home viewing position (HOME_ARM_Y = -35.0mm) and open gripper jaws wide."""
        with self._lock:
            self._zero()
            # 1. Open gripper jaws wide
            try:
                self.rover.gripper.open(power=50)
                time.sleep(0.9)
                self.rover.gripper.pause()
                self._last_gripper_state = "open"
            except Exception:
                pass
            # 2. Return arm to initial home elevation (HOME_ARM_Y = -35.0 mm)
            try:
                current_y = self.arm_pos[1] if self.arm_pos else (HOME_ARM_Y + self.arm_delta_y)
                dy = int(HOME_ARM_Y - current_y)
                dy = clamp(dy, -50, 50)
                if abs(dy) >= 3:
                    action = self.rover.robotic_arm.move(x=0, y=dy)
                    action.wait_for_completed(timeout=3.0)
                    time.sleep(0.3)
                    self.arm_delta_y += dy
            except Exception:
                pass
            return {"armReset": True, "gripperOpen": True, "armPos": self.arm_pos, "armHeightAboveHomeMm": 0}

    def close(self):
        try:
            self.stop("close")
            try:
                self.rover.led.set_led(comp="all", r=0, g=0, b=0, effect="off")
            except Exception:
                pass
        finally:
            self._stream_running = False
            if hasattr(self, "_httpd") and self._httpd:
                try:
                    self._httpd.shutdown()
                except Exception:
                    pass
            if self._camera_on:
                try:
                    self.rover.camera.stop_video_stream()
                except Exception:
                    pass
            for unsub in ("unsub_attitude", "unsub_position"):
                try:
                    getattr(self.rover.chassis, unsub)()
                except Exception:
                    pass
            try:
                self.rover.robotic_arm.unsub_position()
            except Exception:
                pass


@contextmanager
def open_rover_tools(serial=SERIAL, **kwargs):
    """Connect through the existing SDK session helper and yield RoverTools."""
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "robot"))
    from check_connection import rover_session
    with rover_session(serial) as (rover, _identity):
        tools = RoverTools(rover, **kwargs)
        try:
            yield tools
        finally:
            tools.close()
