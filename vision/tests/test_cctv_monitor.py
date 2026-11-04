import base64
import numpy as np
import pytest

from vision import cctv_monitor
from vision.perception import Detection


class _FakeCapture:
    def __init__(self, opened: bool, frames: list[tuple[bool, object]]):
        self._opened = opened
        self._frames = list(frames)
        self.read_calls = 0
        self.released = False

    def isOpened(self) -> bool:
        return self._opened

    def read(self):
        self.read_calls += 1
        if not self._frames:
            return False, None
        return self._frames.pop(0)

    def release(self) -> None:
        self.released = True


def _sample_frame(color: tuple[int, int, int] = (20, 120, 240)) -> np.ndarray:
    frame = np.zeros((16, 24, 3), dtype=np.uint8)
    frame[:, :] = color
    return frame


def test_get_latest_frame_unopened_returns_none_and_releases(monkeypatch):
    fake = _FakeCapture(opened=False, frames=[])
    slept: list[float] = []
    monkeypatch.setattr(cctv_monitor.cv2, "VideoCapture", lambda url: fake)
    monkeypatch.setattr(cctv_monitor.time, "sleep", lambda s: slept.append(s))

    result = cctv_monitor.get_latest_frame()

    assert result is None
    assert fake.released is True
    assert fake.read_calls == 0
    assert slept == []


def test_get_latest_frame_stream_drops_during_discard_returns_none_and_releases(monkeypatch):
    frames = [
        (True, _sample_frame()),
        (True, _sample_frame()),
        (False, None),
        (True, _sample_frame()),
    ]
    fake = _FakeCapture(opened=True, frames=frames)
    slept: list[float] = []
    monkeypatch.setattr(cctv_monitor.cv2, "VideoCapture", lambda url: fake)
    monkeypatch.setattr(cctv_monitor.time, "sleep", lambda s: slept.append(s))

    result = cctv_monitor.get_latest_frame()

    assert result is None
    assert fake.released is True
    assert fake.read_calls == 3
    assert slept == [1.0]


def test_get_latest_frame_happy_path_sleeps_discards_five_and_returns_sixth(monkeypatch):
    discarded = [_sample_frame((i, i, i)) for i in range(5)]
    sixth = _sample_frame((10, 200, 30))
    frames = [(True, f) for f in discarded] + [(True, sixth)]
    fake = _FakeCapture(opened=True, frames=frames)
    captured_urls: list[str] = []
    slept: list[float] = []

    def fake_videocapture(url: str):
        captured_urls.append(url)
        return fake

    monkeypatch.setattr(cctv_monitor.cv2, "VideoCapture", fake_videocapture)
    monkeypatch.setattr(cctv_monitor.time, "sleep", lambda s: slept.append(s))

    result = cctv_monitor.get_latest_frame()

    assert captured_urls == ["http://192.168.5.146:8081/video"]
    assert slept == [1.0]
    assert fake.read_calls == 6
    assert fake.released is True
    assert result is sixth


def test_encode_frame_to_base64_produces_valid_jpeg_and_rejects_empty():
    frame = _sample_frame()
    encoded = cctv_monitor.encode_frame_to_base64(frame)

    assert isinstance(encoded, str)
    raw = base64.b64decode(encoded.encode("ascii"), validate=True)
    assert raw.startswith(b"\xff\xd8")
    assert raw.endswith(b"\xff\xd9")

    with pytest.raises(ValueError):
        cctv_monitor.encode_frame_to_base64(None)

    with pytest.raises(ValueError):
        cctv_monitor.encode_frame_to_base64(np.zeros((0, 0, 3), dtype=np.uint8))


def test_main_writes_debug_jpeg_and_prints_prefix_on_success(monkeypatch, tmp_path, capsys):
    frame = _sample_frame()
    monkeypatch.setattr(cctv_monitor, "get_latest_frame", lambda url=cctv_monitor.DEFAULT_CCTV_URL: frame)
    out_file = tmp_path / "debug_cctv.jpg"

    rc = cctv_monitor.main(output_path=out_file)

    assert rc == 0
    captured = capsys.readouterr()
    expected_b64 = cctv_monitor.encode_frame_to_base64(frame)
    assert captured.out.strip() == expected_b64[:50]
    assert out_file.exists()
    written = out_file.read_bytes()
    assert written.startswith(b"\xff\xd8")
    assert written == base64.b64decode(expected_b64.encode("ascii"))


def test_main_exits_cleanly_without_writing_file_on_unreachable_stream(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cctv_monitor, "get_latest_frame", lambda url=cctv_monitor.DEFAULT_CCTV_URL: None)
    out_file = tmp_path / "debug_cctv.jpg"

    rc = cctv_monitor.main(output_path=out_file)

    assert rc == 1
    assert not out_file.exists()
    captured = capsys.readouterr()
    assert "unavailable" in (captured.err + captured.out).lower()


def test_get_frame_burst_happy_path_returns_5_to_10_frames_and_releases(monkeypatch):
    discarded = [_sample_frame((i, i, i)) for i in range(5)]
    burst_frames = [_sample_frame((10 + i, 50 + i, 100 + i)) for i in range(7)]
    frames = [(True, f) for f in discarded + burst_frames]
    fake = _FakeCapture(opened=True, frames=frames)
    slept: list[float] = []

    monkeypatch.setattr(cctv_monitor.cv2, "VideoCapture", lambda url: fake)
    monkeypatch.setattr(cctv_monitor.time, "sleep", lambda s: slept.append(s))

    result = cctv_monitor.get_frame_burst(burst_count=7, min_frames=5, max_frames=10, buffer_seconds=2.0)

    assert slept == [1.0]
    assert fake.released is True
    assert fake.read_calls == 12
    assert len(result) == 7
    assert all(r is expected for r, expected in zip(result, burst_frames))


def test_get_frame_burst_unopened_or_short_stream_returns_empty_and_releases(monkeypatch):
    unopened = _FakeCapture(opened=False, frames=[])
    monkeypatch.setattr(cctv_monitor.cv2, "VideoCapture", lambda url: unopened)
    assert cctv_monitor.get_frame_burst() == []
    assert unopened.released is True

    # Stream drops after 3 burst frames (< min_frames=5): must fail closed with []
    discarded = [(True, _sample_frame()) for _ in range(5)]
    short_burst = [(True, _sample_frame()) for _ in range(3)] + [(False, None)]
    short_cap = _FakeCapture(opened=True, frames=discarded + short_burst)
    monkeypatch.setattr(cctv_monitor.cv2, "VideoCapture", lambda url: short_cap)
    monkeypatch.setattr(cctv_monitor.time, "sleep", lambda s: None)

    assert cctv_monitor.get_frame_burst(burst_count=8, min_frames=5, max_frames=10) == []
    assert short_cap.released is True


def test_get_frame_burst_stops_at_buffer_seconds_once_min_frames_collected(monkeypatch):
    discarded = [(True, _sample_frame()) for _ in range(5)]
    burst_frames = [(True, _sample_frame((i, 20, 40))) for i in range(10)]
    fake = _FakeCapture(opened=True, frames=discarded + burst_frames)
    clock = {"t": 100.0}

    def fake_monotonic():
        clock["t"] += 0.35
        return clock["t"]

    monkeypatch.setattr(cctv_monitor.cv2, "VideoCapture", lambda url: fake)
    monkeypatch.setattr(cctv_monitor.time, "sleep", lambda s: None)
    monkeypatch.setattr(cctv_monitor.time, "monotonic", fake_monotonic)

    result = cctv_monitor.get_frame_burst(burst_count=10, min_frames=5, max_frames=10, buffer_seconds=2.0)

    assert 5 <= len(result) < 10
    assert fake.released is True


def test_build_burst_payload_packages_multi_frame_detections_and_highest_confidence_frame():
    frames = [_sample_frame((i * 10, 80, 160)) for i in range(5)]
    detections_per_frame = [
        [Detection("bottle", 0.62, (10.0, 12.0, 30.0, 50.0), (20.0, 50.0))],
        [Detection("bottle", 0.78, (11.0, 12.0, 31.0, 50.0), (21.0, 50.0))],
        [Detection("bottle", 0.94, (10.5, 12.0, 30.5, 50.0), (20.5, 50.0))],
        [],
        [Detection("bottle", 0.81, (10.0, 12.5, 30.0, 50.5), (20.0, 50.5))],
    ]

    payload = cctv_monitor.build_burst_payload(frames, frame_detections=detections_per_frame)

    assert payload["frameCount"] == 5
    assert len(payload["frames"]) == 5
    assert len(payload["multiFrameDetections"]) == 4
    best = payload["highestConfidenceFrame"]
    assert best["frameIndex"] == 2
    assert best["maxConfidence"] == pytest.approx(0.94)
    assert best["detections"][0]["contactPointPx"] == [20.5, 50.0]


def test_burst_helpers_fail_closed_on_empty_burst_tie_unknown_sector_or_zero_origin():
    frames = [_sample_frame((i * 15, 40, 90)) for i in range(5)]

    # Empty burst or fewer than 5 frames fails
    with pytest.raises(ValueError, match="between 5 and 10"):
        cctv_monitor.build_burst_payload([])
    with pytest.raises(ValueError, match="between 5 and 10"):
        cctv_monitor.build_burst_payload(frames[:4])

    # No detections across burst fails instead of inventing a target
    with pytest.raises(ValueError, match="no valid detections"):
        cctv_monitor.build_burst_payload(frames, frame_detections=[[] for _ in range(5)])

    # Tie across frames fails closed
    tied_detections = [
        [{"label": "cup", "score": 0.88, "bbox": [5, 5, 20, 25], "contact_point": [12.5, 25.0]}],
        [{"label": "cup", "score": 0.88, "bbox": [6, 5, 21, 25], "contact_point": [13.5, 25.0]}],
        [],
        [],
        [],
    ]
    with pytest.raises(ValueError, match="Tie"):
        cctv_monitor.build_burst_payload(frames, frame_detections=tied_detections)

    # Unknown sector label or (0, 0) fallback coordinate is rejected
    unknown_sector = [
        [{"label": "cup", "score": 0.91, "bbox": [5, 5, 20, 25], "contact_point": [12.5, 25.0], "sector": "unknown"}],
        [],
        [],
        [],
        [],
    ]
    with pytest.raises(ValueError, match="sector"):
        cctv_monitor.build_burst_payload(frames, frame_detections=unknown_sector)

    zero_contact = [
        [{"label": "cup", "score": 0.91, "bbox": [5, 5, 20, 25], "contact_point": [0.0, 0.0]}],
        [],
        [],
        [],
        [],
    ]
    with pytest.raises(ValueError, match="0, 0"):
        cctv_monitor.build_burst_payload(frames, frame_detections=zero_contact)
