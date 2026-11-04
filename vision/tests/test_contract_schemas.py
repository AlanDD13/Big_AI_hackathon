"""Contract fixtures stay aligned with the producer models."""
import json
from pathlib import Path

from pydantic import ValidationError
import pytest

from vision.models import Event, MappingEvidence

ROOT = Path(__file__).resolve().parents[2] / "contracts"
OBJECT_KEYS = {
    "objectId", "cameraId", "sessionId", "geometryVersion", "trackId", "frameId",
    "capturedAt", "lastSeenAt", "cameraKind", "bboxPx", "detectorLabel", "detectorScore",
    "motionState", "pixelStationarySeconds", "metricStationarySeconds", "status",
    "exclusionReason", "classificationStatus", "classification", "classificationProvider",
    "mapping", "potentialTarget", "revision", "entityVersion", "producerBootId",
    "eligibilityReason", "frameRef",
}


def _load(name):
    return json.loads((ROOT / name).read_text())


def test_mapping_fixtures_match_the_model_and_schema_vocabulary():
    MappingEvidence.model_validate(_load("fixtures/mapping-evidence.json"))
    invalid = _load("fixtures/mapping-evidence-invalid.json")
    MappingEvidence.model_validate(invalid)
    assert invalid["invalidReason"] == "missing_contact"
    schema = (ROOT / "vision-mapping.schema.json").read_text()
    for reason in ("uncalibrated", "missing_contact", "frame_unavailable", "stale",
                   "out_of_area", "rover_confirmation_only", "unknown"):
        assert reason in schema
    broken = dict(invalid)
    broken["invalidReason"] = None
    with pytest.raises(ValidationError):
        MappingEvidence.model_validate(broken)


def test_recovery_events_keep_boot_identity_and_required_object_fields():
    recovery = _load("fixtures/vision-recovery.json")
    required_cases = {
        "same_revision_entity_update", "frame_pending_same_episode", "revocation",
        "new_boot_reset", "delayed_old_boot", "sequence_gap_and_complete_snapshot",
    }
    assert required_cases <= {case["id"] for case in recovery["cases"]}
    sample = recovery["sampleObject"]
    assert OBJECT_KEYS <= set(sample)
    seen_retry = False
    for case in recovery["cases"]:
        for event in case.get("events", []):
            payload = dict(sample)
            payload.update(event["payload"])
            assert OBJECT_KEYS <= set(payload)
            parsed = Event.model_validate({**event, "payload": payload})
            assert parsed.event_id.startswith(parsed.producer_boot_id + ":")
            assert payload["producerBootId"] == event["producerBootId"]
            if event["type"] == "alert.target_revoked":
                assert payload["action"] == "HOLD_BEFORE_PICKUP"
                assert payload["potentialTarget"] is False
        identities = [event["eventId"] for event in case.get("events", [])]
        if len(identities) != len(set(identities)):
            seen_retry = True
    assert seen_retry
    gap = next(case for case in recovery["cases"] if case["id"] == "sequence_gap_and_complete_snapshot")
    snapshot = gap["snapshot"]
    assert snapshot["complete"] is True
    assert snapshot["scope"] == "ACTIVE_OBJECTS"
    assert snapshot["producerBootId"] == snapshot["bootId"]
    assert gap["absentMeansDropped"] != sample["objectId"]
