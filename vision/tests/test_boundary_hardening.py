import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from vision.app import create_app
from vision.models import Event
from vision.publisher import Publisher
from vision.settings import Settings

TOKEN = 'test-token-123456789'
BOOT = 'a' * 32


def event(sequence=1):
    return Event(producer_boot_id=BOOT, event_id=f'{BOOT}:{sequence}', sequence=sequence,
                 type='object.updated', timestamp=1, payload={})


def test_peer_requires_explicit_separate_token():
    with pytest.raises(ValueError):
        Publisher(Settings(token=TOKEN, backend_url='https://peer.example'))
    with pytest.raises(ValueError):
        Publisher(Settings(token=TOKEN, backend_url='https://peer.example', peer_token=TOKEN))


def test_delivery_gap_stays_visible_after_success():
    async def go():
        replies = iter([httpx.Response(400), httpx.Response(200)])
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: next(replies))) as client:
            pub = Publisher(Settings(token=TOKEN, backend_url='https://peer.example', peer_token='other'))
            await pub.deliver(event(), client)
            await pub.deliver(event(2), client)
            assert pub.failed == 1 and pub.delivered == 1 and pub.last_error
    asyncio.run(go())


def test_peer_response_is_not_buffered():
    class Stream(httpx.AsyncByteStream):
        def __init__(self): self.read = False; self.closed = False
        async def __aiter__(self):
            self.read = True
            yield b'x' * 10_000_000
        async def aclose(self): self.closed = True
    async def go():
        stream = Stream()
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
            pub = Publisher(Settings(token=TOKEN, backend_url='https://peer.example', peer_token='other'))
            await pub.deliver(event(), client)
        assert not stream.read and stream.closed and pub.delivered == 1
    asyncio.run(go())


def test_json_body_is_bounded_without_content_length():
    app = create_app(Settings(token=TOKEN), run_workers=False)
    with TestClient(app) as api:
        response = api.post('/api/targets/confirm', headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}, content=b' ' * 70000)
        assert response.status_code == 413


def test_sse_subscriber_limit_and_atomic_snapshot():
    app = create_app(Settings(token=TOKEN), run_workers=False)
    route = next(r for r in app.routes if getattr(r, 'path', None) == '/api/events')
    service = app.state.service
    class Request:
        async def is_disconnected(self): return False
    async def go():
        response = await route.endpoint(Request())
        service.emit('object.updated', {})
        iterator = response.body_iterator
        first = await anext(iterator)
        snapshot = json.loads(first.split('data: ', 1)[1])
        assert snapshot['sequence'] == 0
        second = await anext(iterator)
        assert 'id: ' in second and ':1' in second
        await iterator.aclose()
        for _ in range(32): service.subscribers.add(asyncio.Queue())
        with pytest.raises(Exception) as exc:
            await route.endpoint(Request())
        assert exc.value.status_code == 503
    asyncio.run(go())


def test_snapshot_watermark_includes_events_emitted_during_evaluation():
    app = create_app(Settings(token=TOKEN), run_workers=False)
    service = app.state.service
    original = service.snapshot
    def evaluate_then_snapshot():
        service.emit('alert.target_revoked', {})
        return original()
    service.snapshot = evaluate_then_snapshot
    with TestClient(app) as api:
        response = api.get('/api/objects', headers={'Authorization': f'Bearer {TOKEN}'})
        assert response.status_code == 200
        body = response.json()
        assert body['sequence'] == service.sequence == 1
        assert body['producerBootId'] == body['bootId'] == service.boot_id
        assert body['scope'] == 'ACTIVE_OBJECTS' and body['complete'] is True
    route = next(r for r in app.routes if getattr(r, 'path', None) == '/api/events')
    class Request:
        async def is_disconnected(self): return False
    async def go():
        response = await route.endpoint(Request())
        iterator = response.body_iterator
        first = await anext(iterator)
        snapshot = json.loads(first.split('data: ', 1)[1])
        assert snapshot['sequence'] == service.sequence == 2
        await iterator.aclose()
    asyncio.run(go())


def test_chunked_json_body_limit():
    async def go():
        app = create_app(Settings(token=TOKEN), run_workers=False)
        async def chunks():
            yield b' ' * 40000
            yield b' ' * 40000
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/api/targets/confirm', headers={'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}, content=chunks())
            assert response.status_code == 413
    asyncio.run(go())


def test_upload_rejects_second_request_while_first_is_reading():
    app = create_app(Settings(token=TOKEN), run_workers=False)
    route = next(r for r in app.routes if getattr(r, 'path', None) == '/api/cameras/{camera_id}/frames')
    async def go():
        started, release = asyncio.Event(), asyncio.Event()
        class SlowRequest:
            headers = {'content-type': 'image/jpeg'}
            async def stream(self):
                started.set()
                await release.wait()
                yield b'bad'
        task = asyncio.create_task(route.endpoint('phone-1', SlowRequest(), f'Bearer {TOKEN}'))
        await started.wait()
        with pytest.raises(Exception) as exc:
            await route.endpoint('phone-1', SlowRequest(), f'Bearer {TOKEN}')
        assert exc.value.status_code == 503
        release.set()
        with pytest.raises(Exception) as exc:
            await task
        assert exc.value.status_code == 422
    asyncio.run(go())


def test_upload_decode_runs_off_event_loop():
    import threading
    app = create_app(Settings(token=TOKEN), run_workers=False)
    route = next(r for r in app.routes if getattr(r, 'path', None) == '/api/cameras/{camera_id}/frames')
    main_thread = threading.get_ident()
    observed = []
    def fake_put(*args, **kwargs):
        observed.append(threading.get_ident())
        raise ValueError('invalid')
    app.state.service.frames.put = fake_put
    class Request:
        headers = {'content-type': 'image/jpeg', 'x-frame-id': '0', 'x-session-id': 's', 'x-geometry-version': 'g', 'x-captured-at': '1'}
        async def stream(self): yield b'jpeg'
    async def go():
        with pytest.raises(Exception):
            await route.endpoint('phone-1', Request(), f'Bearer {TOKEN}')
    asyncio.run(go())
    assert observed and observed[0] != main_thread
