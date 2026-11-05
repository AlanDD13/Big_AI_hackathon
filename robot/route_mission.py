"""Measured L-route, existing Grok pickup callback, and breadcrumb return.

Distances describe displacement of the initial front-wheel line, not a camera
or gripper-to-object range. Wheel odometry is relative and can slip; this is a
supervised fixed-route demo, not calibrated obstacle-aware navigation.
"""
import math
import time


class MissionError(RuntimeError):
    pass


def _wrap(degrees):
    return (degrees + 180) % 360 - 180


def _pose(tools):
    pose = tools.pose()
    if (not isinstance(pose, (tuple, list)) or len(pose) != 3
            or not all(type(v) in (int, float) and math.isfinite(v) for v in pose)):
        raise MissionError("invalid_pose")
    return tuple(pose)


def _check_motion(name, result):
    if not isinstance(result, dict):
        raise MissionError("missing_motion_result")
    if result.get("stoppedBy"):
        raise MissionError("motion_blocked:" + str(result["stoppedBy"]))
    if name == "turn_degrees" and result.get("reached") is not True:
        raise MissionError("turn_incomplete")
    if name == "move_arm" and result.get("completed") is not True:
        raise MissionError("arm_incomplete")
    if name == "grasp_can":
        if not (result.get("gripperCloseCommanded") is True
                and result.get("gripperClamped") is True
                and result.get("armLifted") is True):
            raise MissionError("pickup_incomplete")
        _check_motion("drive_chassis", result.get("seatedDrive"))


class _RecordedTools:
    """Keep the same SDK owner while recording all Grok motion, including grasp."""
    def __init__(self, tools, breadcrumbs):
        self.tools, self.breadcrumbs = tools, breadcrumbs
        self.pickup_commanded = False

    def __getattr__(self, name):
        return getattr(self.tools, name)

    def dispatch(self, name, args):
        if name == "control_gripper" and args.get("state") != "open":
            raise MissionError("use_grasp_can_for_pickup")
        if name == "drive_chassis" and args.get("z_degps", 0) != 0:
            raise MissionError("route_requires_separate_drive_and_turn")
        self.breadcrumbs.append(_pose(self.tools))
        result = self.tools.dispatch(name, args)
        _check_motion(name, result)
        self.breadcrumbs.append(_pose(self.tools))
        if name == "grasp_can":
            self.pickup_commanded = True
        return result


def _turn_to(tools, yaw):
    for _ in range(5):
        delta = _wrap(yaw - _pose(tools)[2])
        if abs(delta) <= 3:
            return
        _check_motion("turn_degrees", tools.turn(max(-90, min(90, delta))))
    raise MissionError("turn_incomplete")


def _forward(tools, distance):
    start = _pose(tools)
    previous, stalled = 0.0, 0
    deadline = time.monotonic() + distance / .05 + 10
    for _ in range(600):
        current = _pose(tools)
        travelled = math.hypot(current[0] - start[0], current[1] - start[1])
        if travelled > distance + .04:
            raise MissionError("route_overshoot")
        if abs(_wrap(current[2] - start[2])) > 5:
            print("[ROUTE] Correcting accumulated heading drift", flush=True)
            _turn_to(tools, start[2])
        if distance - travelled <= .01:
            print(f"[ROUTE] Leg completed: {travelled * 100:.1f}cm / {distance * 100:.0f}cm", flush=True)
            return current
        if time.monotonic() > deadline:
            raise MissionError("route_timeout")
        stalled = stalled + 1 if travelled - previous < .001 else 0
        if stalled >= 8:
            raise MissionError("odometry_no_progress")
        previous = travelled
        _check_motion("drive_chassis", tools.drive(
            x_mps=min(.16, (distance - travelled) / .5), duration_s=.5,
            reference_yaw_deg=start[2]))
    raise MissionError("route_action_budget")


def _return_to(tools, target, frame_offset):
    _turn_to(tools, target[2])
    previous, stalled = math.inf, 0
    deadline = time.monotonic() + 60
    for _ in range(600):
        x, y, yaw = _pose(tools)
        dx, dy = target[0] - x, target[1] - y
        remaining = math.hypot(dx, dy)
        if remaining <= .01:
            return
        if time.monotonic() > deadline:
            raise MissionError("return_timeout")
        if remaining > previous + .04:
            raise MissionError("return_diverging")
        stalled = stalled + 1 if previous - remaining < .001 else 0
        if stalled >= 8:
            raise MissionError("odometry_no_progress")
        previous = remaining
        # Forward-axis direction in this SDK session's position frame is
        # measured from the first outbound leg; raw IMU yaw need not start at 0.
        angle = math.radians(yaw + frame_offset)
        scale = min(.16 / remaining, 1 / .5)
        _check_motion("drive_chassis", tools.drive(
            x_mps=(dx * math.cos(angle) + dy * math.sin(angle)) * scale,
            y_mps=(-dx * math.sin(angle) + dy * math.cos(angle)) * scale,
            duration_s=.5, reference_yaw_deg=target[2]))
    raise MissionError("return_action_budget")


def run_route_mission(tools, grok_loop):
    """30 cm forward, right 90°, 154 cm forward, pickup, retrace, place.

    ``grok_loop(recorded_tools)`` runs in the caller's existing rover session
    and must stop immediately after successful grasp macro completion, returning
    ``result='PICKUP_COMMANDED'``. Failures stop in place; uncertain motion is
    never retried or automatically reversed. Release occurs only after return
    and successful lowering; abort cleanup preserves the current gripper state.
    """
    try:
        if not tools.allow_transport:
            raise MissionError("transport_disabled")
        start = _pose(tools)
        print("[ROUTE] Forward 30cm from initial front-wheel line", flush=True)
        corner = _forward(tools, .30)
        frame_offset = math.degrees(math.atan2(corner[1] - start[1],
                                             corner[0] - start[0])) - start[2]
        print("[ROUTE] Turning right 90deg", flush=True)
        _turn_to(tools, start[2] + 90)
        turned = _pose(tools)
        print("[ROUTE] Forward 154cm", flush=True)
        end = _forward(tools, 1.54)
        breadcrumbs = [start, corner, turned, end]
        recorded = _RecordedTools(tools, breadcrumbs)
        print("[ROUTE] Starting Grok pickup loop", flush=True)
        pickup = grok_loop(recorded)
        if pickup.get("result") != "PICKUP_COMMANDED" or not recorded.pickup_commanded:
            raise MissionError("pickup_incomplete:" + str(pickup.get("result")))
        print("[ROUTE] Grasp macro completed; retracing recorded route with gripper closed", flush=True)
        for waypoint in reversed(breadcrumbs[:-1]):
            _return_to(tools, waypoint, frame_offset)
        _turn_to(tools, start[2])
        print("[ROUTE] Returned to starting odometry pose and heading", flush=True)
        tools.stop("place_at_start")
        print("[ROUTE] Lowering can to floor", flush=True)
        lowered = tools.arm(dx_mm=0, dy_mm=-35)
        _check_motion("move_arm", lowered)
        print("[ROUTE] Opening gripper to release can", flush=True)
        released = tools.gripper("open", power=50)
        if released.get("ack") is not True or released.get("state") != "open":
            raise MissionError("release_unconfirmed")
        raised = tools.arm(dx_mm=0, dy_mm=35)
        _check_motion("move_arm", raised)
        print("[ROUTE] Release commanded at start; empty arm raised", flush=True)
        return {**pickup, "result": "RETURNED_AND_RELEASED", "startPose": start,
                "finalPose": _pose(tools), "holdingVerified": False,
                "releaseCommanded": True, "placementVerified": False,
                "placement": {"lower": lowered, "release": released, "raise": raised},
                "returnWaypointCount": len(breadcrumbs) - 1}
    finally:
        tools.stop("route_mission_finished")
