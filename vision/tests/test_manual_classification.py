import pytest

from vision.service import VisionService
from vision.settings import Settings
from vision.tests.test_pipeline import Clock, Detector, observe


@pytest.mark.asyncio
async def test_manual_mode_detects_stationary_objects_without_any_grok_calls():
    class NoCalls:
        provider = 'grok'
        async def classify(self, crop, context):
            pytest.fail('Automatic classification must be disabled')
        async def assess_pickup(self, crop, context):
            pytest.fail('Automatic pickup advisory must be disabled')

    clock = Clock()
    service = VisionService(Settings(token='test-token-long-enough', clock=clock,
                                    automatic_classification=False),
                            detector=Detector(), classifier=NoCalls())
    try:
        for count in range(30):
            await observe(service, clock, count, evidence=False)
        await service.drain_classifications()
        obj = service.snapshot()[0]
        assert obj['motionState'] == 'STATIONARY'
        assert obj['classification'] is None
        assert not obj['potentialTarget']
        assert service.classification_tasks == set()
    finally:
        await service.close()


def test_manual_mode_can_be_selected_from_environment(monkeypatch):
    monkeypatch.setenv('VISION_TOKEN', 'test-token-long-enough')
    monkeypatch.setenv('VISION_AUTOMATIC_CLASSIFICATION', 'false')
    assert Settings.from_env().automatic_classification is False


def test_invalid_automatic_mode_flag_is_rejected(monkeypatch):
    monkeypatch.setenv('VISION_TOKEN', 'test-token-long-enough')
    monkeypatch.setenv('VISION_AUTOMATIC_CLASSIFICATION', 'typo')
    with pytest.raises(ValueError, match='VISION_AUTOMATIC_CLASSIFICATION'):
        Settings.from_env()


def test_manual_request_makes_one_identification_call_and_no_pickup_call():
    from fastapi.testclient import TestClient
    from vision.app import create_app
    from vision.tests.test_peer_classification import Classifier, image

    class Counted(Classifier):
        calls = 0
        async def classify(self, crop, context):
            self.calls += 1
            return await super().classify(crop, context)
        async def assess_pickup(self, crop, context):
            pytest.fail('Identification-only test must not request pickup advice')

    provider = Counted()
    app = create_app(Settings(token='test-token-long-enough',
                              automatic_classification=False),
                     classifier=provider, run_workers=False)
    with TestClient(app) as client:
        health = client.get('/health').json()
        assert health['automaticClassificationEnabled'] is False
        assert health['pendingAutomaticClassifications'] == 0
        response = client.post('/api/classify-object',
                               headers={'Authorization': 'Bearer test-token-long-enough'},
                               json={'cropImageBase64': image(), 'includePickup': False})
        assert response.status_code == 200
        assert provider.calls == 1
        assert response.json()['visualPickupAdvisory']['status'] == 'NOT_REQUESTED'
