import asyncio
from io import BytesIO

from PIL import Image
import pytest

from vision.classification import ClassificationResult, FixtureClassifier
from vision.perception import Detection
from vision.service import VisionService
from vision.settings import Settings
from vision.models import MappingEvidence


def jpeg():
    stream = BytesIO()
    Image.new("RGB", (160, 120), "white").save(stream, "JPEG")
    return stream.getvalue()


class Clock:
    def __init__(self): self.value = 1000.0
    def __call__(self): return self.value


class Detector:
    def __init__(self): self.items = [Detection("bottle", .87, (20, 20, 50, 90))]
    def detect(self, data): return self.items


def classifier(kind="INANIMATE", carrying="NO", category="TRASH"):
    return FixtureClassifier(ClassificationResult(category=category, subject_kind=kind,
        carrying=carrying, confidence="HIGH", description="test bottle", reason="Explicit fixture"))


def setup(classifier_=None):
    clock = Clock(); detector = Detector()
    service = VisionService(Settings(token="test-token-long-enough", clock=clock),
        detector=detector, classifier=classifier_ or classifier())
    return service, detector, clock


async def observe(service, clock, count, *, evidence=True, point=(.2, .3)):
    clock.value = 1000 + count * .5
    frame = service.frames.put("phone-1", "session1", "geometry1", count,
        clock.value, jpeg(), received_at=clock.value)
    await service.process_frame(frame)
    objects = service.snapshot()
    obj = next(o for o in objects if o["status"] != "LOST")
    if evidence:
        await service.apply_mapping(MappingEvidence(camera_id="phone-1", session_id="session1",
            geometry_version="geometry1", frame_id=count, track_id=obj["trackId"],
            map_id="floor1", map_version="1", calibration_id="cal1", calibration_valid=True,
            captured_at=clock.value, received_at=clock.value, floor_point_m=point,
            position_error_m=.01, valid=True, invalid_reason=None))
    return obj


@pytest.mark.asyncio
async def test_immediate_detection_then_static_candidate_and_single_notification():
    service, detector, clock = setup()
    first = await observe(service, clock, 0)
    assert first["status"] == "OBSERVING"
    assert first["detectorScore"] == .87
    assert not first["potentialTarget"]
    for count in range(1, 12): await observe(service, clock, count)
    await service.drain_classifications()
    target = service.snapshot()[0]
    assert target["potentialTarget"]
    assert target["classificationProvider"] == "fixture"
    assert target["classification"]["confidence"] == "HIGH"
    assert len([e for e in service.events if e.type == "alert.potential_target"]) == 1
    await observe(service, clock, 12)
    assert len([e for e in service.events if e.type == "alert.potential_target"]) == 1
    await service.close()


@pytest.mark.asyncio
async def test_no_metric_evidence_never_becomes_target():
    service, _, clock = setup()
    for count in range(12): await observe(service, clock, count, evidence=False)
    await service.drain_classifications()
    assert not service.snapshot()[0]["potentialTarget"]
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,carrying", [("PERSON", "NO"), ("ANIMAL", "NO"), ("INANIMATE", "YES"), ("UNCERTAIN", "UNCERTAIN")])
async def test_living_carried_and_uncertain_grok_never_eligible(kind, carrying):
    service, _, clock = setup(classifier(kind, carrying))
    for count in range(12): await observe(service, clock, count)
    await service.drain_classifications()
    assert not service.snapshot()[0]["potentialTarget"]
    await service.close()


@pytest.mark.asyncio
async def test_motion_revoke_and_stale_camera_revoke():
    service, detector, clock = setup()
    for count in range(12): await observe(service, clock, count)
    await service.drain_classifications()
    assert service.snapshot()[0]["potentialTarget"]
    await observe(service, clock, 12, point=(.27,.3))
    assert not service.snapshot()[0]["potentialTarget"]
    assert any(e.type == "alert.target_revoked" for e in service.events)
    clock.value += 2
    await service.sweep()
    assert all(not o["potentialTarget"] for o in service.snapshot())
    await service.close()


@pytest.mark.asyncio
async def test_late_grok_result_cannot_reactivate_lost_track():
    ready = asyncio.Event()
    class Slow:
        provider = "grok"
        async def classify(self, crop, context):
            await ready.wait()
            return await classifier().classify(crop, context)
    service, detector, clock = setup(Slow())
    for count in range(11): await observe(service, clock, count)
    await asyncio.sleep(0)
    detector.items=[]
    frame=service.frames.put("phone-1","session1","geometry1",11,1005.5,jpeg(),received_at=1005.5)
    clock.value=1005.5
    await service.process_frame(frame)
    ready.set(); await service.drain_classifications()
    assert all(not o["potentialTarget"] for o in service.snapshot())
    await service.close()


@pytest.mark.asyncio
async def test_revocation_publishes_complete_versioned_state():
    service, _, clock = setup()
    for count in range(12):
        await observe(service, clock, count)
    await service.drain_classifications()
    before = service.snapshot()[0]
    assert before["potentialTarget"]
    await observe(service, clock, 12, point=(.27, .3))
    revoked = next(e.payload for e in reversed(service.events) if e.type == "alert.target_revoked")
    assert revoked["objectId"] == before["objectId"]
    assert revoked["producerBootId"] == service.boot_id
    assert revoked["entityVersion"] > before["entityVersion"]
    assert revoked["revision"] > before["revision"]
    assert not revoked["potentialTarget"]
    assert revoked["status"] != "POTENTIAL_TARGET"
    assert revoked["classification"] is None
    assert revoked["reason"] == "TARGET_MOVED"
    assert revoked["action"] == "HOLD_BEFORE_PICKUP"
    await service.close()


@pytest.mark.asyncio
async def test_visual_pickup_is_advisory_and_revoked_with_object_evidence():
    from vision.classification import VisualPickupResult
    class Visual(FixtureClassifier):
        async def assess_pickup(self, crop, context):
            return VisualPickupResult(object_form='CRUMPLED', visual_suitability='POSSIBLE',
                                      reason='Raised surface only; size and grip unverified')
    provider = Visual(classifier()._classification)
    service, _, clock = setup(provider)
    for count in range(12):
        await observe(service, clock, count, evidence=False)
    await service.drain_classifications()
    obj = service.snapshot()[0]
    assert obj['visualPickupStatus'] == 'COMPLETED'
    assert obj['visualPickupAdvisory']['object_form'] == 'CRUMPLED'
    assert obj['visualPickupAdvisory']['advisory'] is True
    assert obj['visualPickupAdvisory']['pickupVerified'] is False
    assert not obj['potentialTarget']  # a visual grasp hint cannot replace mapping
    clock.value += 2
    await service.sweep()
    lost = service.snapshot()[0]
    assert lost['visualPickupAdvisory'] is None
    assert not lost['potentialTarget']
    await service.close()
