"""Windows-like slow TLS/client initialization must not consume motion budget."""
import asyncio
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.manual_proxy import ManualFailure, ManualProxy
from backend.tests.test_manual_proxy import FakeBridge
from backend.tests.test_api import OPERATOR, PEER


class ManualClientLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_initialization_is_outside_budget_and_client_is_reused(self):
        original = httpx.AsyncClient
        clients = []
        calls = []
        async def handler(request):
            calls.append(request)
            await asyncio.sleep(0.01)
            return httpx.Response(200, json={"ok": True})
        def slow_client(**kwargs):
            time.sleep(0.35)
            client = original(**kwargs)
            clients.append(client)
            return client
        proxy = ManualProxy(url="https://fixture.invalid", peer_token=PEER, now=time.time,
                            transport=httpx.MockTransport(handler))
        try:
            with patch("backend.manual_proxy.httpx.AsyncClient", slow_client):
                self.assertEqual(await proxy._call("GET", "/health"), {"ok": True})
                self.assertEqual(await proxy._call("GET", "/health"), {"ok": True})
            self.assertEqual(len(clients), 1)
            self.assertEqual(len(calls), 2)
        finally:
            await proxy.close()
        self.assertTrue(clients[0].is_closed)

    async def test_slow_upstream_still_times_out_without_retry(self):
        calls = []
        async def handler(request):
            calls.append(request)
            await asyncio.sleep(1)
            return httpx.Response(200, json={})
        proxy = ManualProxy(url="https://fixture.invalid", peer_token=PEER, now=time.time,
                            transport=httpx.MockTransport(handler))
        await proxy.start()
        try:
            with self.assertRaises(ManualFailure):
                await proxy._call("GET", "/health")
            self.assertEqual(len(calls), 1)
        finally:
            await proxy.close()


class ManualAppLifetimeTests(unittest.TestCase):
    def test_app_startup_initializes_client_and_shutdown_closes_it(self):
        proxy = ManualProxy(url="https://fixture.invalid", peer_token=PEER, now=lambda: 100.05,
                            transport=httpx.MockTransport(FakeBridge()))
        app = create_app(operator_token=OPERATOR, peer_token=PEER, cookie_secure=False, manual_proxy=proxy)
        with TestClient(app) as client:
            upstream = proxy._client
            self.assertIsNotNone(upstream)
            client.post("/api/session", json={"token": OPERATOR})
            self.assertEqual(client.get("/api/manual/status?roverId=rover-1").status_code, 200)
            self.assertIs(proxy._client, upstream)
        self.assertTrue(upstream.is_closed)
