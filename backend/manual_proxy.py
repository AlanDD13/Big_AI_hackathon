"""Session-bound chassis forwarding. P4 remains the sole hardware/watchdog owner."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import json
from math import isfinite
from urllib.parse import urlsplit

from fastapi.concurrency import run_in_threadpool
import httpx
import anyio
from psycopg import Error as PostgresError

from backend.evidence import ID_RE
from backend.manual_requests import InvalidManualRequest, parse_manual_request
from backend.postgres_projection import StorageUnavailable


STAGES = {"IDLE", "MANUAL_IDLE", "MANUAL_DRIVING", "UNKNOWN", "STOPPED"}
ERRORS = {"AUTONOMOUS_ACTIVE", "LEASE_HELD", "LEASE_EXPIRED", "STALE_INTENT",
          "STOP_LOCKED", "NOT_STOPPED", "TELEMETRY_STALE", "VALIDATION_ERROR",
          "UNKNOWN_ROVER", "BRIDGE_BOOT_MISMATCH"}


class ManualFailure(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code


@dataclass(repr=False)
class Lease:
    session: str
    lease_id: str
    generation: int
    boot: str
    expires: float
    caps: dict
    ttl: float
    sequence: int = 0


class ManualProxy:
    def __init__(self, *, url: str, peer_token: str, rover_id: str = "rover-1",
                 autonomous_active: Callable[[str], bool] | None = None,
                 now: Callable[[], float], transport=None):
        self._url = url.rstrip("/")
        self._peer = peer_token
        self.rover_id = rover_id
        self._autonomous_active = autonomous_active
        self._now = now
        self._transport = transport
        self._lock = asyncio.Lock()
        self._lease: Lease | None = None
        self._epoch = 0
        self._unknown = False
        self._boot: str | None = None
        self._stop_latched = False

    def _configured(self):
        try:
            url = urlsplit(self._url)
            url.port
            return (url.scheme in {"http", "https"} and url.hostname and not url.username
                    and not url.password and not url.query and not url.fragment
                    and len(self._peer) >= 16 and ID_RE.fullmatch(self.rover_id))
        except ValueError:
            return False

    async def _call(self, method, path, body=None):
        if not self._configured():
            raise ManualFailure(503, "MANUAL_UNAVAILABLE")
        try:
            async with asyncio.timeout(0.25):
                async with httpx.AsyncClient(transport=self._transport, trust_env=False,
                                            follow_redirects=False, timeout=0.2) as client:
                    async with client.stream(method, self._url + path, json=body,
                                             headers={"Authorization": f"Bearer {self._peer}"}) as response:
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > 65536:
                                raise ValueError
                        result = json.loads(data)
                        if not isinstance(result, dict):
                            raise ValueError
                        if response.status_code != 200:
                            error = result.get("error")
                            code = error.get("code") if isinstance(error, dict) else None
                            if response.status_code in {404, 409, 422, 503} and isinstance(code, str) and code in ERRORS:
                                raise ManualFailure(response.status_code, code)
                            raise ValueError
                        return result
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TimeoutError, OSError):
            raise ManualFailure(503, "MANUAL_UNAVAILABLE") from None

    def _identity(self, body):
        boot = body.get("bridgeBootId")
        if (body.get("roverId") != self.rover_id or not isinstance(boot, str) or len(boot) != 32
                or any(char not in "0123456789abcdef" for char in boot)):
            raise ManualFailure(503, "MANUAL_UNAVAILABLE")
        if self._boot is not None and self._boot != boot:
            self._lease = None
            self._epoch += 1
        self._boot = boot
        return boot

    async def status(self, *, health=None):
        try:
            if health is None:
                health = await self._call("GET", "/health")
            body = await self._call("GET", "/manual/status?roverId=" + self.rover_id, None)
            self._identity(body)
            mode = health.get("executionMode")
            if (health.get("bridgeBootId") != body["bridgeBootId"] or not isinstance(mode, str)
                    or mode not in {"physical", "simulator"}):
                raise ManualFailure(503, "MANUAL_UNAVAILABLE")
            if not isinstance(body.get("stage"), str) or body["stage"] not in STAGES:
                raise ManualFailure(503, "MANUAL_UNAVAILABLE")
        except BaseException:
            self._unknown = True
            self._lease = None
            raise
        if body["stage"] in {"IDLE", "STOPPED"} and body.get("stationaryConfirmed") is True:
            self._unknown = False
            self._lease = None
        elif body["stage"] == "UNKNOWN":
            self._unknown = True
        body["executionMode"] = health["executionMode"]
        body["manualBootFence"] = health.get("manualBootFence")
        body["backendStopLocked"] = self._stop_latched
        return body

    async def _gate(self):
        if self._autonomous_active is None:
            raise ManualFailure(503, "STORAGE_UNAVAILABLE")
        try:
            active = await run_in_threadpool(self._autonomous_active, self.rover_id)
        except (PostgresError, StorageUnavailable):
            raise ManualFailure(503, "STORAGE_UNAVAILABLE") from None
        if active:
            raise ManualFailure(409, "AUTONOMOUS_ACTIVE")

    async def _fence(self):
        health = await self._call("GET", "/health")
        if health.get("manualBootFence") != "bridgeBootId-v1":
            raise ManualFailure(503, "MANUAL_UNAVAILABLE")
        return health

    def _owned(self, session, payload):
        lease = self._lease
        if (lease is None or lease.session != session or lease.lease_id != payload["leaseId"]
                or lease.generation != payload["leaseGeneration"]):
            raise ManualFailure(409, "LEASE_HELD")
        if payload["bridgeBootId"] != lease.boot:
            raise ManualFailure(409, "BRIDGE_BOOT_MISMATCH")
        return lease

    async def end_session(self, session):
        if self._lease is not None and self._lease.session == session:
            try:
                await self.execute("stop", session, {"roverId": self.rover_id, "reason": "operator_stop"}, lambda: True)
            except ManualFailure:
                pass  # Unknown remains blocked; bridge silence watchdog is independent.

    async def execute(self, action, session, payload, authorized):
        payload = parse_manual_request(action, payload, now=self._now())
        if payload["roverId"] != self.rover_id:
            raise ManualFailure(404, "UNKNOWN_ROVER")
        if not authorized():
            raise ManualFailure(401, "UNAUTHORIZED")
        if action in {"stop", "release"}:
            if action == "release":
                self._owned(session, payload)
            self._epoch += 1
            self._lease = None
            self._unknown = True
            if action == "stop":
                self._stop_latched = True
            # Priority safety operations bypass a pending nonzero drive/lease.
            result = await self._call("POST", "/stop" if action == "stop" else "/manual/release", payload)
            boot = self._identity(result)
            if action == "release" and boot != payload["bridgeBootId"]:
                raise ManualFailure(409, "BRIDGE_BOOT_MISMATCH")
            if result.get("stage") not in {"UNKNOWN", "STOPPED"} or result.get("motion") is not False:
                raise ManualFailure(503, "MANUAL_UNAVAILABLE")
            return result
        if self._lock.locked():
            raise ManualFailure(409, "STALE_INTENT" if action == "drive" else "LEASE_HELD")
        async with self._lock:
            epoch = self._epoch
            await self._gate()
            health = await self._fence() if action in {"lease", "rearm"} else None
            if not authorized():
                raise ManualFailure(401, "UNAUTHORIZED")
            if action == "lease":
                status = await self.status(health=health)
                if status["stage"] == "UNKNOWN" or status.get("telemetryFresh") is not True:
                    raise ManualFailure(503, "TELEMETRY_STALE")
                if status["stage"] == "STOPPED" or self._stop_latched:
                    raise ManualFailure(409, "STOP_LOCKED")
                if self._lease is not None or status["stage"] != "IDLE":
                    raise ManualFailure(409, "LEASE_HELD")
            elif action == "drive":
                lease = self._owned(session, payload)
                if self._unknown:
                    raise ManualFailure(503, "TELEMETRY_STALE")
                if self._now() >= lease.expires:
                    raise ManualFailure(409, "LEASE_EXPIRED")
                if payload["sequence"] <= lease.sequence:
                    raise ManualFailure(409, "STALE_INTENT")
                if any(abs(payload[field]) > lease.caps[cap] for field, cap in
                       [("xMps", "maxAbsXMps"), ("yMps", "maxAbsYMps"), ("zDegps", "maxAbsZDegps")]):
                    raise InvalidManualRequest("Velocity exceeds the granted bridge caps")
                if (payload["expiresAt"] - payload["issuedAt"]) * 1000 > lease.caps["maxIntentTtlMs"] + 1e-8:
                    raise InvalidManualRequest("Intent lifetime exceeds the granted bridge cap")
                lease.sequence = payload["sequence"]  # Never reuse an ambiguous sequence.
            else:
                status = await self.status(health=health)
                if status["bridgeBootId"] != payload["bridgeBootId"]:
                    raise ManualFailure(409, "BRIDGE_BOOT_MISMATCH")
                if status["stage"] != "STOPPED" or status.get("stationaryConfirmed") is not True:
                    raise ManualFailure(409, "NOT_STOPPED")
            if epoch != self._epoch:
                raise ManualFailure(503, "TELEMETRY_STALE")
            if not authorized():
                raise ManualFailure(401, "UNAUTHORIZED")
            parse_manual_request(action, payload, now=self._now())
            try:
                result = await self._call("POST", "/manual/" + action, payload)
                boot = self._identity(result)
                if epoch != self._epoch or not authorized():
                    raise ManualFailure(503, "TELEMETRY_STALE")
                if action != "lease" and boot != payload["bridgeBootId"]:
                    raise ManualFailure(409, "BRIDGE_BOOT_MISMATCH")
                if action == "lease" and boot != status["bridgeBootId"]:
                    raise ManualFailure(409, "BRIDGE_BOOT_MISMATCH")
                if action == "lease":
                    caps = result.get("caps", {})
                    for key, max_value in [("maxAbsXMps", 0.35), ("maxAbsYMps", 0.35),
                                           ("maxAbsZDegps", 45.0), ("watchdogTimeoutMs", 300), ("maxIntentTtlMs", 500)]:
                        value = caps.get(key) if isinstance(caps, dict) else None
                        if type(value) not in {int, float} or not 0 < value <= max_value or not isfinite(value):
                            raise ManualFailure(503, "MANUAL_UNAVAILABLE")
                    lease_id, generation, expires = result.get("leaseId"), result.get("leaseGeneration"), result.get("expiresAt")
                    if (not isinstance(lease_id, str) or ID_RE.fullmatch(lease_id) is None
                            or type(generation) is not int or generation < 1 or type(expires) not in {int, float}
                            or not self._now() < expires <= self._now() + 10 or not isfinite(expires)):
                        raise ManualFailure(503, "MANUAL_UNAVAILABLE")
                    self._lease = Lease(session, lease_id, generation, boot, expires, caps,
                                        payload["requestedTtlMs"] / 1000)
                    self._unknown = False
                elif action == "rearm":
                    if result.get("stage") != "IDLE" or result.get("motion") is not False or result.get("rearmed") is not True:
                        raise ManualFailure(503, "MANUAL_UNAVAILABLE")
                    self._lease = None
                    self._unknown = False
                    self._stop_latched = False
                elif (result.get("leaseId") != payload["leaseId"] or result.get("leaseGeneration") != payload["leaseGeneration"]
                      or result.get("lastSequence") != payload["sequence"] or result.get("stage") not in {"MANUAL_IDLE", "MANUAL_DRIVING"}):
                    raise ManualFailure(503, "MANUAL_UNAVAILABLE")
                if action == "drive":
                    velocity = result.get("appliedVelocity")
                    if not isinstance(velocity, dict) or any(
                        type(velocity.get(field)) not in {int, float}
                        or velocity[field] != payload[field]
                        for field in ("xMps", "yMps", "zDegps")
                    ):
                        raise ManualFailure(503, "MANUAL_UNAVAILABLE")
                    lease.expires = self._now() + lease.ttl
                return result
            except BaseException:
                # Lost ACK/cancellation/malformed result never grants reusable authority.
                self._unknown = True
                self._lease = None
                if action in {"lease", "rearm"} or epoch != self._epoch:
                    # A late grant/rearm must not undo a newer stop. Preserve a
                    # backend latch even if the compensating stop loses its ACK.
                    with anyio.CancelScope(shield=True):
                        try:
                            await self.execute("stop", session, {"roverId": self.rover_id, "reason": "operator_stop"}, lambda: True)
                        except ManualFailure:
                            pass
                raise
