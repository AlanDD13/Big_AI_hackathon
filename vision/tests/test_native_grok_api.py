"""Native xAI wire and configuration only; no paid requests."""
import io
import gzip
import json
import asyncio
import httpx
from PIL import Image
import pytest
from vision.classification import GrokClassifier, ClassificationInvalidResponse, ClassificationUnavailable, build_classifier_from_env


def image():
    data = io.BytesIO()
    Image.new('RGB', (4, 4), 'white').save(data, 'JPEG')
    return data.getvalue()


def result():
    return {'category': 'TRASH', 'subject_kind': 'INANIMATE', 'carrying': 'NO',
            'confidence': 'HIGH', 'description': 'Empty can', 'reason': 'Mock evidence'}


def test_factory_requires_native_provider_and_never_borrows_router_key():
    native = build_classifier_from_env({'XAI_API_KEY': 'native', 'OPENROUTER_API_KEY': 'router'})
    assert native._key == 'native' and native._model == 'grok-4.7'
    assert native._url == 'https://api.x.ai/v1/responses'
    assert not build_classifier_from_env({'OPENROUTER_API_KEY': 'router'})._key
    with pytest.raises(ValueError, match='native'):
        build_classifier_from_env({'GROK_VISION_PROVIDER': 'openrouter'})


@pytest.mark.parametrize('model', ['google/gemini-model', 'x-ai/grok-4.7', 'gpt-model'])
def test_native_classifier_rejects_foreign_model_ids(model):
    with pytest.raises(ValueError):
        GrokClassifier('key', model=model)


def test_native_request_and_actual_model_metadata():
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == 'https://api.x.ai/v1/responses'
        assert request.headers['authorization'] == 'Bearer native-key'
        assert body['model'] == 'grok-4.7'
        assert body['reasoning'] == {'effort': 'low'}
        assert body['text']['format']['strict'] is True
        assert 'provider' not in body
        return httpx.Response(200, json={'status': 'completed', 'model': 'grok-4.7',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result())}]}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            classifier = GrokClassifier('native-key', client=client)
            assert (await classifier.classify(image(), image())).category == 'TRASH'
            assert classifier.last_model == 'grok-4.7'
    asyncio.run(run())


def reply(value=None):
    return {'status': 'completed', 'model': 'grok-4.7',
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(value or result())}]}]}


@pytest.mark.parametrize('response', [httpx.Response(401), httpx.Response(429), httpx.Response(503)])
def test_http_failures_are_unavailable(response):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
            with pytest.raises(ClassificationUnavailable):
                await GrokClassifier('key', client=client).classify(image(), image())
    asyncio.run(go())


def test_oversized_stream_closed_before_entire_body_read():
    class LargeStream(httpx.AsyncByteStream):
        closed = False
        chunks = 0
        async def __aiter__(self):
            for _ in range(100):
                self.chunks += 1
                yield b'x' * 65536
        async def aclose(self):
            self.closed = True
    async def go():
        stream = LargeStream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, stream=stream))) as client:
            with pytest.raises(ClassificationInvalidResponse):
                await GrokClassifier('key', client=client).classify(image(), image())
        assert stream.closed and stream.chunks < 100
    asyncio.run(go())


def test_key_image_and_model_validation():
    with pytest.raises(ClassificationUnavailable):
        asyncio.run(GrokClassifier().classify(image(), image()))
    with pytest.raises(ValueError):
        asyncio.run(GrokClassifier('key').classify(b'not-image', image()))
    with pytest.raises(ValueError):
        GrokClassifier('key', model='openai/gpt-4o')


def test_compressed_and_malformed_envelopes_are_rejected():
    responses = [
        httpx.Response(200, content=b'not-json'),
        httpx.Response(200, content=gzip.compress(json.dumps(reply()).encode()), headers={'content-encoding': 'gzip'}),
        httpx.Response(200, content=b'x' * (1024 * 1024 + 1)),
    ]
    async def go():
        for response in responses:
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
                with pytest.raises(ClassificationInvalidResponse):
                    await GrokClassifier('key', client=client).classify(image(), image())
    asyncio.run(go())


def test_provider_network_errors_are_sanitized():
    def handler(request):
        raise httpx.ReadTimeout('secret provider detail', request=request)
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ClassificationUnavailable, match='^Classification unavailable$'):
                await GrokClassifier('key', client=client).classify(image(), image())
    asyncio.run(go())


def test_native_queue_counts_toward_deadline_and_limits_concurrency():
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
                return httpx.Response(200, json=reply())
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
    asyncio.run(go())
