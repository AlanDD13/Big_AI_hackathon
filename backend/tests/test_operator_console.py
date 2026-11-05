"""Real API/session/event plumbing with mocked native xAI audio."""
import json
import unittest

import httpx
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.speech import GrokSpeech
from backend.tests.test_vision_ingest import publication, BOOT_A, OPERATOR, PEER


class OperatorConsoleTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        async def speak(request):
            self.requests.append(request)
            self.assertEqual(str(request.url), 'https://api.x.ai/v1/tts')
            self.assertEqual(request.headers['authorization'], 'Bearer native-test-key')
            body = json.loads(request.content)
            self.assertEqual(body['voice_id'], 'eve')
            self.assertNotIn('model', body)  # xAI TTS selects the service, not a made-up model ID.
            return httpx.Response(200, content=b'ID3test-audio', headers={'content-type': 'audio/mpeg'})
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(speak))
        self.app = create_app(operator_token=OPERATOR, peer_token=PEER, cookie_secure=False,
            now=lambda: 1100., idle_timeout=0, speech=GrokSpeech('native-test-key', client=self.http))
        self.client = TestClient(self.app)

    def login(self):
        self.assertEqual(self.client.post('/api/session', json={'token': OPERATOR}).status_code, 204)

    def post(self, event):
        route = {'object.observed': '/api/detections', 'object.classified': '/api/classifications'}.get(event['type'], '/api/vision-events')
        return self.client.post(route, json=event, headers={'Authorization': f'Bearer {PEER}'})

    def test_auth_and_no_preview_messages(self):
        for route in ('/api/rover/notices', '/api/rover/notices/events', '/api/models', '/api/rover/camera/status'):
            self.assertEqual(self.client.get(route).status_code, 401)
        self.login()
        self.assertEqual(self.client.get('/api/rover/notices').json()['notices'], [])
        self.assertFalse(self.client.get('/api/rover/camera/status').json()['configured'])

    def test_observations_deduplicate_and_each_completed_step_streams(self):
        self.login()
        self.assertEqual(self.post(publication('object.observed', 1, BOOT_A, entityVersion=1, revision=0, classificationStatus='PENDING', classification=None, classificationProvider=None)).status_code, 200)
        first = self.client.get('/api/rover/notices').json()
        self.assertEqual([n['kind'] for n in first['notices']], ['found'])
        self.post(publication('object.observed', 2, BOOT_A, entityVersion=2, revision=0, classificationStatus='PENDING', classification=None, classificationProvider=None))
        for seq, step in ((3, 'FORWARD_30'), (4, 'TURN_RIGHT_90'), (5, 'PICKUP')):
            response = self.post(publication('alert.recovery_step', seq, BOOT_A,
                entityVersion=seq, revision=0, reason='ROUTE_STEP_COMPLETED', recoveryStep=step, classificationStatus='PENDING', classification=None, classificationProvider=None))
            self.assertEqual(response.status_code, 200, response.text)
        snapshot = self.client.get('/api/rover/notices').json()
        self.assertEqual(len(snapshot['notices']), 4)
        stream = self.client.get('/api/rover/notices/events', params={'after': first['cursor']}).text
        self.assertEqual(stream.count('event: rover.notice'), 3)
        self.assertIn('Holding is not independently verified', stream)
        self.assertNotIn('Finished.', stream)

    def test_voice_uses_known_notice_text_and_cache(self):
        self.login()
        self.post(publication('object.observed', 1, BOOT_A, entityVersion=1, revision=0))
        notice = self.client.get('/api/rover/notices').json()['notices'][0]
        audio = self.client.get(notice['audioUrl'])
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.headers['content-type'], 'audio/mpeg')
        self.assertEqual(json.loads(self.requests[0].content)['text'], 'Object found.')
        self.assertEqual(self.client.get(notice['audioUrl']).status_code, 200)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.client.get('/api/rover/notices/unknown/audio').status_code, 404)
        self.assertEqual(self.client.post('/api/rover/notices/unknown/audio', json={'text': 'arbitrary'}).status_code, 405)
        self.assertEqual(self.client.get('/api/models').json()['speech']['used'], True)

    def test_native_model_and_score_are_exposed_without_claiming_speech_used(self):
        self.login()
        event = publication('object.classified', 1, BOOT_A, entityVersion=1,
            classificationModel='grok-4.7', visualPickupStatus='COMPLETED',
            visualPickupAdvisory=dict(object_form='OTHER', visual_suitability='POSSIBLE',
                reason='Mock estimate', advisory=True, pickupVerified=False, sourceFrameId=11,
                grabbability_score=.92))
        self.assertEqual(self.post(event).status_code, 200)
        models = self.client.get('/api/models').json()
        self.assertEqual(models['observedVisionModels'], ['grok-4.7'])
        self.assertFalse(models['speech']['used'])
        notice = self.client.get('/api/rover/notices').json()['notices'][-1]
        self.assertEqual(notice['grabbabilityScore'], .92)
        self.assertEqual(notice['classificationModel'], 'grok-4.7')
        self.assertEqual(notice['classificationConfidence'], 'HIGH')
        self.assertIn('92 percent', notice['text'])
        self.client.delete('/api/session')
        self.assertEqual(self.client.get(notice['audioUrl']).status_code, 401)

    def test_missing_voice_key_is_unavailable_and_old_notice_cursor_requires_snapshot(self):
        self.login()
        cursor = self.client.get('/api/rover/notices').json()['cursor']
        for sequence in range(1, 102):
            self.assertEqual(self.post(publication('object.classified', sequence, BOOT_A,
                objectId=f'phone-1:session1:track{sequence}', trackId=f'track{sequence}',
                entityVersion=1, revision=1)).status_code, 200)
        stream = self.client.get('/api/rover/notices/events', params={'after': cursor}).text
        self.assertIn('event: snapshot_required', stream)
        self.assertEqual(len(self.client.get('/api/rover/notices').json()['notices']), 200)
        unavailable = GrokSpeech()
        from backend.speech import SpeechUnavailable
        with self.assertRaises(SpeechUnavailable):
            import asyncio
            asyncio.run(unavailable.audio('Object found.'))
        self.assertFalse(unavailable.used)
