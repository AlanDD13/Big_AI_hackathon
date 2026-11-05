"""Session/authority integration with a labelled fake P4; never moves hardware."""
import json
import asyncio
import unittest

from fastapi.testclient import TestClient
import httpx

from psycopg import Error as PostgresError

from backend.app import create_app
from backend.manual_proxy import ManualProxy, PostgresManualGate
from backend.manual_proxy import ManualFailure
from backend.tests.test_api import OPERATOR, PEER
from backend.tests.test_manual_requests import intent


class FakeBridge:
    def __init__(self):
        self.boot = "a" * 32
        self.stage = "IDLE"
        self.fence = True
        self.calls = []
        self.lose_ack = False
        self.fail_status = False
        self.caps = {"maxAbsXMps": 0.05, "maxAbsYMps": 0.05, "maxAbsZDegps": 5,
                     "watchdogTimeoutMs": 300, "maxIntentTtlMs": 500}

    def status(self):
        return {"roverId": "rover-1", "bridgeBootId": self.boot, "stage": self.stage,
                "telemetryFresh": True, "stationaryConfirmed": self.stage in {"IDLE", "STOPPED"},
                "caps": self.caps}

    def __call__(self, request):
        self.calls.append(request)
        assert request.headers["authorization"] == f"Bearer {PEER}"
        assert "cookie" not in request.headers
        body = json.loads(request.content) if request.content else None
        path = request.url.path
        if path == "/health":
            return httpx.Response(200, json={"manualBootFence": "bridgeBootId-v1" if self.fence else None,
                                            "bridgeBootId": self.boot, "executionMode": "simulator"})
        if path == "/manual/status":
            if self.fail_status:
                raise httpx.ReadTimeout("private source detail")
            assert request.url.params["roverId"] == "rover-1"
            return httpx.Response(200, json=self.status())
        if path in {"/manual/drive", "/manual/release", "/manual/rearm"} and body["bridgeBootId"] != self.boot:
            return httpx.Response(409, json={"error": {"code": "BRIDGE_BOOT_MISMATCH", "message": "private details"}})
        if path == "/manual/lease":
            self.stage = "MANUAL_IDLE"
            return httpx.Response(200, json={**self.status(), "leaseId": "lease-1", "leaseGeneration": 1,
                                            "issuedAt": 100.05, "expiresAt": 102.05})
        if path == "/manual/drive":
            self.stage = "MANUAL_DRIVING"
            if self.lose_ack:
                raise httpx.ReadTimeout("private lost ACK")
            return httpx.Response(200, json={**self.status(), "leaseId": "lease-1", "leaseGeneration": 1,
                                            "lastSequence": body["sequence"],
                                            "appliedVelocity": {key: body[key] for key in ["xMps", "yMps", "zDegps"]}})
        if path in {"/manual/release", "/stop"}:
            self.stage = "UNKNOWN"
            return httpx.Response(200, json={**self.status(), "motion": False,
                                            "released": path == "/manual/release", "zeroVelocityRequested": True,
                                            "stationarySamples": 0})
        if path == "/manual/rearm":
            self.stage = "IDLE"
            return httpx.Response(200, json={**self.status(), "rearmed": True, "motion": False})
        raise AssertionError(path)


class ManualProxyTests(unittest.TestCase):
    def setUp(self):
        self.bridge = FakeBridge()
        self.clock = {"now": 100.05}
        self.busy = False
        self.proxy = ManualProxy(url="https://bridge.invalid", peer_token=PEER,
                                 now=lambda: self.clock["now"], autonomous_active=lambda _: self.busy,
                                 transport=httpx.MockTransport(self.bridge))
        self.app = create_app(operator_token=OPERATOR, peer_token=PEER, cookie_secure=False,
                              now=lambda: self.clock["now"], manual_proxy=self.proxy)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.login()

    def login(self):
        response = self.client.post("/api/session", json={"token": OPERATOR})
        self.assertEqual(response.status_code, 204)
        return response.cookies["session"]

    def post(self, action, body, *, session=None):
        return self.client.post("/api/manual/" + action, json=body,
                                headers={"Cookie": f"session={session}"} if session else {})

    def lease(self):
        response = self.post("lease", {"roverId": "rover-1", "requestedTtlMs": 2000})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_auth_fixed_rover_request_validation_and_no_arbitrary_forwarding(self):
        self.client.cookies.clear()
        self.assertEqual(self.post("stop", {"roverId": "rover-1", "reason": "operator_stop"}).status_code, 401)
        self.assertEqual(self.client.get("/api/manual/status?roverId=rover-1").status_code, 401)
        self.login()
        malformed = self.client.post("/api/manual/drive", content=json.dumps(intent(xMps=float("nan"))),
                                     headers={"Content-Type": "application/json"})
        self.assertEqual(malformed.status_code, 422)
        self.assertEqual(self.post("lease", {"roverId": "other", "requestedTtlMs": 2000}).status_code, 404)
        self.assertEqual(self.post("drive", intent(xMps=0.4)).status_code, 422)
        self.assertEqual(self.client.get("/api/manual/status").status_code, 422)
        self.assertEqual(self.post("pickup", {}).status_code, 404)
        self.assertEqual(self.bridge.calls, [])

    def test_fenced_flow_release_unknown_rearm_and_stop(self):
        lease = self.lease()
        response = self.post("drive", intent(xMps=0.03))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(json.loads(self.bridge.calls[-1].content), intent(xMps=0.03))
        release = {"roverId": "rover-1", "leaseId": lease["leaseId"], "leaseGeneration": lease["leaseGeneration"],
                   "bridgeBootId": lease["bridgeBootId"], "reason": "blur"}
        self.assertEqual(self.post("release", release).json()["stage"], "UNKNOWN")
        self.assertEqual(self.post("lease", {"roverId": "rover-1", "requestedTtlMs": 2000}).json()["error"]["code"], "TELEMETRY_STALE")
        self.assertEqual(self.post("rearm", {"roverId": "rover-1", "bridgeBootId": self.bridge.boot}).json()["error"]["code"], "NOT_STOPPED")
        self.bridge.stage = "IDLE"
        self.lease()
        stopped = self.post("stop", {"roverId": "rover-1", "reason": "operator_stop"})
        self.assertEqual(stopped.json()["stage"], "UNKNOWN")
        self.assertEqual(self.bridge.calls[-1].url.path, "/stop")
        self.bridge.stage = "STOPPED"
        self.assertEqual(self.post("lease", {"roverId": "rover-1", "requestedTtlMs": 2000}).json()["error"]["code"], "STOP_LOCKED")
        response = self.post("rearm", {"roverId": "rover-1", "bridgeBootId": self.bridge.boot})
        self.assertEqual(response.json()["stage"], "IDLE")
        self.assertIsNone(self.proxy._lease)

    def test_session_cannot_steal_or_release_another_lease_but_can_stop(self):
        owner = self.client.cookies["session"]
        lease = self.lease()
        other = self.login()
        before = len(self.bridge.calls)
        self.assertEqual(self.post("drive", intent(xMps=0.03), session=other).json()["error"]["code"], "LEASE_HELD")
        self.assertEqual(self.post("release", {"roverId": "rover-1", "leaseId": lease["leaseId"],
                                               "leaseGeneration": 1, "bridgeBootId": self.bridge.boot,
                                               "reason": "user_release"}, session=other).status_code, 409)
        self.assertEqual(len(self.bridge.calls), before)
        self.assertEqual(self.post("drive", intent(xMps=0.03), session=owner).status_code, 200)
        self.assertEqual(self.post("stop", {"roverId": "rover-1", "reason": "operator_stop"}).status_code, 200)

    def test_lower_caps_expiry_and_unknown_autonomous_authority(self):
        self.lease()
        self.assertEqual(self.post("drive", intent(xMps=0.06)).status_code, 422)
        self.assertEqual(self.bridge.calls[-1].url.path, "/stop")
        self.busy = True
        self.assertEqual(self.post("drive", intent(xMps=0.03)).json()["error"]["code"], "AUTONOMOUS_ACTIVE")
        self.assertEqual(self.post("stop", {"roverId": "rover-1", "reason": "operator_stop"}).status_code, 200)
        self.assertEqual(self.bridge.calls[-1].url.path, "/stop")

    def test_stale_or_duplicate_intent_requests_stop_without_replaying_motion(self):
        self.lease()
        self.assertEqual(self.post("drive", intent(xMps=0.03)).status_code, 200)
        duplicate = self.post("drive", intent(xMps=0.03))
        self.assertEqual(duplicate.json()["error"]["code"], "STALE_INTENT")
        self.assertEqual(self.bridge.calls[-1].url.path, "/stop")
        self.assertEqual(sum(call.url.path == "/manual/drive" for call in self.bridge.calls), 1)
        self.bridge.stage = "STOPPED"
        self.assertEqual(self.post("rearm", {"roverId": "rover-1", "bridgeBootId": self.bridge.boot}).status_code, 200)
        self.lease()
        expired = self.post("drive", intent(xMps=0.03, expiresAt=100.04))
        self.assertEqual(expired.status_code, 409)
        self.assertEqual(self.bridge.calls[-1].url.path, "/stop")

    def test_lost_ack_does_not_retry_or_reuse_authority(self):
        self.lease()
        self.bridge.lose_ack = True
        response = self.post("drive", intent(xMps=0.03))
        self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "MANUAL_UNAVAILABLE"))
        self.assertNotIn("private", response.text)
        self.post("drive", intent(xMps=0.03, sequence=2))
        self.assertEqual(sum(call.url.path == "/manual/drive" for call in self.bridge.calls), 1)
        self.assertTrue(self.proxy._unknown)

    def test_restart_fence_prevents_reused_lease_from_driving(self):
        self.lease()
        self.bridge.boot = "b" * 32
        self.bridge.stage = "IDLE"
        response = self.post("drive", intent(xMps=0.03))
        self.assertEqual(response.json()["error"]["code"], "BRIDGE_BOOT_MISMATCH")
        self.assertIsNone(self.proxy._lease)
        status = self.client.get("/api/manual/status?roverId=rover-1")
        self.assertEqual(status.json()["bridgeBootId"], self.bridge.boot)
        self.assertEqual(self.post("drive", intent(xMps=0.03, bridgeBootId=self.bridge.boot)).json()["error"]["code"], "LEASE_HELD")

    def test_old_bridge_and_missing_storage_block_lease(self):
        self.bridge.fence = False
        self.assertEqual(self.post("lease", {"roverId": "rover-1", "requestedTtlMs": 2000}).json()["error"]["code"], "MANUAL_UNAVAILABLE")
        self.assertEqual([call.url.path for call in self.bridge.calls], ["/health"])
        self.proxy._autonomous_active = None
        self.assertEqual(self.post("lease", {"roverId": "rover-1", "requestedTtlMs": 2000}).json()["error"]["code"], "STORAGE_UNAVAILABLE")

    def test_logout_and_expiry_invalidate_authority_and_request_stop(self):
        self.lease()
        self.assertEqual(self.client.delete("/api/session").status_code, 204)
        self.assertEqual(self.bridge.calls[-1].url.path, "/stop")
        self.assertIsNone(self.proxy._lease)
        self.bridge.stage = "STOPPED"
        self.login()
        self.assertEqual(self.post("rearm", {"roverId": "rover-1", "bridgeBootId": self.bridge.boot}).status_code, 200)
        self.lease()
        self.clock["now"] += 8 * 3600
        self.assertEqual(self.post("drive", intent(xMps=0.03)).status_code, 401)
        self.assertEqual(self.bridge.calls[-1].url.path, "/stop")

    def test_continuous_acked_driving_renews_the_lease_beyond_initial_ttl(self):
        self.lease()
        for sequence in range(1, 31):
            self.clock["now"] += 0.1
            now = self.clock["now"]
            response = self.post("drive", intent(xMps=0.03, sequence=sequence,
                                                issuedAt=now - 0.01, expiresAt=now + 0.2))
            self.assertEqual(response.status_code, 200, response.text)

    def test_gate_delay_drops_the_expired_tick_without_stopping_the_lease(self):
        self.lease()
        drives = sum(call.url.path == "/manual/drive" for call in self.bridge.calls)

        def slow(_rover):
            self.clock["now"] = 100.25
            return False

        self.proxy._autonomous_active = slow
        expired = self.post("drive", intent(xMps=0.03))
        self.assertEqual(expired.json()["error"]["code"], "INTENT_EXPIRED")
        self.assertEqual(sum(call.url.path == "/manual/drive" for call in self.bridge.calls), drives)
        self.assertIsNotNone(self.proxy._lease)
        self.assertFalse(self.proxy._stop_latched)
        self.proxy._autonomous_active = lambda _rover: False
        self.clock["now"] = 100.30
        continued = self.post("drive", intent(xMps=0.03, sequence=2, issuedAt=100.30, expiresAt=100.50))
        self.assertEqual(continued.status_code, 200, continued.text)
        replay = self.post("drive", intent(xMps=0.03, sequence=1, issuedAt=100.30, expiresAt=100.50))
        self.assertEqual(replay.json()["error"]["code"], "STALE_INTENT")
        self.assertEqual(self.bridge.calls[-1].url.path, "/stop")


class ManualConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_priority_stop_is_not_undone_by_inflight_rearm(self):
        bridge = FakeBridge()
        bridge.stage = "STOPPED"
        rearm_started, resume = asyncio.Event(), asyncio.Event()
        async def handler(request):
            if request.url.path == "/manual/rearm":
                rearm_started.set()
                await resume.wait()
            return bridge(request)
        proxy = ManualProxy(url="https://bridge.invalid", peer_token=PEER, now=lambda: 100.05,
                            autonomous_active=lambda _: False, transport=httpx.MockTransport(handler))
        rearm = asyncio.create_task(proxy.execute("rearm", "session", {"roverId": "rover-1", "bridgeBootId": bridge.boot}, lambda: True))
        await asyncio.wait_for(rearm_started.wait(), 1)
        stopped = await proxy.execute("stop", "session", {"roverId": "rover-1", "reason": "operator_stop"}, lambda: True)
        self.assertEqual(stopped["stage"], "UNKNOWN")
        resume.set()
        with self.assertRaises(ManualFailure):
            await rearm
        self.assertEqual(bridge.stage, "UNKNOWN")  # Compensating stop after late rearm.
        self.assertTrue(proxy._stop_latched)
        bridge.stage = "IDLE"  # Even a later idle report cannot clear backend's stop latch.
        with self.assertRaises(ManualFailure) as blocked:
            await proxy.execute("lease", "session", {"roverId": "rover-1", "requestedTtlMs": 2000}, lambda: True)
        self.assertEqual(blocked.exception.code, "STOP_LOCKED")

    async def test_expiry_during_status_prevents_acquiring_a_lease(self):
        bridge = FakeBridge()
        authorized = {"value": True}
        async def handler(request):
            if request.url.path == "/manual/status":
                authorized["value"] = False
            return bridge(request)
        proxy = ManualProxy(url="https://bridge.invalid", peer_token=PEER, now=lambda: 100.05,
                            autonomous_active=lambda _: False, transport=httpx.MockTransport(handler))
        with self.assertRaises(ManualFailure) as rejected:
            await proxy.execute("lease", "session", {"roverId": "rover-1", "requestedTtlMs": 2000}, lambda: authorized["value"])
        self.assertEqual(rejected.exception.code, "UNAUTHORIZED")
        self.assertFalse(any(call.url.path == "/manual/lease" for call in bridge.calls))


class PostgresManualGateReuseTests(unittest.TestCase):
    def test_reuses_one_connection_and_rereads_every_call(self):
        opened = []

        class Connection:
            def __init__(self, fail):
                self.fail = fail
                self.closed = False
                self.queries = 0
                self.query = ""

            def execute(self, query, params):
                if self.fail:
                    raise PostgresError("connection dropped")
                self.queries += 1
                self.query = query
                self.params = params
                return self

            def fetchone(self):
                return (self.queries > 1,)

            def rollback(self):
                return None

            def close(self):
                self.closed = True

        def connect():
            connection = Connection(fail=not opened)
            opened.append(connection)
            return connection

        gate = PostgresManualGate(connect)
        self.assertFalse(gate("rover-1"))
        self.assertTrue(gate("rover-1"))
        self.assertEqual(len(opened), 2)
        self.assertTrue(opened[0].closed)
        self.assertEqual(opened[1].queries, 2)
        self.assertEqual(opened[1].params, ("rover-1",))
        self.assertIn("dispatching", opened[1].query)
        self.assertIn("unknown", opened[1].query)


if __name__ == "__main__":
    unittest.main()
