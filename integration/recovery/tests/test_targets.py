import httpx
import pytest

from integration.recovery.targets import read_target, target_from_snapshot


def row():
    return {"objectId": "phone:s:paper", "revision": 7, "dispatchEligible": False,
            "classificationStatus": "COMPLETED", "classification": {
                "category": "TRASH", "subject_kind": "INANIMATE", "confidence": "HIGH", "carrying": "NO"}}


def test_reads_exact_backend_projection_without_reclassification_or_db_credentials():
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"objects": [row()]})
    with httpx.Client(base_url="https://backend.test", transport=httpx.MockTransport(handle)) as client:
        target = read_target(client, "phone:s:paper")
    assert target.revision == 7 and target.classification == row()["classification"]
    assert target.dispatch_eligible is False
    assert len(requests) == 1 and requests[0].url.path == "/api/snapshot"


@pytest.mark.parametrize("rows", [[], [row(), row()], [dict(row(), classificationStatus="PENDING")]])
def test_missing_ambiguous_and_pending_classifications_are_rejected(rows):
    with pytest.raises(ValueError):
        target_from_snapshot({"objects": rows}, "phone:s:paper")
