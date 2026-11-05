"""Opt-in compatibility with a P4 checkout's fake-output HTTP server only."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest

from fastapi.testclient import TestClient

# Resolve the backend under test before the peer test helper adjusts sys.path.
from backend.app import create_app
from backend.manual_proxy import ManualProxy


P4_ROOT = os.environ.get("BACKEND_P4_FIXTURE_ROOT")


@unittest.skipUnless(P4_ROOT, "requires an explicit P4 checkout for fake-output loopback HTTP")
class ManualBridgeCompatibilityTests(unittest.TestCase):
    def test_p4_fenced_http_manual_lifecycle_uses_only_fake_output(self):
        root = Path(P4_ROOT).resolve()
        original_path = list(sys.path)
        try:
            sys.path.insert(0, str(root / "robot"))
            spec = importlib.util.spec_from_file_location("p4_offline_bridge_tests", root / "robot/tests/test_bridge.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path[:] = original_path
        fixture = module.BridgeTests()
        bridge, clock, writes = fixture._make_bridge()
        self.assertFalse(bridge.physical)
        self.assertIsInstance(writes, list)  # The fixture records tuples, not SDK output.
        fixture._ready(bridge, clock)
        server = module.BridgeHTTPServer(bridge, host="127.0.0.1", port=0, peer_token="fixture-peer-value")
        base = server.start()
        self.addCleanup(server.close)
        proxy = ManualProxy(url=base, peer_token="fixture-peer-value", now=lambda: clock.wall,
                            autonomous_active=lambda _: False)
        app = create_app(operator_token="fixture-operator-value", peer_token="fixture-peer-value",
                         cookie_secure=False, now=lambda: clock.wall, manual_proxy=proxy)
        with TestClient(app) as client:
            self.assertEqual(client.post("/api/session", json={"token": "fixture-operator-value"}).status_code, 204)
            status = client.get("/api/manual/status?roverId=rover-1")
            self.assertEqual(status.status_code, 200, status.text)
            self.assertEqual(status.json()["executionMode"], "simulator")
            lease = client.post("/api/manual/lease", json={"roverId": "rover-1", "requestedTtlMs": 2000})
            self.assertEqual(lease.status_code, 200, lease.text)
            binding = {key: lease.json()[key] for key in ["roverId", "leaseId", "leaseGeneration", "bridgeBootId"]}
            drive = client.post("/api/manual/drive", json={**binding, "sequence": 1, "issuedAt": clock.wall,
                                "expiresAt": clock.wall + .25, "xMps": .02, "yMps": 0, "zDegps": 0, "frame": "BODY"})
            self.assertEqual(drive.status_code, 200, drive.text)
            self.assertEqual(writes[-1], (.02, 0, 0))
            release = client.post("/api/manual/release", json={**binding, "reason": "user_release"})
            self.assertEqual(release.status_code, 200, release.text)
            self.assertEqual(release.json()["stage"], "UNKNOWN")
            fixture._confirm(bridge, clock, 100)
            self.assertEqual(client.get("/api/manual/status?roverId=rover-1").json()["stage"], "IDLE")
            stop = client.post("/api/manual/stop", json={"roverId": "rover-1", "reason": "operator_stop"})
            self.assertEqual(stop.status_code, 200, stop.text)
            fixture._confirm(bridge, clock, 200)
            rearm = client.post("/api/manual/rearm", json={"roverId": "rover-1", "bridgeBootId": bridge.boot_id})
            self.assertEqual(rearm.status_code, 200, rearm.text)
            self.assertEqual(rearm.json()["stage"], "IDLE")
            self.assertFalse(rearm.json()["motion"])
            self.assertEqual(writes[-1], (0, 0, 0))
