"""Standalone OpenCV IP-camera capture utility in ``vision/``.

This helper connects to the configured MJPEG/HTTP video stream
(defaulting to ``http://192.168.5.146:8081/video``), allows the camera
auto-exposure to settle, discards buffered initial frames, and returns either:
- a single live frame (``get_latest_frame``), or
- a 5-10 frame / 2-second burst buffer (``get_frame_burst``) together with
  multi-frame detection packaging and strict highest-confidence frame selection
  (``select_highest_confidence_frame``, ``build_burst_payload``).

Contract & safety boundaries:
- Never fabricates or substitutes frames when the camera stream is unreachable,
  asleep, or drops mid-capture; returns ``None`` (single) or ``[]`` (burst)
  cleanly and always releases the underlying ``cv2.VideoCapture`` handle.
- Empty bursts, missing detections, or confidence ties fail closed by raising
  ``ValueError`` and never return ``(0, 0)`` fallback coordinates.
- Captures via ``cctv_monitor.py`` are standalone capture/debug utilities and
  do NOT mint a ``vision-v1`` ``sessionId``, ``geometryVersion``, or ``frameId``,
  nor do they bypass the ``vision-v1`` 5 s / 10-sample calibrated metric gate.
- ``mapping/`` still does not open camera streams directly.
"""

from __future__ import annotations

import base64
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable, Sequence

import cv2

from .perception import Detection, YoloDetector, _living, resolve_device

DEFAULT_CCTV_BASE_URL = "http://192.168.5.146:8081"
DEFAULT_CCTV_URL = f"{DEFAULT_CCTV_BASE_URL}/video"
DEFAULT_BURST_MIN_FRAMES = 5
DEFAULT_BURST_MAX_FRAMES = 10
DEFAULT_BURST_COUNT = 8
DEFAULT_BURST_BUFFER_SECONDS = 2.0
DEFAULT_YOLO_MODEL = "yoloe-26s-seg-pf.pt"

_DETECTOR_CACHE: dict[tuple[str, str, float], YoloDetector] = {}


def resolve_local_model_path(model: str = DEFAULT_YOLO_MODEL) -> str:
    """Resolve local YOLOE weights from vision/.local/ if present, otherwise return model name."""
    candidate = Path(model)
    if candidate.exists():
        return str(candidate)
    bundled = Path(__file__).resolve().parent / ".local" / model
    if bundled.exists():
        return str(bundled)
    return model


def get_default_detector(
    model: str | None = None,
    device: str | None = None,
    confidence: float = 0.25,
) -> YoloDetector:
    """Return a cached cross-platform YoloDetector (CUDA on Windows/Linux GPU, MPS on Mac, or CPU)."""
    resolved_model = resolve_local_model_path(model or os.getenv("VISION_MODEL", DEFAULT_YOLO_MODEL))
    resolved_device = resolve_device(device or os.getenv("VISION_DEVICE", "auto"))
    key = (resolved_model, resolved_device, float(confidence))
    if key not in _DETECTOR_CACHE:
        _DETECTOR_CACHE[key] = YoloDetector(
            model=resolved_model,
            device=resolved_device,
            confidence=confidence,
        )
    return _DETECTOR_CACHE[key]


def detect_frame_remote(
    jpeg_bytes: bytes,
    remote_url: str,
    token: str,
    timeout: float = 10.0,
) -> list[Detection]:
    """Send a JPEG frame to Alan's Mac vision server (/api/detect-frame) for MPS GPU YOLOE inference."""
    if not jpeg_bytes:
        raise ValueError("Cannot send empty JPEG bytes for remote detection")
    if not remote_url or not remote_url.strip():
        raise ValueError("remote_url is required for remote detection")
    if not token or len(token.strip()) < 16:
        raise ValueError("Valid VISION_TOKEN or PEER_TOKEN (>=16 chars) is required for remote detection")
    import httpx
    endpoint = f"{remote_url.rstrip('/')}/api/detect-frame"
    response = httpx.post(
        endpoint,
        content=jpeg_bytes,
        headers={
            "Authorization": f"Bearer {token.strip()}",
            "Content-Type": "image/jpeg",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    raw_items = payload.get("detections") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        raise ValueError("Remote detection response missing 'detections' list")
    results: list[Detection] = []
    for item in raw_items:
        norm = _normalize_detection(item)
        if _living(norm["detectorLabel"]):
            continue
        bbox = tuple(norm["bboxPx"]) if norm["bboxPx"] is not None else (0.0, 0.0, 0.0, 0.0)
        contact = tuple(norm["contactPointPx"]) if norm["contactPointPx"] is not None else None
        results.append(
            Detection(
                norm["detectorLabel"],
                norm["detectorScore"],
                bbox,
                contact,
            )
        )
    return results


def detect_frame_objects(
    frame: Any,
    detector: YoloDetector | None = None,
    remote_url: str | None = None,
    token: str | None = None,
) -> list[Detection]:
    """Run YOLOE detection on an OpenCV BGR frame (offloads to VISION_REMOTE_URL if set, else local)."""
    if not _is_valid_frame(frame):
        raise ValueError("Cannot run object detection on an empty or None frame")
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok or buf is None or getattr(buf, "size", 0) == 0:
        raise ValueError("Failed to encode frame as JPEG for YOLO detection")
    jpeg_bytes = buf.tobytes()
    target_remote = (remote_url if remote_url is not None else os.getenv("VISION_REMOTE_URL", "")).strip()
    if detector is None and target_remote:
        auth_token = (
            token
            or os.getenv("VISION_TOKEN")
            or os.getenv("PEER_TOKEN")
            or ""
        )
        return detect_frame_remote(jpeg_bytes, remote_url=target_remote, token=auth_token)
    active_detector = detector or get_default_detector()
    detections = active_detector.detect(jpeg_bytes)
    return [d for d in detections if not _living(d.label)]


def _is_valid_frame(frame: Any) -> bool:
    return frame is not None and getattr(frame, "size", 0) > 0


def get_latest_frame(
    url: str = DEFAULT_CCTV_URL,
    warmup_seconds: float = 1.0,
    discard_frames: int = 5,
) -> Any | None:
    """Capture a single fresh frame from the IP camera stream.

    Opens ``cv2.VideoCapture(url)``, waits ``warmup_seconds`` for auto-exposure,
    discards ``discard_frames`` initial frames from the buffer, and returns the
    next frame, or ``None`` if opening or reading fails at any step.
    """
    cap = cv2.VideoCapture(url)
    try:
        if not cap.isOpened():
            return None
        if warmup_seconds > 0:
            time.sleep(warmup_seconds)
        for _ in range(discard_frames):
            ok, _ = cap.read()
            if not ok:
                return None
        ok, frame = cap.read()
        if not ok or not _is_valid_frame(frame):
            return None
        return frame
    finally:
        cap.release()


def get_frame_burst(
    url: str = DEFAULT_CCTV_URL,
    burst_count: int = DEFAULT_BURST_COUNT,
    min_frames: int = DEFAULT_BURST_MIN_FRAMES,
    max_frames: int = DEFAULT_BURST_MAX_FRAMES,
    buffer_seconds: float = DEFAULT_BURST_BUFFER_SECONDS,
    warmup_seconds: float = 1.0,
    discard_frames: int = 5,
) -> list[Any]:
    """Capture a burst of 5-10 frames (within a 2-second buffer window) from the IP camera.

    Opens ``cv2.VideoCapture(url)``, waits ``warmup_seconds`` for auto-exposure,
    discards ``discard_frames`` initial buffered frames, and reads up to
    ``burst_count`` frames before ``buffer_seconds`` elapses.

    If the camera is closed/asleep, drops mid-burst, or yields fewer than
    ``min_frames`` valid frames within ``buffer_seconds``, returns ``[]`` cleanly
    without fabricating frames and always releases ``cv2.VideoCapture``.
    """
    if (
        type(min_frames) is not int
        or type(max_frames) is not int
        or type(burst_count) is not int
        or min_frames < 1
        or max_frames < min_frames
        or not (min_frames <= burst_count <= max_frames)
    ):
        raise ValueError("burst_count must be an integer between min_frames and max_frames")
    if not isinstance(buffer_seconds, (int, float)) or isinstance(buffer_seconds, bool) or not math.isfinite(buffer_seconds) or buffer_seconds <= 0:
        raise ValueError("buffer_seconds must be a positive finite number")

    cap = cv2.VideoCapture(url)
    try:
        if not cap.isOpened():
            return []
        if warmup_seconds > 0:
            time.sleep(warmup_seconds)
        for _ in range(discard_frames):
            ok, _ = cap.read()
            if not ok:
                return []

        frames: list[Any] = []
        deadline = time.monotonic() + float(buffer_seconds)
        while len(frames) < burst_count:
            ok, frame = cap.read()
            if not ok or not _is_valid_frame(frame):
                return []
            frames.append(frame)
            if time.monotonic() >= deadline:
                break

        if len(frames) < min_frames:
            return []
        return frames
    finally:
        cap.release()


def encode_frame_to_base64(frame: Any) -> str:
    """Encode an OpenCV image frame as JPEG and return an ASCII base64 string."""
    if not _is_valid_frame(frame):
        raise ValueError("Cannot encode an empty or None frame")
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok or buf is None or getattr(buf, "size", 0) == 0:
        raise ValueError("Failed to encode frame as JPEG")
    return base64.b64encode(buf.tobytes()).decode("ascii")


def _normalize_detection(raw: Any) -> dict[str, Any]:
    """Validate and normalize a single detection item without inventing (0, 0) coordinates."""
    if isinstance(raw, dict):
        label = raw.get("label") or raw.get("detectorLabel")
        score = raw.get("score", raw.get("detectorScore", raw.get("confidence")))
        bbox = raw.get("bbox", raw.get("bboxPx"))
        contact = raw.get("contact_point", raw.get("contactPointPx"))
        sector = raw.get("sector")
    else:
        label = getattr(raw, "label", None)
        score = getattr(raw, "score", None)
        bbox = getattr(raw, "bbox", None)
        contact = getattr(raw, "contact_point", None)
        sector = getattr(raw, "sector", None)

    if not isinstance(label, str) or not label.strip():
        raise ValueError("Detection label must be a non-empty string")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not (0.0 <= float(score) <= 1.0):
        raise ValueError("Detection score must be a finite number in [0, 1]")

    normalized_bbox: list[float] | None = None
    if bbox is not None:
        if len(bbox) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox):
            raise ValueError("Detection bbox must have 4 finite numbers")
        x1, y1, x2, y2 = (float(v) for v in bbox)
        if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
            raise ValueError("Detection bbox coordinates must be non-negative with x1 < x2 and y1 < y2")
        normalized_bbox = [x1, y1, x2, y2]

    normalized_contact: list[float] | None = None
    if contact is not None:
        if len(contact) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in contact):
            raise ValueError("Detection contact_point must have 2 finite numbers or be None")
        cx, cy = float(contact[0]), float(contact[1])
        if (cx, cy) == (0.0, 0.0):
            raise ValueError("Refusing (0, 0) fallback contact point")
        normalized_contact = [cx, cy]

    result: dict[str, Any] = {
        "detectorLabel": label.strip(),
        "detectorScore": float(score),
        "bboxPx": normalized_bbox,
        "contactPointPx": normalized_contact,
    }
    if sector is not None:
        if not isinstance(sector, str) or not sector.strip() or sector.strip().lower() == "unknown":
            raise ValueError("Unknown or empty sector label is not permitted")
        result["sector"] = sector.strip()
    return result


def select_highest_confidence_frame(frame_entries: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Return the single burst frame entry with the strictly highest detection confidence.

    Fails closed by raising ``ValueError`` on an empty burst, when no detections
    are present, or when two or more frames tie for the highest confidence.
    Never invents a default frame or ``(0, 0)`` position.
    """
    if not frame_entries:
        raise ValueError("Cannot select highest-confidence frame from an empty burst")

    scored_entries: list[tuple[float, dict[str, Any]]] = []
    for entry in frame_entries:
        detections = entry.get("detections") or []
        if detections:
            normalized = [_normalize_detection(d) for d in detections]
            scores = [d["detectorScore"] for d in normalized]
            top_score = max(scores)
            if sum(1 for s in scores if math.isclose(s, top_score, rel_tol=0.0, abs_tol=1e-9)) > 1:
                raise ValueError("Tie between top detections within a single burst frame")
            scored_entries.append((top_score, entry))
        elif "maxConfidence" in entry and entry["maxConfidence"] is not None:
            raw_score = entry["maxConfidence"]
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)) or not math.isfinite(raw_score) or not (0.0 <= float(raw_score) <= 1.0):
                raise ValueError("Invalid maxConfidence on burst frame entry")
            scored_entries.append((float(raw_score), entry))

    if not scored_entries:
        raise ValueError("Burst contains no valid detections")

    best_score = max(score for score, _ in scored_entries)
    winners = [entry for score, entry in scored_entries if math.isclose(score, best_score, rel_tol=0.0, abs_tol=1e-9)]
    if len(winners) != 1:
        raise ValueError("Tie for highest-confidence frame across burst")
    return winners[0]


def build_burst_payload(
    frames: Sequence[Any],
    frame_detections: Sequence[Sequence[Any]] | None = None,
    detector: Callable[[Any], Sequence[Any]] | None = None,
    min_frames: int = DEFAULT_BURST_MIN_FRAMES,
    max_frames: int = DEFAULT_BURST_MAX_FRAMES,
) -> dict[str, Any]:
    """Package a 5-10 frame burst into multi-frame detections and the highest-confidence frame.

    Raises ``ValueError`` if the burst has fewer than ``min_frames`` or more than
    ``max_frames`` frames, if any frame is invalid, if no detections exist, or
    if there is a confidence tie. Never substitutes ``(0, 0)``.
    """
    if not frames or len(frames) < min_frames or len(frames) > max_frames:
        raise ValueError(f"Burst must contain between {min_frames} and {max_frames} frames")
    if frame_detections is not None and len(frame_detections) != len(frames):
        raise ValueError("frame_detections length must match frames length")

    frame_entries: list[dict[str, Any]] = []
    multi_frame_detections: list[dict[str, Any]] = []

    for idx, frame in enumerate(frames):
        b64 = encode_frame_to_base64(frame)
        if frame_detections is not None:
            raw_dets = frame_detections[idx]
        elif detector is not None:
            raw_dets = detector(frame)
        else:
            raw_dets = detect_frame_objects(frame)

        normalized_dets = [_normalize_detection(d) for d in raw_dets]
        for det in normalized_dets:
            with_frame = dict(det, frameIndex=idx)
            multi_frame_detections.append(with_frame)

        max_conf = max((d["detectorScore"] for d in normalized_dets), default=None)
        if normalized_dets and max_conf is not None:
            if sum(1 for d in normalized_dets if math.isclose(d["detectorScore"], max_conf, rel_tol=0.0, abs_tol=1e-9)) > 1:
                raise ValueError("Tie between top detections within a single burst frame")

        frame_entries.append(
            {
                "frameIndex": idx,
                "imageBase64": b64,
                "maxConfidence": max_conf,
                "detections": normalized_dets,
            }
        )

    best_entry = select_highest_confidence_frame(frame_entries)
    return {
        "frameCount": len(frame_entries),
        "frames": frame_entries,
        "multiFrameDetections": multi_frame_detections,
        "highestConfidenceFrame": best_entry,
    }


def capture_and_detect_burst(
    url: str = DEFAULT_CCTV_URL,
    burst_count: int = DEFAULT_BURST_COUNT,
    min_frames: int = DEFAULT_BURST_MIN_FRAMES,
    max_frames: int = DEFAULT_BURST_MAX_FRAMES,
    buffer_seconds: float = DEFAULT_BURST_BUFFER_SECONDS,
    warmup_seconds: float = 1.0,
    discard_frames: int = 5,
    detector: Callable[[Any], Sequence[Any]] | None = None,
) -> dict[str, Any]:
    """Capture a 5-10 frame burst from the IP camera and run local YOLOE detection across the burst."""
    frames = get_frame_burst(
        url=url,
        burst_count=burst_count,
        min_frames=min_frames,
        max_frames=max_frames,
        buffer_seconds=buffer_seconds,
        warmup_seconds=warmup_seconds,
        discard_frames=discard_frames,
    )
    return build_burst_payload(
        frames,
        detector=detector,
        min_frames=min_frames,
        max_frames=max_frames,
    )


def main(
    url: str = DEFAULT_CCTV_URL,
    output_path: str | Path = "debug_cctv.jpg",
) -> int:
    """Capture one live frame, print the first 50 base64 chars, and save debug_cctv.jpg."""
    frame = get_latest_frame(url)
    if frame is None:
        print(f"CCTV stream unavailable or failed to read frame from {url}", file=sys.stderr)
        return 1
    b64 = encode_frame_to_base64(frame)
    print(b64[:50])
    Path(output_path).write_bytes(base64.b64decode(b64.encode("ascii")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
