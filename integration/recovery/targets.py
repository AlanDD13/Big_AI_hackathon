"""Read the backend's durable projection through its existing authenticated API.

The caller supplies a session-configured client; this module knows no DB keys.
Dispatch eligibility is an upstream decision, not inferred from Grok confidence.
"""
import httpx

from .types import Target


def target_from_snapshot(snapshot: dict, object_id: str) -> Target:
    matches = [row for row in snapshot.get("objects", []) if row.get("objectId") == object_id]
    if len(matches) != 1:
        raise ValueError("Target missing or ambiguous in backend snapshot")
    row = matches[0]
    if row.get("classificationStatus") != "COMPLETED":
        raise ValueError("Stored classification is not complete")
    return Target(object_id=row["objectId"], revision=row["revision"],
                  classification=row["classification"], dispatch_eligible=row["dispatchEligible"])


def read_target(client: httpx.Client, object_id: str) -> Target:
    """client.base_url points at backend, with its operator session already set."""
    response = client.get("/api/snapshot", timeout=5.0)
    response.raise_for_status()
    return target_from_snapshot(response.json(), object_id)
