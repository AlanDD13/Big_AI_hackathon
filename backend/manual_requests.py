"""Validate the canonical manual-control-v1 browser requests without actuation.

Bridge boot fencing and session-bound forwarding are separate admission steps.
Passing this parser does not confer authority to drive.
"""
from __future__ import annotations

from math import isfinite

from backend.evidence import ID_RE


# Browser, backend and bridge wall clocks may differ slightly. Expiry stays strict.
MAX_FUTURE_CLOCK_SKEW_S = 0.1


class InvalidManualRequest(ValueError):
    pass


class StaleManualIntent(InvalidManualRequest):
    pass


def _identifier(value: object) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


def _integer(value: object, low: int, high: int | None = None) -> bool:
    return (type(value) is int and value >= low and (high is None or value <= high))


def _number(value: object) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)
    except OverflowError:
        return False


def parse_manual_request(action: str, payload: object, *, now: float) -> dict[str, object]:
    """Return a canonical copy or reject before any bridge interaction."""
    if not isinstance(payload, dict) or not _identifier(payload.get("roverId")):
        raise InvalidManualRequest("A valid roverId is required")
    required = {
        "lease": {"roverId", "requestedTtlMs"},
        "drive": {"roverId", "leaseId", "leaseGeneration", "bridgeBootId", "sequence", "issuedAt",
                  "expiresAt", "xMps", "yMps", "zDegps", "frame"},
        "release": {"roverId", "leaseId", "leaseGeneration", "bridgeBootId", "reason"},
        "stop": {"roverId", "reason"},
        "rearm": {"roverId", "bridgeBootId"},
    }
    if action not in required or set(payload) != required[action]:
        raise InvalidManualRequest("Manual request fields do not match the contract")
    if action == "lease" and not _integer(payload["requestedTtlMs"], 1, 10000):
        raise InvalidManualRequest("requestedTtlMs must be an integer in 1..10000")
    if action in {"drive", "release"}:
        if not _identifier(payload["leaseId"]) or not _integer(payload["leaseGeneration"], 1):
            raise InvalidManualRequest("A valid lease identity is required")
    if action in {"drive", "release", "rearm"}:
        boot = payload["bridgeBootId"]
        if (not isinstance(boot, str) or len(boot) != 32
                or any(char not in "0123456789abcdef" for char in boot)):
            raise InvalidManualRequest("A valid bridgeBootId is required")
    if action == "drive":
        if not _integer(payload["sequence"], 1) or payload["frame"] != "BODY":
            raise InvalidManualRequest("A positive sequence and BODY frame are required")
        for field, cap in [("xMps", 0.35), ("yMps", 0.35), ("zDegps", 45.0)]:
            if not _number(payload[field]) or abs(payload[field]) > cap:
                raise InvalidManualRequest("Velocity is nonfinite or exceeds the chassis slice caps")
        issued, expires = payload["issuedAt"], payload["expiresAt"]
        if not _number(issued) or not _number(expires):
            raise InvalidManualRequest("Intent timestamps must be finite numbers")
        if (not 0 < expires - issued <= 0.5
                or issued > now + MAX_FUTURE_CLOCK_SKEW_S or expires <= now):
            raise StaleManualIntent("Intent must be current with a lifetime at most 500 ms")
    if action == "release" and (not isinstance(payload["reason"], str)
                                or payload["reason"] not in {"user_release", "blur", "tab_hidden"}):
        raise InvalidManualRequest("Invalid release reason")
    if action == "stop" and payload["reason"] != "operator_stop":
        raise InvalidManualRequest("Invalid stop reason")
    return dict(payload)
