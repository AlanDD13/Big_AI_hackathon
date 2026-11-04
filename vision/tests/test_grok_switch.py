import asyncio
import pytest
from fastapi.testclient import TestClient
from vision.app import camera_token, create_app
from vision.settings import Settings
from vision.service import VisionService
from vision.tests.test_pipeline import Clock, Detector, observe, classifier


def test_camera_control_auth_and_boolean_validation():
    master = 'test-master-token-long-enough'
    app = create_app(Settings(token=master, automatic_classification=False),
                     detector=Detector(), classifier=classifier(), run_workers=False)
    auth = {'Authorization': 'Bearer ' + camera_token(master, 'phone-1')}
    route = '/api/cameras/phone-1/grok'
    with TestClient(app) as api:
        assert api.get(route).status_code == 401
        assert api.get(route, headers=auth).json()['enabled'] is False
        assert api.put('/api/cameras/phone-2/grok', headers=auth, json={'enabled': True}).status_code == 401
        assert api.put(route, headers=auth, json={'enabled': 'false'}).status_code == 422
        assert api.put(route, headers=auth, json={'enabled': True, 'extra': 1}).status_code == 422
        assert api.put(route, headers=auth, json={'enabled': True}).json()['enabled'] is True
        assert api.get('/health').json()['automaticClassificationByCamera']['phone-2'] is False
        assert api.put(route, headers=auth, json={'enabled': False}).json()['enabled'] is False


@pytest.mark.asyncio
async def test_switch_enables_both_assessments_once_then_off_prevents_new_calls():
    class Counted:
        provider = 'fixture'
        classifications = 0
        pickups = 0
        async def classify(self, crop, context):
            self.classifications += 1
            return await classifier().classify(crop, context)
        async def assess_pickup(self, crop, context):
            self.pickups += 1
            return None
    clock = Clock(); provider = Counted()
    service = VisionService(Settings(token='test-token-long-enough', clock=clock,
                                    automatic_classification=False), Detector(), provider)
    try:
        for i in range(12): await observe(service, clock, i, evidence=False)
        assert provider.classifications == provider.pickups == 0
        await service.set_grok_enabled('phone-1', True)
        await observe(service, clock, 12, evidence=False)
        await service.drain_classifications()
        assert provider.classifications == provider.pickups == 1
        for i in range(13, 20): await observe(service, clock, i, evidence=False)
        await service.drain_classifications()
        assert provider.classifications == provider.pickups == 1
        await service.set_grok_enabled('phone-1', False)
        for state in service.objects.values(): service.invalidate(state, 'TEST_NEW_EPISODE')
        for i in range(20, 25): await observe(service, clock, i, evidence=False)
        await service.drain_classifications()
        assert provider.classifications == provider.pickups == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_off_cancels_pending_automatic_assessment():
    started = asyncio.Event()
    class Slow:
        provider = 'fixture'
        async def classify(self, crop, context):
            started.set()
            await asyncio.Event().wait()
    clock = Clock()
    service = VisionService(Settings(token='test-token-long-enough', clock=clock), Detector(), Slow())
    try:
        for i in range(12): await observe(service, clock, i, evidence=False)
        await started.wait()
        await service.set_grok_enabled('phone-1', False)
        assert not service.classification_tasks
        assert service.snapshot()[0]['classification'] is None
    finally:
        await service.close()
