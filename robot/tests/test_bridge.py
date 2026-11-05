from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import BridgeHTTPServer, PhysicalBridgeRuntime, PhysicalChassisAdapter, RobotBridge
from camera import RoverFrameBuffer
from safety import SafetyController, SafetyError


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "contracts" / "fixtures"


class FakeClock:
    def __init__(self, wall: float = 1793797200.0, mono: float = 100.0) -> None:
        self.wall = wall
        self.mono = mono

    def advance(self, dt: float) -> None:
        self.wall += dt
        self.mono += dt


def http_call(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = "test-peer-token",
    body: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    url = base_url.rstrip("/") + path
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=3.0) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw)
    except HTTPError as exc:
        raw = exc.read().decode("utf-8")
        return exc.code, json.loads(raw)


class BridgeTests(unittest.TestCase):
    def _ready(self, bridge, clk, start_sample=1):
        bridge.controller.stop("STARTUP")
        self._confirm(bridge, clk, start_sample)
        bridge.controller.rearm()

    def _confirm(self, bridge, clk, start_sample=10):
        for sample in range(start_sample, start_sample + 3):
            clk.advance(0.11)
            bridge.controller.heartbeat()
            bridge.controller.observe(0, 0, 0, sample_id=sample)

    def _binding(self, clk):
        return dict(commandId="cmd-test", missionId="mission-1",
                    objectId="phone-1:session1:track1", targetRevision=1,
                    confirmationEvidenceId="evidence-1", expiresAt=clk.wall + 2)

    def test_physical_stop_stays_unknown_until_confirmed_and_rearm_never_resumes(self):
        bridge, clk, writes = self._make_bridge()
        bridge.physical = True
        self._ready(bridge, clk)
        bridge.seed_command("cmd-test", stage="PICKING")
        status, body = bridge.stop_command("cmd-test", {"missionId": "mission-1"})
        self.assertEqual((status, body["stage"]), (200, "UNKNOWN"))
        self.assertEqual(bridge.get_command("cmd-test")[1]["stage"], "UNKNOWN")
        status, body = bridge.rearm_command("cmd-test", {"missionId": "mission-1"})
        self.assertEqual((status, body["error"]["code"]), (409, "NOT_STOPPED"))
        self._confirm(bridge, clk)
        self.assertEqual(bridge.get_command("cmd-test")[1]["stage"], "STOPPED")
        before = len(writes)
        status, body = bridge.rearm_command("cmd-test", {"missionId": "mission-1"})
        self.assertEqual((status, body["stage"], body["rearmed"]), (200, "STOPPED", True))
        self.assertEqual(len(writes), before)
        self.assertEqual(bridge.rearm_command("cmd-test", {"missionId": "mission-1"})[0], 200)

    def test_command_rearm_does_not_swallow_safety_rejection(self):
        bridge, clk, _ = self._make_bridge()
        bridge.physical = True
        self._ready(bridge, clk)
        bridge.seed_command("cmd-test", stage="PICKING")
        bridge.stop_command("cmd-test", {"missionId": "mission-1"})
        self._confirm(bridge, clk)
        def reject():
            raise SafetyError("HEARTBEAT_LOST")
        bridge.controller.rearm = reject
        status, body = bridge.rearm_command("cmd-test", {"missionId": "mission-1"})
        self.assertEqual((status, body["error"]["code"]), (409, "NOT_STOPPED"))
        self.assertFalse(body["rearmed"])

    def test_emergency_stop_preserves_unknown_command_outcome(self):
        bridge, clk, _ = self._make_bridge()
        bridge.physical = True
        self._ready(bridge, clk)
        bridge.seed_command("cmd-test", stage="PICKING")
        bridge.emergency_stop()
        self.assertEqual(bridge.get_command("cmd-test")[1]["stage"], "UNKNOWN")

    def test_pickup_blocked_until_clean_manual_release_is_confirmed(self):
        bridge, clk, _ = self._make_bridge()
        self._ready(bridge, clk)
        self.assertEqual(bridge.manual_lease({})[0], 200)
        bridge.manual_release()
        status, body = bridge.pickup(self._binding(clk))
        self.assertEqual((status, body["error"]["code"]), (503, "TELEMETRY_STALE"))
        self.assertEqual(bridge.get_command("cmd-test")[0], 404)
        self._confirm(bridge, clk)
        self.assertEqual(bridge.pickup(self._binding(clk))[0], 200)

    def test_pickup_blocked_during_manual_stop_and_after_confirmed_fault(self):
        bridge, clk, _ = self._make_bridge()
        self._ready(bridge, clk)
        bridge.manual_lease({})
        bridge.emergency_stop()
        self.assertEqual(bridge.pickup(self._binding(clk))[0], 503)
        self._confirm(bridge, clk)
        status, body = bridge.pickup(self._binding(clk))
        self.assertEqual((status, body["error"]["code"]), (409, "STOP_LOCKED"))

    def test_pickup_cannot_race_through_release_before_core_latch(self):
        bridge, clk, _ = self._make_bridge()
        self._ready(bridge, clk)
        bridge.manual_lease({})
        entered, proceed, completed = threading.Event(), threading.Event(), threading.Event()
        release = bridge.controller.release
        result = []
        def delayed_release(reason):
            entered.set()
            if not proceed.wait(1):
                raise RuntimeError("test release timeout")
            return release(reason)
        bridge.controller.release = delayed_release
        release_thread = threading.Thread(target=bridge.manual_release)
        def pickup():
            result.append(bridge.pickup(self._binding(clk)))
            completed.set()
        pickup_thread = threading.Thread(target=pickup)
        try:
            release_thread.start()
            self.assertTrue(entered.wait(1))
            pickup_thread.start()
            self.assertFalse(completed.wait(0.03))
        finally:
            proceed.set()
            release_thread.join(1)
            if pickup_thread.ident is not None:
                pickup_thread.join(1)
        self.assertFalse(release_thread.is_alive())
        self.assertFalse(pickup_thread.is_alive())
        self.assertEqual(result[0][0], 503)
        self.assertEqual(result[0][1]["error"]["code"], "TELEMETRY_STALE")

    def test_physical_pickup_refuses_stale_telemetry(self):
        bridge, clk, _ = self._make_bridge()
        bridge.physical = True
        self._ready(bridge, clk)
        clk.advance(0.6)
        self.assertEqual(bridge.pickup(self._binding(clk))[0], 503)

    def _make_bridge(self, clock: FakeClock | None = None) -> tuple[RobotBridge, FakeClock, list[tuple[float, float, float]]]:
        clk = clock or FakeClock()
        writes: list[tuple[float, float, float]] = []

        def write_fn(x: float, y: float, yaw: float) -> bool:
            writes.append((float(x), float(y), float(yaw)))
            return True

        ctrl = SafetyController(
            write_fn,
            clock=lambda: clk.mono,
            wall=lambda: clk.wall,
        )
        buf = RoverFrameBuffer(
            "rover-1",
            wall_clock=lambda: clk.wall,
            monotonic_clock=lambda: clk.mono,
        )
        bridge = RobotBridge(
            ctrl,
            buf,
            wall_clock=lambda: clk.wall,
            monotonic_clock=lambda: clk.mono,
        )
        return bridge, clk, writes

    def test_all_normative_robot_simulator_fixture_cases(self) -> None:
        fixture = json.loads((FIXTURE_DIR / "robot-simulator.json").read_text())
        default_binding = fixture["binding"]

        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                bridge, clk, _ = self._make_bridge()
                for step in case["steps"]:
                    if "now" in step:
                        clk.wall = float(step["now"])
                    method = step["method"]
                    path = step["path"]
                    body = dict(default_binding) if step.get("bodyRef") == "binding" else step.get("body")

                    if method == "GET" and path.startswith("/commands/"):
                        cid = path.split("/")[2]
                        status, resp = bridge.get_command(cid)
                    elif method == "POST" and path == "/commands/pickup":
                        status, resp = bridge.pickup(body or {}, now_override=clk.wall)
                    elif method == "POST" and path.endswith("/hold"):
                        cid = path.split("/")[2]
                        status, resp = bridge.hold_command(cid, body or {})
                    elif method == "POST" and path.endswith("/stop"):
                        cid = path.split("/")[2]
                        status, resp = bridge.stop_command(cid, body or {})
                    elif method == "POST" and path.endswith("/rearm"):
                        cid = path.split("/")[2]
                        status, resp = bridge.rearm_command(cid, body or {})
                    else:
                        self.fail(f"Unhandled fixture step: {step}")

                    if "status" in step:
                        self.assertEqual(status, step["status"])
                    if "stage" in step:
                        self.assertEqual(resp.get("stage"), step["stage"])
                    if "error" in step:
                        self.assertEqual(resp.get("error", {}).get("code"), step["error"])
                    if "statusRevision" in step:
                        self.assertEqual(resp.get("statusRevision"), step["statusRevision"])
                    if "acted" in step:
                        self.assertEqual(resp.get("acted"), step["acted"])
                    if "rearmed" in step:
                        self.assertEqual(resp.get("rearmed"), step["rearmed"])
                    if "motion" in step:
                        self.assertEqual(resp.get("motion"), step["motion"])

    def test_restart_invalidates_prior_commands_with_404_unknown_command(self) -> None:
        from backend.bridge_client import interpret_bridge_response

        bridge, clk, _ = self._make_bridge()
        binding = {
            "commandId": "cmd-restart-1",
            "missionId": "mission-1",
            "objectId": "phone-1:session1:track1",
            "targetRevision": 3,
            "confirmationEvidenceId": "evidence-1",
            "expiresAt": clk.wall + 2.0,
        }
        status, resp = bridge.pickup(binding)
        self.assertEqual(status, 200)
        old_boot = bridge.boot_id

        # Simulate bridge process restart
        new_ctrl = SafetyController(
            lambda x, y, yaw: True,
            clock=lambda: clk.mono,
            wall=lambda: clk.wall,
        )
        new_boot = bridge.restart_InMemory_state(new_ctrl)
        self.assertNotEqual(old_boot, new_boot)

        # GET /commands/cmd-restart-1 after restart returns 404 UNKNOWN_COMMAND
        g_status, g_body = bridge.get_command("cmd-restart-1")
        self.assertEqual(g_status, 404)
        self.assertEqual(g_body["error"]["code"], "UNKNOWN_COMMAND")

        # Verify backend bridge_client reconciles 404 UNKNOWN_COMMAND with reuse_command=False
        interp = interpret_bridge_response(g_status, g_body)
        self.assertFalse(interp.reuse_command)
        self.assertEqual(interp.observation.state, "unknown")

        # Retrying pickup with the retired commandId also returns 404 UNKNOWN_COMMAND without creating it
        p_status, p_body = bridge.pickup(binding)
        self.assertEqual(p_status, 404)
        self.assertEqual(p_body["error"]["code"], "UNKNOWN_COMMAND")

    def test_manual_control_http_lifecycle_and_safety_gates(self) -> None:
        bridge, clk, writes = self._make_bridge()
        token = "peer-token-xyz"
        server = BridgeHTTPServer(bridge, host="127.0.0.1", port=0, peer_token=token)
        base_url = server.start()
        self.addCleanup(server.close)

        # 1. Startup stage is UNKNOWN -> lease/drive return 503 TELEMETRY_STALE, rearm returns 409 NOT_STOPPED
        st, body = http_call(base_url, "POST", "/manual/lease", token=token, body={"roverId": "rover-1", "requestedTtlMs": 2000})
        self.assertEqual(st, 503)
        self.assertEqual(body["error"]["code"], "TELEMETRY_STALE")

        st, body = http_call(base_url, "POST", "/manual/rearm", token=token, body={"roverId": "rover-1"})
        self.assertEqual(st, 409)
        self.assertEqual(body["error"]["code"], "NOT_STOPPED")

        # 2. Confirm 3 stationary samples spanning >= 200 ms -> latches STOPPED
        bridge.controller.tick()
        for i in range(1, 4):
            clk.advance(0.11)
            bridge.controller.heartbeat()
            bridge.controller.observe(0.0, 0.0, 0.0, sample_id=i)
        bridge.observe_pose_timestamp(clk.wall)

        st, body = http_call(base_url, "POST", "/manual/lease", token=token, body={"roverId": "rover-1", "requestedTtlMs": 2000})
        self.assertEqual(st, 409)
        self.assertEqual(body["error"]["code"], "STOP_LOCKED")

        # 3. Explicit rearm transitions STOPPED -> IDLE with motion=False
        st, body = http_call(base_url, "POST", "/manual/rearm", token=token, body={"roverId": "rover-1"})
        self.assertEqual(st, 200)
        self.assertEqual(body["stage"], "IDLE")
        self.assertFalse(body["motion"])
        self.assertTrue(body["rearmed"])

        # 4. Acquire lease -> 200 OK; second lease while active -> 409 LEASE_HELD
        st, lease = http_call(base_url, "POST", "/manual/lease", token=token, body={"roverId": "rover-1", "requestedTtlMs": 2000})
        self.assertEqual(st, 200)
        self.assertEqual(lease["mode"], "MANUAL_CHASSIS_SLICE_1")
        self.assertEqual(lease["caps"]["watchdogTimeoutMs"], 300)

        st2, dup_lease = http_call(base_url, "POST", "/manual/lease", token=token, body={"roverId": "rover-1", "requestedTtlMs": 2000})
        self.assertEqual(st2, 409)
        self.assertEqual(dup_lease["error"]["code"], "LEASE_HELD")

        # 5. Autonomous pickup is blocked with 409 MANUAL_LEASE_ACTIVE while manual lease is active
        st_pick, pick_err = http_call(
            base_url,
            "POST",
            "/commands/pickup",
            token=token,
            body={
                "commandId": "cmd-m1",
                "missionId": "mission-1",
                "objectId": "rover-1:sess1:trk1",
                "targetRevision": 1,
                "confirmationEvidenceId": "ev-1",
                "expiresAt": clk.wall + 2.0,
            },
        )
        self.assertEqual(st_pick, 409)
        self.assertEqual(pick_err["error"]["code"], "MANUAL_LEASE_ACTIVE")

        # 6. Valid manual drive within caps -> 200 OK MANUAL_DRIVING
        st_drv, drv = http_call(
            base_url,
            "POST",
            "/manual/drive",
            token=token,
            body={
                "roverId": "rover-1",
                "leaseId": lease["leaseId"],
                "leaseGeneration": lease["leaseGeneration"],
                "sequence": 1,
                "issuedAt": clk.wall,
                "expiresAt": clk.wall + 0.25,
                "xMps": 0.2,
                "yMps": 0.0,
                "zDegps": 0.0,
                "frame": "BODY",
            },
        )
        self.assertEqual(st_drv, 200)
        self.assertEqual(drv["stage"], "MANUAL_DRIVING")
        self.assertEqual(drv["appliedVelocity"]["xMps"], 0.2)

        # 7. Voluntary release -> immediate 200 OK stage UNKNOWN, then transitions UNKNOWN -> IDLE after 3 stationary samples
        st_rel, rel = http_call(
            base_url,
            "POST",
            "/manual/release",
            token=token,
            body={
                "roverId": "rover-1",
                "leaseId": lease["leaseId"],
                "leaseGeneration": lease["leaseGeneration"],
                "reason": "user_release",
            },
        )
        self.assertEqual(st_rel, 200)
        self.assertEqual(rel["stage"], "UNKNOWN")
        self.assertFalse(rel["stationaryConfirmed"])
        self.assertFalse(rel["motion"])

        for i in range(10, 13):
            clk.advance(0.11)
            bridge.controller.heartbeat()
            bridge.controller.observe(0.0, 0.0, 0.0, sample_id=i)
        st_stat, m_stat = http_call(base_url, "GET", "/manual/status?roverId=rover-1", token=token)
        self.assertEqual(st_stat, 200)
        self.assertEqual(m_stat["stage"], "IDLE")

        # 8. Out-of-range velocity (> 0.35 m/s) fails closed with 422 VALIDATION_ERROR and revokes lease
        st, lease2 = http_call(base_url, "POST", "/manual/lease", token=token, body={"roverId": "rover-1", "requestedTtlMs": 2000})
        self.assertEqual(st, 200)
        st_bad, bad_resp = http_call(
            base_url,
            "POST",
            "/manual/drive",
            token=token,
            body={
                "roverId": "rover-1",
                "leaseId": lease2["leaseId"],
                "leaseGeneration": lease2["leaseGeneration"],
                "sequence": 1,
                "issuedAt": clk.wall,
                "expiresAt": clk.wall + 0.25,
                "xMps": 0.9,
                "yMps": 0.0,
                "zDegps": 0.0,
                "frame": "BODY",
            },
        )
        self.assertEqual(st_bad, 422)
        self.assertEqual(bad_resp["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(writes[-1], (0.0, 0.0, 0.0))


class RecordingModule:
    """Offline SDK-shaped fixture; no connection or physical outputs."""

    def __init__(self):
        self.callbacks = {}
        self.calls = []
        self._auto_timer = None
        self.moved = False
        self.zero_after_motion = threading.Event()
        self.reject_subscription = None

    def __getattr__(self, name):
        if name.startswith("sub_"):
            def subscribe(*, freq, callback):
                if name == self.reject_subscription:
                    return False
                self.callbacks[name[4:]] = callback
                return True
            return subscribe
        if name.startswith("unsub_"):
            return lambda: self.callbacks.pop(name[6:], None)
        raise AttributeError(name)

    def drive_speed(self, **kwargs):
        self.calls.append(kwargs)
        if any(kwargs[k] for k in ("x", "y", "z")):
            self.moved = True
        elif self.moved:
            self.zero_after_motion.set()
        return False  # Actual speed protocol has no acknowledgement.


class RecordingRover:
    def __init__(self):
        self.chassis = RecordingModule()
        self.battery = RecordingModule()


class PhysicalWiringTests(unittest.TestCase):
    def test_adapter_supports_bridge_caps_and_yaw_not_bench_caps(self):
        rover = RecordingRover()
        adapter = PhysicalChassisAdapter(rover)
        adapter(0.2, -0.1, 20)
        self.assertEqual(rover.chassis.calls[-1], dict(x=0.2, y=-0.1, z=20, timeout=1.0))
        for velocity in ((0.36, 0, 0), (0, -0.36, 0), (0, 0, 46), (float("nan"), 0, 0)):
            with self.assertRaises(ValueError):
                adapter(*velocity)
        self.assertEqual(len(rover.chassis.calls), 1)

    def test_zero_cancels_timer_and_does_not_spawn_a_new_timer(self):
        rover = RecordingRover()
        class Timer:
            cancelled = False
            def is_alive(self):
                return True
            def cancel(self):
                self.cancelled = True
        timer = rover.chassis._auto_timer = Timer()
        PhysicalChassisAdapter(rover)(0, 0, 0)
        self.assertTrue(timer.cancelled)
        self.assertEqual(rover.chassis.calls[-1], dict(x=0.0, y=0.0, z=0.0))

    def test_physical_startup_subscriptions_and_silence_without_http_polling(self):
        rover = RecordingRover()
        runtime = PhysicalBridgeRuntime(rover)
        runtime.start()
        try:
            self.assertTrue(runtime.bridge.physical)
            for key in ("position", "attitude", "velocity"):
                self.assertIn(key, rover.chassis.callbacks)
            rover.battery.callbacks["battery_info"](80)
            # Exercise actual coherent telemetry adapter, not controller injection.
            until = time.monotonic() + 2
            while time.monotonic() < until:
                rover.chassis.callbacks["position"]((0, 0, 0))
                rover.chassis.callbacks["attitude"]((0, 0, 0))
                rover.chassis.callbacks["velocity"]((0, 0, 0, 0, 0, 0))
                if runtime.controller.status()["stopConfirmed"]:
                    break
                time.sleep(0.11)
            status = runtime.controller.status()
            self.assertTrue(status["stopConfirmed"])
            self.assertTrue(status["heartbeatFresh"])
            self.assertIsNotNone(runtime.bridge._last_pose_received_at)
            self.assertTrue(status["watchdogAlive"])
            self.assertEqual(runtime.bridge.manual_rearm()[0], 200)
            code, lease = runtime.bridge.manual_lease({})
            self.assertEqual(code, 200)
            now = time.time()
            code, body = runtime.bridge.manual_drive(dict(
                leaseId=lease["leaseId"], leaseGeneration=lease["leaseGeneration"],
                sequence=1, issuedAt=now, expiresAt=now + 0.5,
                xMps=0.2, yMps=0, zDegps=0, frame="BODY"))
            self.assertEqual(code, 200, body)
            self.assertTrue(rover.chassis.moved)
            # No HTTP call, status tick, heartbeat or telemetry after drive.
            self.assertTrue(rover.chassis.zero_after_motion.wait(0.35))
            self.assertEqual(runtime.controller.status()["reason"], "INPUT_SILENCE")
            self.assertEqual(runtime.controller.status()["stage"], "UNKNOWN")
        finally:
            runtime.close()
        self.assertFalse(runtime.controller.status()["watchdogAlive"])
        self.assertFalse(rover.chassis.callbacks)
        self.assertFalse(rover.battery.callbacks)

    def test_partial_subscription_failure_cleans_up(self):
        rover = RecordingRover()
        rover.chassis.reject_subscription = "sub_attitude"
        runtime = PhysicalBridgeRuntime(rover)
        with self.assertRaisesRegex(RuntimeError, "subscription rejected"):
            runtime.start()
        self.assertFalse(rover.chassis.callbacks)
        self.assertFalse(runtime.controller.status()["watchdogAlive"])


if __name__ == "__main__":
    unittest.main()
