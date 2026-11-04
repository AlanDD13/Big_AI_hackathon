from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from bridge import BridgeHTTPServer, RobotBridge
from camera import RoverFrameBuffer
from safety import SafetyController


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


if __name__ == "__main__":
    unittest.main()
