"""Regression tests for revocable target evidence, using no network or ML model."""
import asyncio
from io import BytesIO

import pytest
from PIL import Image

from vision.classification import ClassificationResult
from vision.models import MappingEvidence
from vision.perception import Detection
from vision.service import VisionService
from vision.settings import Settings


class Clock:
    value = 1000.0
    def __call__(self):
        return self.value


class Detector:
    def __init__(self):
        self.items = [Detection('bottle', .9, (20, 20, 50, 90))]
    def detect(self, image):
        return self.items


class DelayedClassifier:
    provider = 'fixture'
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
    async def classify(self, crop, context):
        self.started.set()
        await self.release.wait()
        return ClassificationResult(category='TRASH', subject_kind='INANIMATE', carrying='NO',
                                    confidence='HIGH', description='Bottle', reason='Fixture')


@pytest.fixture
def fixture():
    clock, detector, classifier = Clock(), Detector(), DelayedClassifier()
    service = VisionService(Settings(token='fixture-token-123456', clock=clock),
                            detector=detector, classifier=classifier)
    stream = BytesIO()
    Image.new('RGB', (160, 120), 'white').save(stream, 'JPEG')
    return service, detector, classifier, clock, stream.getvalue()


def frame(service, clock, data, n, geometry='g1'):
    clock.value = 1000 + n * .5
    return service.frames.put('phone-1', 'session1', geometry, n, clock.value, data,
                              received_at=clock.value)


def evidence(frame_, track_id, point=(.2, .3)):
    return MappingEvidence(camera_id=frame_.camera_id, session_id=frame_.session_id,
        geometry_version=frame_.geometry_version, frame_id=frame_.frame_id, track_id=track_id,
        map_id='map1', map_version='v1', calibration_id='cal1', calibration_valid=True,
        captured_at=frame_.captured_at, received_at=frame_.received_at,
        floor_point_m=point, position_error_m=.01, valid=True, invalid_reason=None)


async def observe(service, clock, data, n, point=(.2, .3)):
    f = frame(service, clock, data, n)
    await service.process_frame(f)
    obj = next(o for o in service.snapshot() if o['status'] != 'LOST')
    await service.apply_mapping(evidence(f, obj['trackId'], point))
    return obj


@pytest.mark.asyncio
async def test_slow_metric_drift_does_not_accumulate_as_stationary(fixture):
    service, _, classifier, clock, data = fixture
    for n in range(11):
        await observe(service, clock, data, n, (.2 + n * .006, .3))
    classifier.release.set()
    await service.drain_classifications()
    obj = service.snapshot()[0]
    assert obj['metricStationarySeconds'] < 5
    assert not obj['potentialTarget']
    await service.close()


@pytest.mark.asyncio
async def test_unknown_track_for_existing_frame_cannot_supply_mapping(fixture):
    service, _, _, clock, data = fixture
    original = await observe(service, clock, data, 0)
    unprocessed = frame(service, clock, data, 1)
    with pytest.raises(ValueError, match='not observed'):
        await service.apply_mapping(evidence(unprocessed, original['trackId']))
    with pytest.raises(ValueError):
        await service.apply_mapping(evidence(unprocessed, 'fabricated-track'))
    assert not service.snapshot()[0]['potentialTarget']
    await service.close()


@pytest.mark.asyncio
async def test_geometry_change_discards_inflight_classification(fixture):
    service, _, classifier, clock, data = fixture
    for n in range(11):
        await observe(service, clock, data, n)
    await classifier.started.wait()
    changed = frame(service, clock, data, 11, geometry='g2')
    service.enqueue_frame(changed)
    classifier.release.set()
    await service.drain_classifications()
    old = service.snapshot()[0]
    assert old['status'] == 'LOST'
    assert not old['potentialTarget']
    assert old['classification'] is None
    assert not any(e.type == 'alert.potential_target' for e in service.events)
    await service.close()


@pytest.mark.asyncio
async def test_movement_discards_inflight_classification_and_prior_evidence(fixture):
    service, detector, classifier, clock, data = fixture
    for n in range(11):
        await observe(service, clock, data, n)
    await classifier.started.wait()
    old_evidence = evidence(service.frames.get('phone-1', 'session1', 10), service.snapshot()[0]['trackId'])
    detector.items = [Detection('bottle', .9, (27, 20, 57, 90))]
    await observe(service, clock, data, 11)
    classifier.release.set()
    await service.drain_classifications()
    obj = service.snapshot()[0]
    assert not obj['potentialTarget']
    assert obj['classification'] is None
    with pytest.raises(ValueError):
        await service.apply_mapping(old_evidence)
    await service.close()


@pytest.mark.asyncio
async def test_missing_track_revokes_previously_eligible_target(fixture):
    service, detector, classifier, clock, data = fixture
    classifier.release.set()
    for n in range(11):
        await observe(service, clock, data, n)
    await service.drain_classifications()
    assert service.snapshot()[0]['potentialTarget']
    detector.items = []
    await service.process_frame(frame(service, clock, data, 11))
    obj = service.snapshot()[0]
    assert obj['status'] == 'LOST'
    assert not obj['potentialTarget']
    assert any(e.type == 'alert.target_revoked' and e.payload['reason'] == 'TRACK_LOST'
               for e in service.events)
    await service.close()

@pytest.mark.asyncio
async def test_new_frame_revokes_eligibility_without_erasing_evidence(fixture):
    service, _, classifier, clock, data = fixture
    classifier.release.set()
    for n in range(11):
        await observe(service, clock, data, n)
    await service.drain_classifications()
    state = next(iter(service.objects.values()))
    assert state.potential_target
    original_revision = state.revision
    incoming = frame(service, clock, data, 11)
    service.enqueue_frame(incoming)
    assert not service.snapshot()[0]['potentialTarget']
    assert state.revision == original_revision
    assert state.classification is not None and state.metric_count >= 10
    await service.process_frame(incoming)
    assert service.snapshot()[0]['potentialTarget']
    await service.close()


@pytest.mark.asyncio
async def test_faster_ingress_does_not_discard_fresh_inference(fixture):
    import threading
    service, _, _, clock, data = fixture
    started, release = threading.Event(), threading.Event()
    class SlowDetector:
        def detect(self, image):
            started.set()
            release.wait(timeout=2)
            return [Detection('bottle', .9, (20, 20, 50, 90))]
    service.detector = SlowDetector()
    first = frame(service, clock, data, 0)
    task = asyncio.create_task(service.process_frame(first))
    assert await asyncio.to_thread(started.wait, 1)
    newer = frame(service, clock, data, 1)
    service.enqueue_frame(newer)
    release.set()
    await task
    assert service.snapshot()[0]['frameId'] == 0
    assert not service.snapshot()[0]['potentialTarget']
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['valid', 'invalid', 'moved', 'gap'])
async def test_identical_mapping_retry_is_idempotent_and_conflict_rejected(fixture, mode):
    service, _, _, clock, data = fixture
    first = await observe(service, clock, data, 0)
    n = 3 if mode == 'gap' else 1
    if mode == 'gap':
        for intermediate in (1, 2):
            await service.process_frame(frame(service, clock, data, intermediate))
    f = frame(service, clock, data, n)
    await service.process_frame(f)
    track = next(o['trackId'] for o in service.snapshot() if o['status'] != 'LOST')
    item = evidence(f, track, (.3, .3) if mode == 'moved' else (.2, .3))
    if mode == 'invalid':
        item = item.model_copy(update={'valid': False, 'floor_point_m': None})
    await service.apply_mapping(item)
    state = next(o for o in service.objects.values() if o.track_id == track)
    before = (state.metric_count, state.revision, len(service.events))
    await service.apply_mapping(item)
    assert (state.metric_count, state.revision, len(service.events)) == before
    conflict = item.model_copy(update={'map_version': 'different'})
    with pytest.raises(ValueError):
        await service.apply_mapping(conflict)
    await service.close()
