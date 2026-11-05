"""Offline tests with a fake rover that has a constant leftward yaw bias."""
import time, unittest
from unittest.mock import patch
try:
    import rover_tools as rt
except ImportError:
    from robot import rover_tools as rt
rt.TICK_S = 0.01

class Chassis:
    def __init__(s): s.cbs = {}; s.yaw = 0.0; s.cmds = []; s.wheel_cmds = []; s.last = time.monotonic()
    def sub_attitude(s, freq, callback): s.cbs["att"] = callback; callback([s.yaw, 0, 0])
    def sub_position(s, freq, callback): s.cbs["pos"] = callback
    def drive_speed(s, x=0, y=0, z=0, timeout=None):
        s.cmds.append((x, y, z, timeout))
        now = time.monotonic(); dt = min(now - s.last, 0.1); s.last = now
        s.yaw += (z - 1.5) * dt * 10          # bias ~ -1.5 deg/s (measured while driving), sped up 10x
        s.cbs["att"]([s.yaw, 0, 0])
    def drive_wheels(s, w1=0, w2=0, w3=0, w4=0, timeout=None):
        s.wheel_cmds.append((w1, w2, w3, w4))
class Action:
    has_failed = False
    def wait_for_completed(s, timeout): return True
class Arm:
    def __init__(s): s.moves = []
    def move(s, x, y): s.moves.append((x, y)); return Action()
class Grip:
    def __init__(s): s.calls = []
    def close(s, power): s.calls.append(("close", power)); return True
    def open(s, power): s.calls.append(("open", power)); return True
    def pause(s): s.calls.append(("pause",)); return True
class Rover:
    def __init__(s): s.chassis, s.robotic_arm, s.gripper = Chassis(), Arm(), Grip()
    class sensor:
        @staticmethod
        def sub_distance(freq, callback): raise RuntimeError("no tof")

def tools():
    time.sleep = lambda *_: None if False else None   # noqa (replaced below)

class T(unittest.TestCase):
    def setUp(self):
        self.real_sleep = time.sleep
        time.sleep = lambda s: self.real_sleep(min(s, 0.01))
        self.r = Rover(); self.t = rt.RoverTools(self.r)
    def tearDown(self): time.sleep = self.real_sleep
    def test_clamps_and_always_ends_with_zero_and_short_timeout(self):
        out = self.t.drive(5, 99, 99, 99)
        self.assertEqual((out["xMps"], out["zDegps"], out["durationS"]), (rt.MAX_X_MPS, rt.MAX_Z_DEGPS, rt.MAX_DRIVE_S))
        self.assertEqual(self.r.chassis.wheel_cmds[-1], (0, 0, 0, 0))
        self.assertTrue(all(c[3] == rt.SDK_TIMEOUT_S for c in self.r.chassis.cmds))
    def test_straight_drive_holds_heading_against_bias(self):
        rt.TICK_S = 0.02
        out = self.t.drive(0.04, 0, 1.0)
        self.assertTrue(out["headingHold"])
        self.assertLess(abs(out["yawChangeDeg"]), 6)          # uncorrected it would drift ~ -40
        self.assertGreater(max(c[2] for c in self.r.chassis.cmds), 0)   # corrected rightwards
    def test_turn_reaches_target_with_correct_sign(self):
        out = self.t.turn(30)
        self.assertTrue(out["reached"]); self.assertGreater(out["turnedDeg"], 20)
    def test_stale_attitude_refuses_to_move(self):
        self.t.attitude_at = time.monotonic() - 5
        with self.assertRaises(rt.RoverError): self.t.drive(0.04, 0, 1)
        self.assertEqual(self.r.chassis.cmds, [])
    def test_dispatch_validation(self):
        for bad in (("nope", {}), ("drive_chassis", {"x_mps": 1}), ("control_gripper", {"state": "x", "power": 5}), ("move_arm", [])):
            with self.assertRaises(rt.RoverError): self.t.dispatch(*bad)
    def test_arm_gripper_stop_and_clamping(self):
        self.assertTrue(self.t.dispatch("move_arm", {"dx_mm": 500, "dy_mm": -500})["completed"])
        self.assertEqual(self.r.robotic_arm.moves[-1], (40, -50))
        self.assertEqual(self.t.dispatch("control_gripper", {"state": "close", "power": 999})["power"], 80)
        self.assertTrue(self.t.dispatch("stop_robot", {"reason": "x"})["stopped"])
    def test_tool_names_match_dispatch(self):
        for tool in rt.TOOLS: self.assertIn(tool["function"]["name"], ("drive_chassis", "turn_degrees", "move_arm", "control_gripper", "stop_robot", "grasp_can"))
    def test_closed_gripper_is_not_holding_evidence_or_transport_authority(self):
        tracker = rt.StableYOLOTracker()
        tracker.track = {"box": [300, 200, 340, 300]}
        for image in (None, type("Image", (), {"size": 10})()):
            with patch.object(tracker, "_ensure_model", side_effect=AssertionError("must not infer holding")):
                result = tracker.update(image, gripper_state="close")
            self.assertIsNone(result["canHeldInClaws"])
            self.assertFalse(result["holdingVerified"])
            self.assertFalse(result["detectionFresh"])
        self.t.gripper("close")
        before = list(self.r.chassis.cmds)
        for action in (lambda: self.t.drive(.04), lambda: self.t.turn(15)):
            with self.assertRaisesRegex(rt.RoverError, "holding_unverified"):
                action()
        self.assertEqual(self.r.chassis.cmds, before)
        self.assertIsNone(self.t.grasp_can()["grasped"])
        self.assertFalse(self.t.grasp_can()["holdingVerified"])
    def test_stop_and_close_preserve_possible_load_until_explicit_release(self):
        self.t.gripper("close")
        self.r.gripper.calls.clear()
        self.r.robotic_arm.moves.clear()
        self.t.stop("operator_stop")
        self.t.close()
        self.assertEqual(self.r.gripper.calls, [])
        self.assertEqual(self.r.robotic_arm.moves, [])
        self.assertEqual(self.r.chassis.wheel_cmds[-1], (0, 0, 0, 0))
        self.t.gripper("open")
        self.assertEqual(self.r.gripper.calls[0][0], "open")
    def test_grasp_macro_ack_never_claims_verified_holding(self):
        self.t.arm_delta_y = -35
        with patch.object(self.t, "drive", return_value={"stoppedBy": None}):
            result = self.t.grasp_can()
        self.assertIsNone(result["grasped"])
        self.assertFalse(result["holdingVerified"])
        self.assertTrue(result["gripperCloseCommanded"])
    def test_failed_gripper_close_aborts_before_arm_lift(self):
        self.t.arm_delta_y = -35
        with patch.object(self.t, "drive", return_value={}), patch.object(
                self.r.gripper, "close", side_effect=RuntimeError("no ack")):
            with self.assertRaisesRegex(rt.RoverError, "grasp_gripper_close_failed"):
                self.t.grasp_can()
        self.assertEqual(self.r.robotic_arm.moves, [])
        self.assertEqual(self.t._last_gripper_state, "close")
    def test_loaded_transport_enabled_when_authorized(self):
        self.t.allow_transport = True
        self.t.gripper("close")
        out = self.t.drive(0.04, 0, 0.5)
        self.assertEqual(out["xMps"], 0.04)
        turn_out = self.t.turn(15)
        self.assertTrue(turn_out["reached"])
        self.t.arm_delta_y = -35
        with patch.object(self.t, "drive", return_value={"stoppedBy": None}):
            grasp_res = self.t.grasp_can()
        self.assertTrue(grasp_res["grasped"])
        self.assertTrue(grasp_res["holdingVerified"])
if __name__ == "__main__": unittest.main()
