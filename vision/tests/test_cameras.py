import io
import pytest
from PIL import Image
from vision.cameras import FrameStore


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr('vision.cameras.time.time', lambda: 5)


def jpeg(size=(20, 10), orientation=None):
    img = Image.new('RGB', size, 'red')
    exif = Image.Exif()
    if orientation:
        exif[274] = orientation
    out = io.BytesIO()
    img.save(out, 'JPEG', exif=exif)
    return out.getvalue()


def test_normalizes_exif_and_rejects_replay_and_geometry_change():
    store = FrameStore()
    frame = store.put('cam', 'session', 'g1', 0, 10, jpeg(orientation=6), received_at=11)
    assert (frame.width, frame.height) == (10, 20)
    assert not Image.open(io.BytesIO(frame.jpeg)).getexif()
    assert store.get('cam', 'session', 0) == frame
    with pytest.raises(ValueError):
        store.put('cam', 'session', 'g1', 0, 10, jpeg(), received_at=12)
    with pytest.raises(ValueError):
        store.put('cam', 'session', 'g1', 1, 10, jpeg(), received_at=12)
    assert store.latest('cam') == frame


def test_session_retirement_and_kind_stability():
    store = FrameStore()
    store.put('cam', 'old', 'g', 0, 1, jpeg(), received_at=1)
    store.put('cam', 'new', 'g', 0, 2, jpeg(), received_at=2)
    with pytest.raises(ValueError):
        store.put('cam', 'old', 'g', 1, 3, jpeg(), received_at=3)
    with pytest.raises(ValueError):
        store.put('cam', 'new', 'g', 1, 3, jpeg(), kind='ROVER', received_at=3)
    assert store.latest('cam').session_id == 'new'


def test_limits_and_invalid_inputs():
    store = FrameStore(max_cameras=1, max_image_bytes=1024)
    store.put('cam', 's', 'g', 0, 1, jpeg(), received_at=1)
    for args in [('other', 's', 'g', 0, 1, jpeg()), ('cam', 's', 'g', 1, 1, b'bad'), ('../x', 's', 'g', 0, 1, jpeg()), ('cam', 's', 'g', 1, float('nan'), jpeg())]:
        with pytest.raises(ValueError):
            store.put(*args, received_at=2)


def test_bounded_memory_evicts_old_frames():
    data = jpeg()
    store = FrameStore(max_bytes=1500)
    for i in range(5):
        store.put('cam', 's', 'g', i, i, data, received_at=i)
    assert store.latest('cam').frame_id == 4
    assert store.get('cam', 's', 0) is None


def test_expiration_and_new_geometry(monkeypatch):
    now = [5]
    store = FrameStore(ttl_s=3, clock=lambda: now[0])
    store.put('cam', 's', 'g0', 0, 1, jpeg(), received_at=1)
    store.put('cam', 's', 'g1', 1, 2, jpeg((10, 20)), received_at=2)
    assert store.latest('cam').geometry_version == 'g1'
    with pytest.raises(ValueError):
        store.put('cam', 's', 'g0', 2, 3, jpeg(), received_at=3)
    now[0] = 6
    assert store.latest('cam') is None


def test_bridge_requires_configured_origin():
    from vision.cameras.adapters import RoverHTTPAdapter
    for url in ('http://user:pass@localhost', 'http://example.com/path', 'http://example.com/?url=x', 'file:///tmp/x'):
        with pytest.raises(ValueError):
            RoverHTTPAdapter(url, FrameStore(), 'cam', 'session')


def test_injected_clock_keeps_explicit_received_time_in_same_time_domain():
    now = [100.0]
    store = FrameStore(ttl_s=5, clock=lambda: now[0])
    frame = store.put('cam', 's', 'g', 0, 99, jpeg(), received_at=100)
    now[0] = 104
    assert store.latest('cam') == frame
    assert store.get('cam', 's', 0) == frame
    next_frame = store.put('cam', 's', 'g', 1, 104, jpeg())
    assert next_frame.received_at == 104
    now[0] = 110
    assert store.latest('cam') is None


def test_bridge_preserves_source_clock_basis_and_geometry(monkeypatch):
    import httpx
    from vision.cameras.adapters import RoverHTTPAdapter
    headers = {'X-Camera-Id': 'cam', 'X-Session-Id': 's', 'X-Geometry-Version': 'g1', 'X-Frame-Id': '5', 'X-Captured-At': '3', 'X-Timestamp-Basis': 'RECEIVE'}
    def handle(request):
        assert request.headers['Authorization'] == 'Bearer secret'
        return httpx.Response(200, content=jpeg(), headers=headers)
    client = httpx.Client
    monkeypatch.setattr('vision.cameras.adapters.httpx.Client', lambda **kwargs: client(transport=httpx.MockTransport(handle), **kwargs))
    store = FrameStore()
    bridge = RoverHTTPAdapter('http://rover.local', store, 'cam', token='secret')
    first = bridge.read()
    assert (first.timestamp_basis, first.captured_at, first.geometry_version) == ('RECEIVE', 3, 'g1')
    headers.update({'X-Frame-Id': '6', 'X-Geometry-Version': 'g2'})
    assert bridge.read().geometry_version == 'g2'


def test_bridge_uses_source_identity_and_does_not_refresh_repeated_frames(monkeypatch):
    import httpx
    from vision.cameras.adapters import RoverHTTPAdapter
    headers = {'X-Camera-Id': 'rover', 'X-Session-Id': 'epoch1', 'X-Geometry-Version': 'g1', 'X-Frame-Id': '40', 'X-Captured-At': '10', 'X-Timestamp-Basis': 'SOURCE'}
    received = [100]
    def handle(request):
        return httpx.Response(200, content=jpeg(), headers=headers)
    client = httpx.Client
    monkeypatch.setattr('vision.cameras.adapters.httpx.Client', lambda **kw: client(transport=httpx.MockTransport(handle), **kw))
    monkeypatch.setattr('vision.cameras.adapters.time.time', lambda: received[0])
    store = FrameStore(clock=lambda: received[0])
    bridge = RoverHTTPAdapter('http://bridge.local', store, 'rover', 'unused')
    first = bridge.read()
    assert (first.session_id, first.frame_id, first.captured_at, first.received_at) == ('epoch1', 40, 10, 100)
    received[0] = 101
    assert bridge.read() is None
    assert store.latest('rover') == first
    headers['X-Frame-Id'] = '39'
    with pytest.raises(ValueError):
        bridge.read()
    headers.update({'X-Session-Id': 'epoch2', 'X-Frame-Id': '7'})
    second = bridge.read()
    assert (second.session_id, second.frame_id, second.received_at) == ('epoch2', 7, 101)
    headers.update({'X-Session-Id': 'epoch1', 'X-Frame-Id': '41'})
    with pytest.raises(ValueError):
        bridge.read()


def test_bridge_requires_complete_metadata_and_rejects_http_errors(monkeypatch):
    import httpx
    from vision.cameras.adapters import RoverHTTPAdapter
    responses = iter([httpx.Response(200, content=jpeg()), httpx.Response(503), httpx.Response(302, headers={'Location': 'http://other.local/'})])
    client = httpx.Client
    monkeypatch.setattr('vision.cameras.adapters.httpx.Client', lambda **kw: client(transport=httpx.MockTransport(lambda request: next(responses)), **kw))
    bridge = RoverHTTPAdapter('http://bridge.local', FrameStore(), 'rover', 'unused', token='secret')
    with pytest.raises(ValueError):
        bridge.read()
    with pytest.raises(httpx.HTTPStatusError):
        bridge.read()
    with pytest.raises(httpx.HTTPStatusError):
        bridge.read()
