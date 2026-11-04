import asyncio
import io
import json

import httpx
import pytest
from PIL import Image

from vision.classification import (
    ClassificationInvalidResponse, ClassificationResult, ClassificationUnavailable,
    ConfirmationResult, FixtureClassifier, GrokClassifier,
)


def image():
    out = io.BytesIO()
    Image.new('RGB', (2, 2), 'red').save(out, 'PNG')
    return out.getvalue()


def response(value):
    return httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]})


def classification():
    return dict(category='TRASH', subject_kind='INANIMATE', carrying='NO', confidence='HIGH', description='a bottle', reason='visible bottle')


def run(coro):
    return asyncio.run(coro)


def test_responses_request_and_typed_result():
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == 'https://api.x.ai/v1/responses'
        assert request.headers['authorization'] == 'Bearer test-key'
        assert body['model'] == 'grok-4.7' and body['store'] is False
        assert body['text']['format']['type'] == 'json_schema'
        assert body['text']['format']['strict'] is True
        content = body['input'][1]['content']
        assert sum(x['type'] == 'input_image' and x['image_url'].startswith('data:image/png;base64,') for x in content) == 2
        assert 'test-key' not in request.content.decode()
        return response(classification())
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await GrokClassifier('test-key', client=client).classify(image(), image())
            assert isinstance(result, ClassificationResult)
            assert result.category == 'TRASH'
    run(go())


def test_missing_key_and_bad_image():
    with pytest.raises(ClassificationUnavailable):
        run(GrokClassifier().classify(image(), image()))
    with pytest.raises(ValueError):
        run(GrokClassifier('key').classify(b'not image', image()))


@pytest.mark.parametrize('reply', [
    httpx.Response(429), httpx.Response(503),
    httpx.Response(200, json={'status': 'incomplete', 'output': []}),
    httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'refusal', 'refusal': 'no'}]}]}),
])
def test_unavailable_provider(reply):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: reply)) as client:
            with pytest.raises(ClassificationUnavailable):
                await GrokClassifier('key', client=client).classify(image(), image())
    run(go())


def test_timeout_and_invalid_output_are_safe():
    async def go():
        def timeout(request):
            raise httpx.ReadTimeout('private detail', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with pytest.raises(ClassificationUnavailable, match='unavailable'):
                await GrokClassifier('key', client=client).classify(image(), image())
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response({'category': 'evil'}))) as client:
            with pytest.raises(ClassificationInvalidResponse):
                await GrokClassifier('key', client=client).classify(image(), image())
    run(go())


def test_confirmation_must_match_allowed_track():
    async def go():
        result = {'state': 'CONFIRMED', 'candidate_track_id': 'unknown', 'reason': 'looks similar'}
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response(result))) as client:
            with pytest.raises(ClassificationInvalidResponse):
                await GrokClassifier('key', client=client).confirm(image(), image(), [{'track_id': 'known'}])
    run(go())


def test_fixture_is_explicit_and_uncertain_by_default():
    fixture = FixtureClassifier()
    assert fixture.provider == 'fixture'
    assert run(fixture.classify(image(), image())).category == 'UNCERTAIN'
    assert run(fixture.confirm(image(), image(), [{'track_id': 'one'}])).state == 'UNCERTAIN'
    typed = ClassificationResult(**classification())
    assert run(FixtureClassifier(classification=typed).classify(image(), image())) == typed


def test_confirmation_accepts_only_supplied_track_and_sends_bounding_box():
    def handler(request):
        payload = json.loads(request.content)
        assert '"track_id": "one"' in payload['input'][1]['content'][0]['text']
        assert '"bbox": [1, 2, 3, 4]' in payload['input'][1]['content'][0]['text']
        return response({'state': 'CONFIRMED', 'candidate_track_id': 'one', 'reason': 'same object'})
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await GrokClassifier('key', client=client).confirm(image(), image(), [{'track_id': 'one', 'bbox': [1, 2, 3, 4]}])
            assert isinstance(result, ConfirmationResult)
            assert result.candidate_track_id == 'one'
    run(go())


def test_queue_is_included_in_timeout_and_concurrency_is_two():
    async def go():
        started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        maximum = 0
        async def handler(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                started.set()
            try:
                await release.wait()
                return response(classification())
            finally:
                active -= 1
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            classifier = GrokClassifier('key', client=client, timeout=0.1)
            first = [asyncio.create_task(classifier.classify(image(), image())) for _ in range(2)]
            await started.wait()
            with pytest.raises(ClassificationUnavailable):
                await classifier.classify(image(), image())
            release.set()
            await asyncio.gather(*first, return_exceptions=True)
            assert maximum == 2
    run(go())


def test_oversized_and_malformed_responses_are_rejected():
    replies = [
        httpx.Response(200, content=b'x' * (1024 * 1024 + 1)),
        httpx.Response(200, json={'status': 'completed', 'output': [None]}),
        httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'message', 'content': [None]}]}),
        httpx.Response(200, json={'status': 'completed', 'output': [{'type': 'function_call', 'name': 'danger'}, {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(classification())}]}]}),
    ]
    async def go():
        for reply in replies:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: reply)) as client:
                with pytest.raises(ClassificationInvalidResponse):
                    await GrokClassifier('key', client=client).classify(image(), image())
    run(go())


@pytest.mark.parametrize('number', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_candidate_bbox_is_rejected(number):
    with pytest.raises(ValueError):
        run(GrokClassifier('key').confirm(image(), image(), [{'track_id': 'one', 'bbox': [0, 0, number, 1]}]))


def test_decompression_bomb_warning_is_rejected(monkeypatch):
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 3)
    with pytest.raises(ValueError):
        run(FixtureClassifier().classify(image(), image()))


def test_fixture_rejects_inconsistent_confirmation():
    invalid = ConfirmationResult(state='UNCERTAIN', candidate_track_id='one', reason='ambiguous')
    with pytest.raises((ValueError, ClassificationInvalidResponse)):
        run(FixtureClassifier(confirmation=invalid).confirm(image(), image(), [{'track_id': 'one'}]))


def test_response_stream_is_bounded_and_closed():
    class LargeStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False
            self.chunks = 0
        async def __aiter__(self):
            for _ in range(100):
                self.chunks += 1
                yield b'x' * 65536
        async def aclose(self):
            self.closed = True
    async def go():
        stream = LargeStream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
            with pytest.raises(ClassificationInvalidResponse):
                await GrokClassifier('key', client=client).classify(image(), image())
        assert stream.closed and stream.chunks < 100
    run(go())


def test_fixture_revalidates_constructed_models():
    invalid = ClassificationResult.model_construct(category='INVENTED', subject_kind='INANIMATE', carrying='NO', confidence='HIGH', description='x', reason='x')
    with pytest.raises(ValueError):
        FixtureClassifier(classification=invalid)
