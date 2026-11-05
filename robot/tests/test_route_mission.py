"""Route orchestration against synthetic odometry; no SDK or model calls."""
import math
import unittest

from robot.route_mission import MissionError, run_route_mission


class Tools:
    allow_transport = True

    def __init__(self, yaw=0, frame_angle=0):
        self.x, self.y, self.yaw = 2.0, -1.0, yaw
        self.frame_offset = frame_angle - yaw
        self.actions = []
        self.stalled = False
        self.blocked = False

    def pose(self):
        return self.x, self.y, self.yaw

    def drive(self, x_mps, y_mps=0, z_degps=0, duration_s=1, **kwargs):
        self.actions.append(("drive", x_mps, y_mps))
        angle = math.radians(self.yaw + self.frame_offset)
        if not self.stalled and not self.blocked:
            self.x += (x_mps * math.cos(angle) - y_mps * math.sin(angle)) * duration_s
            self.y += (x_mps * math.sin(angle) + y_mps * math.cos(angle)) * duration_s
        return {"stoppedBy": "obstacle" if self.blocked else None}

    def turn(self, degrees, **kwargs):
        self.actions.append(("turn", degrees))
        self.yaw = (self.yaw + degrees + 180) % 360 - 180
        return {"reached": True}

    def dispatch(self, name, args):
        if name == "grasp_can":
            self.drive(.06, duration_s=1.4)  # seating motion is part of the grasp
            return {"gripperCloseCommanded": True, "gripperClamped": True,
                    "armLifted": True, "seatedDrive": {"stoppedBy": None}}
        if name == "turn_degrees":
            return self.turn(**args)
        if name == "drive_chassis":
            return self.drive(**args)
        raise AssertionError(name)

    def stop(self, reason):
        self.actions.append(("stop", reason))

    def arm(self, dx_mm, dy_mm):
        self.actions.append(("arm", dx_mm, dy_mm, self.pose()))
        return {"completed": True}

    def gripper(self, state, power):
        self.actions.append(("gripper", state, power))
        return {"state": state, "ack": True}


class RouteTests(unittest.TestCase):
    def pickup(self, tools):
        tools.dispatch("turn_degrees", {"degrees": 12})
        tools.dispatch("drive_chassis", {"x_mps": .1, "duration_s": 1})
        tools.dispatch("grasp_can", {})
        return {"result": "PICKUP_COMMANDED", "steps": [], "grokCalls": 3}

    def test_route_then_pickup_and_return_in_rotated_odometry_frame(self):
        for yaw, frame in ((0, 0), (172, -43)):
            with self.subTest(yaw=yaw, frame=frame):
                tools = Tools(yaw, frame)
                at_pickup = []
                def pickup(recorded):
                    at_pickup.append(tools.pose())
                    return self.pickup(recorded)
                result = run_route_mission(tools, pickup)
                a = math.radians(frame)
                expected = (2 + .30*math.cos(a) - 1.54*math.sin(a),
                            -1 + .30*math.sin(a) + 1.54*math.cos(a))
                self.assertAlmostEqual(at_pickup[0][0], expected[0], delta=.025)
                self.assertAlmostEqual(at_pickup[0][1], expected[1], delta=.025)
                self.assertAlmostEqual(tools.x, 2, delta=.025)
                self.assertAlmostEqual(tools.y, -1, delta=.025)
                self.assertAlmostEqual(tools.yaw, yaw, delta=3)
                self.assertEqual(result["result"], "RETURNED_AND_RELEASED")
                self.assertFalse(result["holdingVerified"])
                self.assertEqual([a for a in tools.actions if a[0] == "turn"][0], ("turn", 90))

    def test_lower_release_and_raise_only_after_return(self):
        tools = Tools()
        result = run_route_mission(tools, self.pickup)
        placement = tools.actions[-5:]
        self.assertEqual(placement[0], ("stop", "place_at_start"))
        self.assertEqual(placement[1][:3], ("arm", 0, -35))
        self.assertAlmostEqual(placement[1][3][0], 2, delta=.025)
        self.assertAlmostEqual(placement[1][3][1], -1, delta=.025)
        self.assertAlmostEqual(placement[1][3][2], 0, delta=3)
        self.assertEqual(placement[2], ("gripper", "open", 50))
        self.assertEqual(placement[3][:3], ("arm", 0, 35))
        self.assertEqual(placement[4], ("stop", "route_mission_finished"))
        self.assertTrue(result["releaseCommanded"])
        self.assertFalse(result["placementVerified"])

    def test_failed_lowering_never_opens_gripper(self):
        tools = Tools()
        tools.arm = lambda *args, **kwargs: {"completed": False}
        with self.assertRaisesRegex(MissionError, "arm_incomplete"):
            run_route_mission(tools, self.pickup)
        self.assertFalse(any(a[0] == "gripper" for a in tools.actions))

    def test_failed_release_does_not_raise_arm(self):
        tools = Tools()
        tools.gripper = lambda *args, **kwargs: {"state": "open", "ack": False}
        with self.assertRaisesRegex(MissionError, "release_unconfirmed"):
            run_route_mission(tools, self.pickup)
        self.assertEqual([a[2] for a in tools.actions if a[0] == "arm"], [-35])

    def test_failed_return_never_lowers_or_releases(self):
        tools = Tools()
        def pickup(recorded):
            result = self.pickup(recorded)
            tools.blocked = True
            return result
        with self.assertRaisesRegex(MissionError, "motion_blocked"):
            run_route_mission(tools, pickup)
        self.assertFalse(any(a[0] in ("arm", "gripper") for a in tools.actions))

    def test_failed_pickup_does_not_start_return(self):
        tools = Tools()
        def fail(recorded):
            self.last_actions = len(tools.actions)
            return {"result": "STOPPED_BY_GROK", "steps": [], "grokCalls": 1}
        with self.assertRaisesRegex(MissionError, "pickup_incomplete"):
            run_route_mission(tools, fail)
        self.assertEqual(tools.actions[self.last_actions:], [("stop", "route_mission_finished")])

    def test_blocked_route_never_calls_grok(self):
        tools = Tools()
        tools.blocked = True
        with self.assertRaisesRegex(MissionError, "motion_blocked"):
            run_route_mission(tools, lambda _: self.fail("Grok must not be called"))
        self.assertEqual(tools.actions[-1][0], "stop")

    def test_missing_or_stalled_odometry_aborts(self):
        tools = Tools()
        tools.pose = lambda: (float("nan"), 0, 0)
        with self.assertRaisesRegex(MissionError, "invalid_pose"):
            run_route_mission(tools, self.pickup)
        tools = Tools()
        tools.stalled = True
        with self.assertRaisesRegex(MissionError, "no_progress"):
            run_route_mission(tools, self.pickup)

    def test_failed_turn_never_calls_grok(self):
        tools = Tools()
        tools.turn = lambda *a, **k: {"reached": False}
        with self.assertRaisesRegex(MissionError, "turn_incomplete"):
            run_route_mission(tools, lambda _: self.fail("unexpected model call"))

    def test_outbound_leg_holds_one_heading_across_pulses(self):
        tools = Tools()
        original = tools.drive
        references = []
        def drifting_drive(*args, **kwargs):
            reference = kwargs.get("reference_yaw_deg")
            references.append(reference)
            tools.yaw = tools.yaw + 1 if reference is None else reference + .1
            return original(*args, **kwargs)
        tools.drive = drifting_drive
        def pickup(recorded):
            self.assertTrue(all(ref is not None for ref in references))
            self.assertGreater(max(a[1] for a in tools.actions if a[0] == "drive"), .10)
            return {"result": "STOPPED_BY_GROK"}
        with self.assertRaisesRegex(MissionError, "pickup_incomplete"):
            run_route_mission(tools, pickup)

    def test_transport_must_be_enabled_before_outbound_motion(self):
        tools = Tools()
        tools.allow_transport = False
        with self.assertRaisesRegex(MissionError, "transport_disabled"):
            run_route_mission(tools, self.pickup)
        self.assertFalse(any(a[0] == "drive" for a in tools.actions))

    def test_failed_or_guarded_pickup_motion_aborts_without_return(self):
        tools = Tools()
        def pickup(recorded):
            tools.blocked = True
            recorded.dispatch("drive_chassis", {"x_mps": .1, "duration_s": 1})
            self.fail("guarded motion must raise")
        with self.assertRaisesRegex(MissionError, "motion_blocked"):
            run_route_mission(tools, pickup)


if __name__ == "__main__":
    unittest.main()
