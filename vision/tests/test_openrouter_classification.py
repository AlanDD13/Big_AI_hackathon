"""Exercise OpenRouter's actual chat wire envelope without a live API call."""
import asyncio
import gzip
import io
import json

import httpx
import pytest
from PIL import Image

from vision.classification import (
    ClassificationInvalidResponse, ClassificationUnavailable, GrokClassifier,
    OpenRouterGrokClassifier, build_classifier_from_env,
)


def image():
    out = io.BytesIO()
    Image.new('RGB', (3, 3), 'white').save(out, 'PNG')
    return out.getvalue()


def classification(**updates):
    return dict(category='TRASH', subject_kind='INANIMATE', carrying='NO',
                confidence='MEDIUM', description='Crumpled white paper',
                reason='Visible folds', **updates)


def reply(value=None, **message_updates):
    message = {'role': 'assistant', 'content': json.dumps(value or classification())}
    message.update(message_updates)
    return {'model': 'x-ai/grok-4.7',
            'choices': [{'finish_reason': 'stop', 'message': message}]}


def run_with_payload(payload):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=payload))) as client:
            return await OpenRouterGrokClassifier('test-key', client=client).classify(image(), image())
    return asyncio.run(go())


def test_openrouter_chat_request_and_visible_description():
    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == 'https://openrouter.ai/api/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-key'
        assert request.headers['accept-encoding'] == 'identity'
        assert body['model'] == 'x-ai/grok-4.7'
        assert body['max_tokens'] == 1200
        assert body['provider']['require_parameters'] is True
        assert body['reasoning'] == {'effort': 'low', 'exclude': True}
        assert not any(key in body for key in ('models', 'tools', 'route'))
        assert body['response_format']['type'] == 'json_schema'
        schema = body['response_format']['json_schema']
        assert schema['strict'] is True
        assert schema['schema']['additionalProperties'] is False
        content = body['messages'][1]['content']
        assert content[0]['type'] == 'text'
        assert 'crumpled' in content[0]['text'] and 'weight' in content[0]['text']
        assert len(content) == 3
        for part in content[1:]:
            assert part['type'] == 'image_url'
            assert part['image_url']['url'].startswith('data:image/png;base64,')
        assert 'test-key' not in request.content.decode()
        return httpx.Response(200, json=reply())
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = OpenRouterGrokClassifier('test-key', client=client)
            assert adapter.provider == 'grok' and adapter.transport == 'openrouter'
            result = await adapter.classify(image(), image())
            assert result.description == 'Crumpled white paper'
            assert len(result.model_dump()) == 6
    asyncio.run(go())


@pytest.mark.parametrize('kind', ['PERSON', 'ANIMAL', 'UNCERTAIN'])
def test_living_and_uncertain_results_remain_typed(kind):
    payload = classification()
    payload.update(category='OTHER', subject_kind=kind, carrying='UNCERTAIN')
    assert run_with_payload(reply(payload)).subject_kind == kind


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(error={'message': 'upstream private detail'}),
    lambda p: p['choices'][0].update(finish_reason='length'),
    lambda p: p['choices'][0].update(finish_reason='content_filter'),
    lambda p: p['choices'][0]['message'].update(refusal='No'),
    lambda p: p['choices'][0].update(error={'message': 'upstream private detail'}),
])
def test_unavailable_replies_fail_closed_without_provider_details(mutate):
    payload = reply()
    mutate(payload)
    with pytest.raises(ClassificationUnavailable, match='^Classification unavailable$'):
        run_with_payload(payload)


@pytest.mark.parametrize('mutate', [
    lambda p: p['choices'][0]['message'].update(tool_calls=[{'function': 'move'}]),
    lambda p: p['choices'][0]['message'].update(function_call={'name': 'move'}),
    lambda p: p['choices'][0]['message'].update(content=[]),
    lambda p: p['choices'][0]['message'].update(role='tool'),
    lambda p: p['choices'].append(p['choices'][0]),
    lambda p: p.update(model='openai/gpt-4o'),
    lambda p: p['choices'][0]['message'].update(content='{}'),
])
def test_invalid_or_wrong_model_results_rejected(mutate):
    payload = reply()
    mutate(payload)
    with pytest.raises(ClassificationInvalidResponse):
        run_with_payload(payload)


def test_confirmation_uses_candidates_and_rejects_unknown_track():
    def handler(request):
        body = json.loads(request.content)
        assert '"track_id": "one"' in body['messages'][1]['content'][0]['text']
        assert body['response_format']['json_schema']['name'] == 'ConfirmationResult'
        return httpx.Response(200, json=reply({'state': 'CONFIRMED',
                                             'candidate_track_id': 'unknown', 'reason': 'similar'}))
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ClassificationInvalidResponse):
                await OpenRouterGrokClassifier('key', client=client).confirm(
                    image(), image(), [{'track_id': 'one', 'bbox': [1, 2, 3, 4]}])
    asyncio.run(go())


@pytest.mark.parametrize('response', [httpx.Response(401), httpx.Response(429), httpx.Response(503)])
def test_http_failures_are_unavailable(response):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
            with pytest.raises(ClassificationUnavailable):
                await OpenRouterGrokClassifier('key', client=client).classify(image(), image())
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
                await OpenRouterGrokClassifier('key', client=client).classify(image(), image())
        assert stream.closed and stream.chunks < 100
    asyncio.run(go())


def test_key_image_and_model_validation():
    with pytest.raises(ClassificationUnavailable):
        asyncio.run(OpenRouterGrokClassifier().classify(image(), image()))
    with pytest.raises(ValueError):
        asyncio.run(OpenRouterGrokClassifier('key').classify(b'not-image', image()))
    with pytest.raises(ValueError):
        OpenRouterGrokClassifier('key', model='openai/gpt-4o')


def test_environment_selection_is_explicit_and_never_uses_generic_model():
    assert isinstance(build_classifier_from_env({}), GrokClassifier)
    assert type(build_classifier_from_env({'XAI_API_KEY': 'x', 'OPENROUTER_API_KEY': 'o'})) is GrokClassifier
    routed = build_classifier_from_env({'OPENROUTER_API_KEY': 'o', 'OPENROUTER_MODEL': 'not-grok'})
    assert isinstance(routed, OpenRouterGrokClassifier) and routed._model == 'x-ai/grok-4.7'
    assert isinstance(build_classifier_from_env({'GROK_VISION_PROVIDER': 'openrouter', 'XAI_API_KEY': 'x'}), OpenRouterGrokClassifier)
    assert type(build_classifier_from_env({'GROK_VISION_PROVIDER': 'xai', 'OPENROUTER_API_KEY': 'o'})) is GrokClassifier
    with pytest.raises(ValueError):
        build_classifier_from_env({'GROK_VISION_PROVIDER': 'typo'})


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
                    await OpenRouterGrokClassifier('key', client=client).classify(image(), image())
    asyncio.run(go())


def test_provider_network_errors_are_sanitized():
    def handler(request):
        raise httpx.ReadTimeout('secret provider detail', request=request)
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ClassificationUnavailable, match='^Classification unavailable$'):
                await OpenRouterGrokClassifier('key', client=client).classify(image(), image())
    asyncio.run(go())


def test_openrouter_queue_counts_toward_deadline_and_limits_concurrency():
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
            classifier = OpenRouterGrokClassifier('key', client=client, timeout=0.1)
            first = [asyncio.create_task(classifier.classify(image(), image())) for _ in range(2)]
            await started.wait()
            with pytest.raises(ClassificationUnavailable):
                await classifier.classify(image(), image())
            release.set()
            await asyncio.gather(*first, return_exceptions=True)
            assert maximum == 2
    asyncio.run(go())


def test_openrouter_vision_model_precedence_and_bare_xai_fallback():
    env = {'OPENROUTER_API_KEY': 'key', 'GROK_VISION_MODEL': 'grok-4.7'}
    assert build_classifier_from_env(env)._model == 'x-ai/grok-4.7'
    env['GROK_VISION_MODEL'] = 'x-ai/grok-4-fast'
    assert build_classifier_from_env(env)._model == 'x-ai/grok-4-fast'
    env['OPENROUTER_VISION_MODEL'] = 'x-ai/grok-4.7'
    assert build_classifier_from_env(env)._model == 'x-ai/grok-4.7'
    env['OPENROUTER_VISION_MODEL'] = 'openai/gpt-4o'
    with pytest.raises(ValueError):
        build_classifier_from_env(env)


def test_pickup_estimate_receives_robot_limits_without_claiming_measured_object_size():
    from vision.classification import VisualPickupResult
    value = {'object_form': 'CRUMPLED', 'visual_suitability': 'POSSIBLE',
             'reason': 'Visible raised folds; actual grasp needs physical validation'}
    def handler(request):
        body = json.loads(request.content)
        assert body['response_format']['json_schema']['name'] == 'VisualPickupResult'
        instruction = body['messages'][1]['content'][0]['text']
        profile = json.loads(instruction.split('Robot parameters (trusted configuration): ', 1)[1])
        assert profile['usableOpeningMm'] == 90
        assert profile['conservativePayloadG'] == 200
        assert profile['nominalGripForceN'] == 5
        assert profile['horizontalArmTravelMm'] == 220
        assert profile['objectSizeMeasured'] is False and profile['unitCalibrated'] is False
        assert 'not claim dimensions or weight are measured' in instruction
        return httpx.Response(200, json=reply(value))
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await OpenRouterGrokClassifier('key', client=client).assess_pickup(image(), image())
            assert isinstance(result, VisualPickupResult)
            assert result.robot_grasp_estimate == 'UNKNOWN'
            assert result.estimate_confidence == 'LOW'
            assert result.object_form == 'CRUMPLED'
    asyncio.run(go())


def test_joint_schema_requires_fields_in_nested_objects():
    from vision.classification import ObjectAssessment, _strict_schema
    schema = _strict_schema(ObjectAssessment)
    assert set(schema['required']) == {'classification', 'pickup'}
    pickup = schema['$defs']['VisualPickupResult']
    assert set(pickup['required']) == set(pickup['properties'])
    assert {'robot_grasp_estimate', 'estimate_confidence', 'assumptions', 'required_checks'} <= set(pickup['required'])


def test_visual_pickup_rejects_definitive_safe_and_weight_fields():
    from vision.classification import VisualPickupResult
    for value in [
        {'object_form': 'FLAT_SHEET', 'visual_suitability': 'SAFE', 'reason': 'fine'},
        {'object_form': 'OTHER', 'visual_suitability': 'POSSIBLE', 'reason': 'fine', 'weight': 3},
    ]:
        with pytest.raises(ValueError):
            VisualPickupResult.model_validate(value)


def test_fixture_does_not_invent_pickup_evidence():
    from vision.classification import FixtureClassifier
    with pytest.raises(ClassificationUnavailable):
        asyncio.run(FixtureClassifier().assess_pickup(image(), image()))


def test_direct_xai_can_return_separate_visual_pickup_advisory():
    value = {'object_form': 'FLAT_SHEET', 'visual_suitability': 'UNSUITABLE',
             'reason': 'Flat sheet lies flush against the floor'}
    def handler(request):
        body = json.loads(request.content)
        assert body['text']['format']['name'] == 'VisualPickupResult'
        assert str(request.url) == 'https://api.x.ai/v1/responses'
        return httpx.Response(200, json={'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(value)}]}]})
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await GrokClassifier('key', client=client).assess_pickup(image(), image())
            assert result.visual_suitability == 'UNSUITABLE'
    asyncio.run(go())
