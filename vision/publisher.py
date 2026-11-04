"""Bounded peer delivery. This is not the backend's durable database/outbox."""
import asyncio
from urllib.parse import urlsplit

import httpx


class Publisher:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client
        self.queue = asyncio.Queue(maxsize=256)
        self.failed = 0
        self.delivered = 0
        self.last_error = None
        self.closed = False
        for url in (settings.mapping_url, settings.backend_url):
            if url:
                parsed = urlsplit(url)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError("Peer URL must be an operator-configured HTTP(S) base URL")

    def destination(self, event):
        if event.type == "frame.detections":
            return self.settings.mapping_url, "/detections"
        if event.type == "object.observed":
            return self.settings.backend_url, "/api/detections"
        if event.type == "object.classified":
            return self.settings.backend_url, "/api/classifications"
        return self.settings.backend_url, "/api/vision-events"

    def enqueue(self, event):
        if not self.destination(event)[0]:
            return
        if self.queue.full():
            self.failed += 1
            self.last_error = "Delivery queue full; consumer must reconcile from snapshot"
            return
        self.queue.put_nowait(event)

    async def deliver(self, event, client):
        base, path = self.destination(event)
        headers = {"Authorization": f"Bearer {self.settings.peer_token}"}
        for attempt in range(3):
            try:
                # Only the status is needed. Closing the stream avoids buffering
                # an untrusted or accidentally huge peer response.
                async with asyncio.timeout(2):
                    async with client.stream("POST", base.rstrip("/") + path,
                        json=event.model_dump(by_alias=True, mode="json"), headers=headers,
                        timeout=2, follow_redirects=False) as response:
                        status = response.status_code
                if 200 <= status < 300:
                    self.delivered += 1
                    return
                if status < 500 and status != 429:
                    break
            except (httpx.HTTPError, TimeoutError):
                pass
            if attempt < 2:
                await asyncio.sleep(.2 * (2 ** attempt))
        self.failed += 1
        self.last_error = "Peer delivery failed; consumer must reconcile from snapshot"

    async def run(self):
        async with httpx.AsyncClient(follow_redirects=False) as owned:
            client = self.client or owned
            while not self.closed:
                event = await self.queue.get()
                try:
                    await self.deliver(event, client)
                finally:
                    self.queue.task_done()
