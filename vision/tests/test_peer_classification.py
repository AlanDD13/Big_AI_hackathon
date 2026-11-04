import base64
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image

from vision.app import create_app
from vision.classification import ObjectAssessment, ClassificationResult, VisualPickupResult
from vision.settings import Settings


def image():
    out = BytesIO(); Image.new('RGB', (20, 20)).save(out, 'JPEG')
    return base64.b64encode(out.getvalue()).decode()


class Classifier:
    provider = 'grok'
    transport = 'openrouter'
    _model = 'x-ai/grok-4.7'
    async def classify(self, crop, context):
        return (await self.assess_object(crop, context)).classification
    async def assess_object(self, crop, context):
        return ObjectAssessment(
            classification=ClassificationResult(category='TRASH', subject_kind='INANIMATE', carrying='NO',
                confidence='HIGH', description='crumpled paper', reason='visible target crop'),
            pickup=VisualPickupResult(object_form='CRUMPLED', visual_suitability='POSSIBLE',
                reason='raised folds', robot_grasp_estimate='LIKELY', assumptions=['lightweight paper'],
                required_checks=['verify size and reach']))


def test_peer_can_use_grok_without_receiving_a_model_key_or_authorization():
    app = create_app(Settings(token='master-token-at-least-16', peer_token='peer-token-at-least-16'),
                     classifier=Classifier(), run_workers=False)
    with TestClient(app) as client:
        body = {'requestId': 'frame-1-track-2', 'cropImageBase64': image()}
        assert client.post('/api/classify-object', json=body).status_code == 401
        response = client.post('/api/classify-object', json=body,
                               headers={'Authorization': 'Bearer peer-token-at-least-16'})
        assert response.status_code == 200
        result = response.json()
        assert result['requestId'] == body['requestId']
        assert result['model'] == 'x-ai/grok-4.7'
        assert result['classification']['result']['description'] == 'crumpled paper'
        assert not result['dispatchAuthorized'] and not result['pickupVerified']
        assert not app.state.service.objects  # stateless, no stationarity bypass
        assert 'token' not in response.text and 'api_key' not in response.text


def test_invalid_peer_images_fail_before_the_model_call():
    app = create_app(Settings(token='master-token-at-least-16'), classifier=Classifier(), run_workers=False)
    with TestClient(app) as client:
        response = client.post('/api/classify-object', json={'cropImageBase64': 'not-base64'},
                               headers={'Authorization': 'Bearer master-token-at-least-16'})
        assert response.status_code == 422
