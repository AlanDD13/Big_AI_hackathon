"""Bounded, fixed-source camera transport; transport activity is not freshness."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from email.message import Message
import re
import ssl
from threading import Lock
from urllib.parse import urlsplit

import anyio
import httpx
from fastapi.responses import StreamingResponse


MAX_FRAME_BYTES = 8 * 1024 * 1024
FIRST_FRAME_SECONDS = 5.0
STREAM_SECONDS = 60.0
BOUNDARY = re.compile(r"[0-9A-Za-z'()+_,./:=?-]{1,70}")


class CameraUnavailable(Exception):
    """Public callers receive a generic error, never source configuration."""


@dataclass(frozen=True, repr=False)
class CameraConfig:
    url: str = ""
    authorization: str = ""
    ca_cert: str = ""

    @property
    def configured(self) -> bool:
        try:
            source = urlsplit(self.url)
            source.port  # Reject malformed/out-of-range ports before reporting configured.
            return bool(source.scheme in {"http", "https"} and source.hostname
                        and not source.username and not source.password
                        and not source.fragment and "\r" not in self.url and "\n" not in self.url
                        and "\r" not in self.authorization and "\n" not in self.authorization)
        except ValueError:
            return False


class CameraFeed:
    def __init__(self, client, response, chunks, prefix, media_type, release):
        self.client = client
        self.response = response
        self.chunks = chunks
        self.prefix = prefix
        self.media_type = media_type
        self.release = release
        self.closed = False

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            with anyio.CancelScope(shield=True):
                try:
                    await self.response.aclose()
                finally:
                    await self.client.aclose()
        finally:
            self.release()

    async def stream(self, authorized: Callable[[], bool]) -> AsyncIterator[bytes]:
        try:
            async with asyncio.timeout(STREAM_SECONDS):
                if not authorized():
                    return
                yield self.prefix
                async for chunk in self.chunks:
                    if not authorized():
                        return
                    yield chunk
        except (httpx.HTTPError, TimeoutError):
            # Headers are already sent. Close; the UI owns reconnect/stale state.
            return
        finally:
            await self.close()


class CameraProxy:
    def __init__(self, config: CameraConfig, *, transport=None):
        self.config = config
        self.transport = transport
        self._lock = Lock()
        self._active = 0

    def _release(self):
        with self._lock:
            self._active -= 1

    async def open(self) -> CameraFeed:
        if not self.config.configured:
            raise CameraUnavailable
        with self._lock:
            if self._active >= 2:
                raise CameraUnavailable
            self._active += 1
        client = response = None
        try:
            async with asyncio.timeout(FIRST_FRAME_SECONDS):
                verify = ssl.create_default_context(cafile=self.config.ca_cert) if self.config.ca_cert else True
                client = httpx.AsyncClient(
                    verify=verify, transport=self.transport, trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(3.0, connect=3.0),
                )
                headers = {"Accept": "image/jpeg, multipart/x-mixed-replace", "Accept-Encoding": "identity"}
                if self.config.authorization:
                    headers["Authorization"] = self.config.authorization
                response = await client.send(client.build_request("GET", self.config.url, headers=headers), stream=True)
                if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                    raise CameraUnavailable
                media = response.headers.get("content-type", "")
                if len(media) > 256 or "\r" in media or "\n" in media:
                    raise CameraUnavailable
                parsed = Message()
                parsed["content-type"] = media
                kind = parsed.get_content_type()
                chunks = response.aiter_bytes()
                prefix = bytearray()
                if kind == "image/jpeg":
                    async for chunk in chunks:
                        prefix.extend(chunk)
                        if len(prefix) > MAX_FRAME_BYTES:
                            raise CameraUnavailable
                    if not prefix.startswith(b"\xff\xd8") or not prefix.endswith(b"\xff\xd9"):
                        raise CameraUnavailable
                    media = "image/jpeg"
                elif kind == "multipart/x-mixed-replace":
                    boundary = parsed.get_param("boundary")
                    if not isinstance(boundary, str) or BOUNDARY.fullmatch(boundary) is None:
                        raise CameraUnavailable
                    media = f'multipart/x-mixed-replace; boundary="{boundary}"'
                    marker = b"--" + boundary.encode("ascii") + b"\r\n"
                    async for chunk in chunks:
                        prefix.extend(chunk)
                        if len(prefix) > MAX_FRAME_BYTES + 8192:
                            raise CameraUnavailable
                        split = prefix.find(b"\r\n\r\n")
                        if split < 0:
                            if len(prefix) > 8192:
                                raise CameraUnavailable
                            continue
                        if split > 8192 or not prefix.startswith(marker):
                            raise CameraUnavailable
                        part = Message()
                        for line in bytes(prefix[len(marker):split]).decode("ascii").split("\r\n"):
                            name, separator, value = line.partition(":")
                            if not separator:
                                raise CameraUnavailable
                            part[name] = value.strip()
                        if part.get_content_type() != "image/jpeg":
                            raise CameraUnavailable
                        image = prefix[split + 4:]
                        if len(image) >= 2 and not image.startswith(b"\xff\xd8"):
                            raise CameraUnavailable
                        end = image.find(b"\xff\xd9", 2)
                        if end >= 0:
                            if end + 2 > MAX_FRAME_BYTES:
                                raise CameraUnavailable
                            break
                    else:
                        raise CameraUnavailable
                else:
                    raise CameraUnavailable
                return CameraFeed(client, response, chunks, bytes(prefix), media, self._release)
        except BaseException as exc:
            try:
                with anyio.CancelScope(shield=True):
                    try:
                        if response is not None:
                            await response.aclose()
                    finally:
                        if client is not None:
                            await client.aclose()
            finally:
                self._release()
            if isinstance(exc, (httpx.HTTPError, httpx.InvalidURL, TimeoutError, ValueError, OSError, CameraUnavailable)):
                raise CameraUnavailable from None
            raise


class CameraStreamingResponse(StreamingResponse):
    """Own cleanup even if ASGI send fails before the body iterator starts."""

    def __init__(self, feed: CameraFeed, authorized: Callable[[], bool], *, headers):
        self.feed = feed
        super().__init__(feed.stream(authorized), media_type=feed.media_type, headers=headers)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self.feed.close()
