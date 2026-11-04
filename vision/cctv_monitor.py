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
from pathlib import Path
import sys
import time
from typing import Any, Callable, Sequence

import cv2

DEFAULT_CCTV_BASE_URL = "http://192.168.5.146:8081"
DEFAULT_CCTV_URL = f"{DEFAULT_CCTV_BASE_URL}/video"
DEFAULT_BURST_MIN_FRAMES = 5
DEFAULT_BURST_MAX_FRAMES = 10
DEFAULT_BURST_COUNT = 8
DEFAULT_BURST_BUFFER_SECONDS = 2.0


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
            raw_dets = []

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
