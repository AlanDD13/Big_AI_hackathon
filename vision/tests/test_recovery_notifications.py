"""Mock model + synthetic frames -> route callback or durable backend SSE.

No real inference, database, SDK connection, or movement is performed.
"""
import json
from pathlib import Path
import sys

import httpx
import pytest
from pydantic import ValidationError

from backend.app import create_app
from vision.camera_recovery import CameraRecovery, RouteBinding
from vision.classification import ClassificationResult, ObjectAssessment, VisualPickupResult
from vision.perception import Detection
from vision.publisher import Publisher
from vision.tests.test_pipeline import observe, setup


class JointModel:
    provider = 'grok'  # Mock of the live provider interface, not live evidence.
    def __init__(self, score):
        self.score, self.calls = score, 0

    async def assess_object(self, crop, context):
        self.calls += 1
        return ObjectAssessment(
            classification=ClassificationResult(category='TRASH', subject_kind='INANIMATE',
                carrying='NO', confidence='HIGH', description='Empty can', reason='Mock evidence'),
            pickup=VisualPickupResult(object_form='OTHER', visual_suitability='POSSIBLE',
                robot_grasp_estimate='LIKELY', estimate_confidence='HIGH',
                grabbability_score=self.score, reason='Mock grasp surface'))


@pytest.mark.parametrize('score', [True, '0.99', -.1, 1.1, float('nan'), float('inf')])
def test_model_grabbability_score_is_strict_and_bounded(score):
    with pytest.raises(ValidationError):
        VisualPickupResult(object_form='OTHER', visual_suitability='POSSIBLE',
                           reason='Mock', grabbability_score=score)


@pytest.mark.asyncio
@pytest.mark.parametrize('score,expected,calls_expected', [
    (.42, 'pickup_unavailable', 0), (.92, 'recovery_completed', 1)])
async def test_camera_classifies_then_dispatches_or_notifies_backend_frontend(tmp_path, score, expected, calls_expected):
    model = JointModel(score)
    service, detector, clock = setup(model)
    detector.items = [Detection('can', .87, (20, 20, 50, 90))]
    launches = []
    async def run_route(obj, config, directory, reference):
        assert reference  # Exact classified crop accompanies the route call.
        launches.append(obj['objectId'])
        return {'result': 'RETURNED_AND_RELEASED'}
    service.recovery = CameraRecovery(
        RouteBinding('*', '*', '*', None, Path(sys.executable), tmp_path, True),
        service._recovery_notice, clock=clock, execute=run_route,
        reference=lambda object_id: service.objects[object_id].evidence_crop)
    for frame in range(12):
        await observe(service, clock, frame, evidence=False)
    await service.drain_classifications()
    await service.recovery.drain()
    assert model.calls == 1
    assert len(launches) == calls_expected
    backend = create_app(operator_token='operator-test-secret', peer_token='peer-test-secret-value',
                         cookie_secure=False, now=clock, idle_timeout=0)
    service.settings.backend_url = 'http://backend.test'
    service.settings.peer_token = 'peer-test-secret-value'
    publisher = Publisher(service.settings)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=backend), base_url='http://backend.test') as client:
        await client.post('/api/session', json={'token': 'operator-test-secret'})
        cursor = (await client.get('/api/snapshot')).json()['cursor']
        for event in service.events:
            await publisher.deliver(event, client)
        assert publisher.failed == 0
        snapshot = (await client.get('/api/snapshot')).json()
        obj = snapshot['objects'][0]
        assert obj['classification']['category'] == 'TRASH'
        assert obj['visualPickupAdvisory']['grabbability_score'] == score
        assert obj['dispatchEligible'] is False  # Generic mapped mission gate is unchanged.
        assert expected in [alert['kind'] for alert in snapshot['alerts']]
        stream = (await client.get('/api/events', params={'after': cursor})).text
        assert f'event: alert.{expected}' in stream
    await service.close()
