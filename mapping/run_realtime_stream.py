"""Stream the phone camera with the convex floor grid and confirmed trash labels.

The desktop window and http://localhost:5051 open on startup. A capture thread
keeps only the newest phone frame. The first frame asks for four floor corners.
Those clicks lock a homography, and later frames only redraw that perspective
grid, so a rover on the wood cannot move the sectors. The detector still runs
a few times a second.
Grok is asked only when a box appears away from a label already cached.
Row A is the top of the detected floor. The metres are the uncalibrated
demo-grid estimate and are not MappingEvidence. includePickup stays false.
This viewer does not mint vision ids and does not post observations.
"""

from __future__ import annotations

import math
import os
import re
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import quote

import cv2
import numpy as np
from flask import Flask, Response, jsonify

MAPPING = Path(__file__).resolve().parent
sys.path.insert(0, str(MAPPING))
import auto_dynamic_tracker as tracker
import calibrate_and_test_new_photo as floor
import test_live_ip_camera as live
import test_trash_detection as trash
from grid_mapper import GRID_COLUMNS, GRID_ROWS, sector_to_coordinates

VIDEO_URL = "http://192.168.5.146:8081/video"
WINDOW = "Live Floor Grid & Trash Tracker"
CALIBRATE_WINDOW = "Select 4 Floor Corners"
CORNER_NAMES = ("Bottom-Left", "Bottom-Right", "Top-Right", "Top-Left")
ARENA = 2000.0


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _setting(name: str) -> str:
    return os.environ.get(name) or floor._load_env(floor.REPO / ".env").get(name) or ""


USE_GROK = _as_bool(_setting("USE_GROK"), True)
VIEWER_PORT = int(_setting("VIEWER_PORT") or "5051")
VIEWER_HOST = _setting("VIEWER_HOST") or "0.0.0.0"
PAGE_URL = f"http://localhost:{VIEWER_PORT}"
STALE_SECONDS = 2.0
_viewer_session = uuid.uuid4().hex
CONF_THRESHOLD = 0.20
BLACK_MEAN = 15.0
TRACK_INTERVAL = 0.3
MATCH_PX = 100.0
# Local names only. Floor brightness blobs are not trash; Grok still confirms those.
TRASH_WORDS = (
    "bottle",
    "can",
    "tin",
    "soda",
    "beverage",
    "drink",
    "redbull",
    "cup",
    "mug",
    "paper",
    "tissue",
    "wrapper",
    "carton",
    "cardboard",
    "box",
    "plastic",
    "aluminum",
    "aluminium",
    "trash",
    "garbage",
    "litter",
    "packet",
    "crumpled",
)

_lock = threading.Lock()
_stop = threading.Event()
latest_frame: np.ndarray | None = None
_latest_jpeg: bytes | None = None
_points: list[tuple[float, float]] = []
_locked_points: list[tuple[float, float]] | None = None
_locked_matrix: np.ndarray | None = None
_locked_inverse: np.ndarray | None = None
_clicks: list[tuple[float, float]] = []
_hits: list[dict[str, object]] = []
_cache: list[dict[str, object]] = []
_rejected: list[tuple[float, float, float]] = []
_pending: dict[str, object] | None = None
_model: object | None = None
_model_ready = False
_last_publish = 0.0
_frame_at = 0.0
_camera_error = ""


PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Live Floor Grid & Trash Tracker</title>
  <style>
    body { margin: 0; background: #111; color: #eee; font-family: sans-serif; }
    header { padding: 12px 16px; }
    img { width: 100%; height: auto; background: #000; }
  </style>
</head>
<body>
  <header>
    <h1>Live Floor Grid & Trash Tracker</h1>
    <p>mapId demo-grid · calibrated false · ROW_A_TOP · 0.5 m sector estimate</p>
  </header>
  <img src="/stream.mjpg" alt="live floor grid">
</body>
</html>
"""


def _stream_url() -> str:
    """Use the camera password from the environment. Never print the password."""
    user, password = tracker._camera_password()
    if not password:
        return VIDEO_URL
    return VIDEO_URL.replace(
        "://",
        f"://{quote(user or 'admin', safe='')}:{quote(password, safe='')}@",
        1,
    )


def _open_capture() -> cv2.VideoCapture | None:
    capture = cv2.VideoCapture(_stream_url())
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
    capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000)
    if capture.isOpened():
        print(f"[INFO] Connected to IP Camera Lite at {VIDEO_URL}", flush=True)
        return capture
    capture.release()
    print("videocapture_unavailable", flush=True)
    return None


def _read_bright(capture: cv2.VideoCapture) -> np.ndarray | None:
    ok, frame = capture.read()
    if not ok or frame is None:
        return None
    if float(frame.mean()) <= BLACK_MEAN:
        return None
    return frame


def _capture_loop() -> None:
    """Read forever and keep only the newest bright frame."""
    global latest_frame, _frame_at, _camera_error
    capture: cv2.VideoCapture | None = None
    failures = 0
    while not _stop.is_set():
        if capture is None or not capture.isOpened():
            capture = _open_capture()
            if capture is None:
                with _lock:
                    _camera_error = "videocapture_unavailable"
                if _stop.wait(1.0):
                    break
                continue
        frame = _read_bright(capture)
        if frame is None:
            failures += 1
            if failures > 30:
                with _lock:
                    _camera_error = "camera_read_failed"
                capture.release()
                capture = None
                failures = 0
            continue
        failures = 0
        with _lock:
            latest_frame = frame
            _frame_at = time.monotonic()
            _camera_error = ""
    if capture is not None:
        capture.release()


def _inference_device() -> str:
    """Pick MPS, CUDA, or CPU. YOLO_DEVICE=cpu|mps|cuda overrides the check."""
    requested = _setting("YOLO_DEVICE").strip().lower()
    if requested in {"cpu", "mps", "cuda"}:
        return requested
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception as exc:
        print(f"device_detect {type(exc).__name__}", flush=True)
    return "cpu"


_device = ""


def _device_once() -> str:
    global _device
    if not _device:
        _device = _inference_device()
        print(f"yolo_device {_device}", flush=True)
    return _device


def _model_once() -> object | None:
    global _model, _model_ready
    if not _model_ready:
        _model = tracker._load_model()
        _model_ready = True
        if _model is not None and hasattr(_model, "to"):
            try:
                _model.to(_device_once())
            except Exception as exc:
                print(f"yolo_device_move {type(exc).__name__}: {exc}", flush=True)
    return _model


def _yolo_boxes(image: np.ndarray) -> list[dict[str, object]]:
    model = _model_once()
    boxes: list[dict[str, object]] = []
    if model is not None:
        results = model.predict(
            source=image,
            conf=CONF_THRESHOLD,
            imgsz=640,
            device=_device_once(),
            verbose=False,
        )
        found = results[0].boxes
        names = results[0].names
        if found is not None:
            for box in found:
                x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
                boxes.append(
                    {
                        "label": str(names[int(box.cls[0])]),
                        "score": float(box.conf[0]),
                        "source": "yolo",
                        "bbox": (x1, y1, x2, y2),
                    }
                )
        return boxes
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return boxes
    for item in tracker._detect_boxes(image, encoded.tobytes(), None):
        if float(item["score"]) < CONF_THRESHOLD:
            continue
        item["source"] = "yolo"
        boxes.append(item)
    return boxes


# The prompt-free model calls the dark floor can "remote". An upright box is a can.
CAN_MISREAD = ("remote", "cell phone", "mobile phone", "flashlight", "battery")


def _can_shaped(bbox: tuple[float, float, float, float], width: int, height: int) -> bool:
    x1, y1, x2, y2 = bbox
    box_w = x2 - x1
    box_h = y2 - y1
    if box_w < 12 or box_h < 28:
        return False
    if box_w > 0.16 * width or box_h > 0.35 * height:
        return False
    return 1.4 <= (box_h / box_w) <= 4.2


def _looks_like_trash(label: str) -> bool:
    """True when a detector name is a trash word. 'floor-blob' is never trash."""
    text = label.lower()
    if "floor-blob" in text or live._banned(text):
        return False
    if "red bull" in text:
        return True
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in TRASH_WORDS)


def _label_name(classification: dict, source: str) -> str:
    """Name the object from the description, ignoring a later 'rather than' phrase."""
    head = str(classification.get("description") or "").lower().split("rather than", 1)[0]
    if "bottle" in head:
        return "plastic bottle"
    if re.search(r"\bcan\b", head):
        return "can"
    return live._grok_name(classification, source)


def _floor_contact(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    """Bottom-centre of the box, the point that sits on the floor."""
    x1, _y1, x2, y2 = bbox
    return (x1 + x2) / 2, y2


def _near(cx: float, cy: float, other_x: float, other_y: float) -> bool:
    return math.hypot(cx - other_x, cy - other_y) <= MATCH_PX


def _half(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    return cv2.resize(image, (max(1, width // 2), max(1, height // 2)), interpolation=cv2.INTER_AREA)


def _scale_box(item: dict[str, object], scale: float) -> dict[str, object]:
    x1, y1, x2, y2 = item["bbox"]
    return {**item, "bbox": (x1 * scale, y1 * scale, x2 * scale, y2 * scale)}


def _on_corner(event: int, x: int, y: int, _flags: int, _param: object) -> None:
    if event == cv2.EVENT_LBUTTONDOWN and len(_clicks) < 4:
        _clicks.append((float(x), float(y)))


def _select_corners(frame: np.ndarray) -> bool:
    """Four clicks define the arena. Order: bottom-left, bottom-right, top-right, top-left."""
    global _clicks, _locked_points, _locked_matrix, _locked_inverse
    _clicks = []
    preview = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
    scale_x = frame.shape[1] / 1280
    scale_y = frame.shape[0] / 720
    cv2.namedWindow(CALIBRATE_WINDOW, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(CALIBRATE_WINDOW, _on_corner)
    print("Click Bottom-Left, Bottom-Right, Top-Right, Top-Left", flush=True)
    while len(_clicks) < 4 and not _stop.is_set():
        canvas = preview.copy()
        cv2.putText(
            canvas,
            f"Click {CORNER_NAMES[len(_clicks)]} ({len(_clicks) + 1}/4)",
            (24, 48),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 0),
            2,
            cv2.LINE_AA,
        )
        for index, (px, py) in enumerate(_clicks):
            cv2.circle(canvas, (int(px), int(py)), 7, (0, 255, 0), -1)
        if len(_clicks) >= 2:
            chain = np.array(_clicks, dtype=np.int32).reshape(-1, 1, 2)
            cv2.polylines(canvas, [chain], False, (0, 255, 0), 2)
        cv2.imshow(CALIBRATE_WINDOW, canvas)
        if cv2.waitKey(20) & 0xFF in {ord("q"), 27}:
            cv2.destroyWindow(CALIBRATE_WINDOW)
            return False
    cv2.destroyWindow(CALIBRATE_WINDOW)
    if len(_clicks) < 4:
        return False
    full = [(px * scale_x, py * scale_y) for px, py in _clicks]
    source = np.float32(full)
    destination = np.float32([[0, ARENA], [ARENA, ARENA], [ARENA, 0], [0, 0]])
    _locked_points = full
    _locked_matrix = cv2.getPerspectiveTransform(source, destination)
    _locked_inverse = cv2.getPerspectiveTransform(destination, source)
    print("floor_locked homography corners 4", flush=True)
    print("calibrated false  mapId demo-grid  metres are a sector estimate", flush=True)
    return True


def _project(metric_points: list[list[float]]) -> np.ndarray:
    pts = np.float32(metric_points).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(pts, _locked_inverse).reshape(-1, 2)


def _sector_from_lock(cx: float, cy: float) -> str | None:
    """Name the cell of a floor-contact point in the locked top-down arena."""
    if _locked_points is None or _locked_matrix is None:
        return None
    polygon = np.float32(_locked_points).reshape(-1, 1, 2)
    if cv2.pointPolygonTest(polygon, (float(cx), float(cy)), False) < 0:
        return None
    mapped = cv2.perspectiveTransform(np.float32([[[cx, cy]]]), _locked_matrix)
    x = float(mapped[0, 0, 0])
    y = float(mapped[0, 0, 1])
    if not np.isfinite(x) or not np.isfinite(y):
        return None
    if x < -1 or y < -1 or x > ARENA + 1 or y > ARENA + 1:
        return None
    x = min(max(x, 0.0), ARENA - 1e-3)
    y = min(max(y, 0.0), ARENA - 1e-3)
    column = min(GRID_COLUMNS, int(x / (ARENA / GRID_COLUMNS)) + 1)
    row = min(GRID_ROWS - 1, int(y / (ARENA / GRID_ROWS)))
    return f"{chr(ord('A') + row)}{column}"


def _proposals(image: np.ndarray, points: list[tuple[float, float]]) -> list[dict[str, object]]:
    height, width = image.shape[:2]
    small = _half(image)
    small_points = [(x / 2, y / 2) for x, y in points]
    polygon = np.float32(small_points).reshape(-1, 1, 2)
    mask = np.zeros(small.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [polygon.astype(np.int32)], 255)
    boxes = _yolo_boxes(image)
    # Bright and dark patches are only sent to Grok. Without that check they
    # were drawn on chairs, bags, and coats.
    if USE_GROK:
        boxes.extend(_scale_box(item, 2.0) for item in live._floor_blobs(small, mask, polygon))
    polygon = np.float32(points).reshape(-1, 1, 2)
    kept: list[dict[str, object]] = []
    for item in boxes:
        label = str(item["label"])
        score = float(item["score"])
        x1, y1, x2, y2 = item["bbox"]
        if score < CONF_THRESHOLD:
            continue
        if item.get("source") == "yolo" and label.lower() in CAN_MISREAD and _can_shaped(item["bbox"], width, height):
            label = "can"
            item = {**item, "label": label}
        if item.get("source") != "yolo":
            if live._banned(label):
                continue
        elif not USE_GROK and not _looks_like_trash(label):
            continue
        if live._overlaps_overlay(item["bbox"], width, height):
            continue
        if (x2 - x1) * (y2 - y1) > 0.18 * width * height:
            continue
        if (x2 - x1) > 0.50 * width or (y2 - y1) > 0.50 * height:
            continue
        cx, cy = _floor_contact(item["bbox"])
        if cv2.pointPolygonTest(polygon, (float(cx), float(cy)), False) <= 0:
            continue
        sector = _sector_from_lock(cx, cy)
        if sector is None:
            continue
        kept.append({**item, "sector": sector, "cx": cx, "cy": cy})
    kept.sort(key=lambda item: (0 if item.get("source") == "yolo" else 1, -float(item["score"])))
    unique: list[dict[str, object]] = []
    for item in kept:
        if any(live._iou(item["bbox"], other["bbox"]) > 0.45 for other in unique):
            continue
        unique.append(item)
    return unique


def _blocked(cx: float, cy: float, now: float) -> bool:
    global _rejected
    _rejected = [item for item in _rejected if now - item[2] < 8.0]
    if any(_near(cx, cy, item[0], item[1]) for item in _rejected):
        return True
    if _pending is not None and _near(cx, cy, float(_pending["cx"]), float(_pending["cy"])):
        return True
    return False


def _queue_new(image: np.ndarray, item: dict[str, object]) -> None:
    """Hand one unseen crop to Grok. A cached or in-flight center is not sent."""
    global _pending
    now = time.monotonic()
    cx, cy = float(item["cx"]), float(item["cy"])
    with _lock:
        if _blocked(cx, cy, now):
            return
        if any(_near(cx, cy, float(track["cx"]), float(track["cy"])) for track in _cache):
            return
    x1, y1, x2, y2 = item["bbox"]
    height, width = image.shape[:2]
    pad_x = max(8.0, (x2 - x1) * 0.25)
    pad_y = max(8.0, (y2 - y1) * 0.25)
    crop = image[
        max(0, int(y1 - pad_y)) : min(height, int(y2 + pad_y)),
        max(0, int(x1 - pad_x)) : min(width, int(x2 + pad_x)),
    ]
    context = image
    if width > 960:
        context = cv2.resize(image, (960, int(height * 960 / width)), interpolation=cv2.INTER_AREA)
    crop_ok, crop_encoded = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    context_ok, context_encoded = cv2.imencode(".jpg", context, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    if not crop_ok or not context_ok:
        return
    job = {
        "cx": cx,
        "cy": cy,
        "bbox": item["bbox"],
        "sector": item["sector"],
        "score": float(item["score"]),
        "source": str(item.get("source") or ""),
        "crop": crop_encoded.tobytes(),
        "context": context_encoded.tobytes(),
    }
    with _lock:
        if _pending is None and not _blocked(cx, cy, now):
            _pending = job


def _track_once(image: np.ndarray) -> None:
    """Move cached labels with the detector. Grok is only queued for a new center."""
    global _points, _hits, _cache
    started = time.monotonic()
    if _locked_points is None:
        return
    points = list(_locked_points)
    with _lock:
        _points = points
    proposals = _proposals(image, points)
    now = time.monotonic()
    hits: list[dict[str, object]] = []
    fresh: list[dict[str, object]] = []
    used: set[int] = set()
    with _lock:
        cache = _cache
        for item in proposals:
            cx, cy = float(item["cx"]), float(item["cy"])
            best_index = -1
            best_distance = MATCH_PX
            for index, track in enumerate(cache):
                if index in used:
                    continue
                distance = math.hypot(cx - float(track["cx"]), cy - float(track["cy"]))
                if distance < best_distance:
                    best_distance = distance
                    best_index = index
            if best_index < 0:
                if not USE_GROK:
                    sector = str(item["sector"])
                    x_m, y_m = sector_to_coordinates(sector)
                    cache.append(
                        {
                            "cx": cx,
                            "cy": cy,
                            "bbox": item["bbox"],
                            "sector": sector,
                            "label": str(item["label"]),
                            "score": float(item["score"]),
                            "x_m": x_m,
                            "y_m": y_m,
                            "seen": now,
                        }
                    )
                    continue
                fresh.append(item)
                continue
            used.add(best_index)
            track = cache[best_index]
            sector = str(item["sector"])
            x_m, y_m = sector_to_coordinates(sector)
            track.update(
                {
                    "cx": cx,
                    "cy": cy,
                    "bbox": item["bbox"],
                    "sector": sector,
                    "score": float(item["score"]),
                    "x_m": x_m,
                    "y_m": y_m,
                    "seen": now,
                }
            )
        _cache = [track for track in cache if now - float(track["seen"]) <= 1.2]
        hits = [
            {
                "bbox": track["bbox"],
                "sector": track["sector"],
                "label": track["label"],
                "score": track["score"],
            }
            for track in _cache
        ]
        _hits = hits
    if USE_GROK and fresh:
        _queue_new(image, fresh[0])
    print(
        f"track_ms {int((time.monotonic() - started) * 1000)} hits {len(hits)} new {len(fresh)}",
        flush=True,
    )


def _grok_loop() -> None:
    """Name a box that is not already in the spatial cache."""
    global _pending, _cache, _rejected
    while not _stop.is_set():
        with _lock:
            job = _pending
        if job is None:
            if _stop.wait(0.05):
                return
            continue
        env = floor._load_env(floor.REPO / ".env")
        token = env.get("PEER_TOKEN") or env.get("VISION_TOKEN") or ""
        cert = env.get("VISION_CA_CERT") or ""
        classification = None
        if USE_GROK and token and cert and Path(cert).is_file():
            classification = trash._classify(
                token,
                cert,
                job["crop"],
                job["context"],
                f"stream-{int(time.time())}",
            )
        now = time.monotonic()
        cx, cy = float(job["cx"]), float(job["cy"])
        with _lock:
            if _pending is job:
                _pending = None
            if not live._is_trash(classification):
                if classification:
                    _rejected.append((cx, cy, now))
                    print(
                        f"grok_skip category={classification.get('category')} "
                        f"confidence={classification.get('confidence')}",
                        flush=True,
                    )
                continue
            sector = str(job["sector"])
            x_m, y_m = sector_to_coordinates(sector)
            label = _label_name(classification, str(job["source"]))
            if any(_near(cx, cy, float(track["cx"]), float(track["cy"])) for track in _cache):
                continue
            _cache.append(
                {
                    "cx": cx,
                    "cy": cy,
                    "bbox": job["bbox"],
                    "sector": sector,
                    "label": label,
                    "score": float(job["score"]),
                    "x_m": x_m,
                    "y_m": y_m,
                    "seen": now,
                }
            )
            _hits.append(
                {
                    "bbox": job["bbox"],
                    "sector": sector,
                    "label": label,
                    "score": float(job["score"]),
                }
            )
        distance = math.hypot(x_m, y_m)
        print(
            f"{label:<16} {float(job['score']):10.2f} {sector:>8} {x_m:7.2f} {y_m:7.2f} {distance:11.2f}",
            flush=True,
        )
        print("calibrated false  mapId demo-grid  metres are a sector estimate", flush=True)


def _track_loop() -> None:
    while not _stop.is_set():
        started = time.monotonic()
        with _lock:
            frame = None if latest_frame is None else latest_frame.copy()
        if frame is not None:
            try:
                _track_once(frame)
            except Exception as exc:
                print(f"detect_error {type(exc).__name__}: {exc}", flush=True)
        remaining = TRACK_INTERVAL - (time.monotonic() - started)
        if remaining > 0 and _stop.wait(remaining):
            return


def _draw_perspective(canvas: np.ndarray, scale_x: float, scale_y: float) -> None:
    """Project the flat 4x4 arena back into the camera. The outline stays the four clicks."""
    if _locked_inverse is None or _locked_points is None:
        return
    grid_color = (255, 255, 0)
    for index in range(GRID_COLUMNS + 1):
        metric_x = index * ARENA / GRID_COLUMNS
        start, end = _project([[metric_x, 0], [metric_x, ARENA]])
        cv2.line(
            canvas,
            (int(start[0] * scale_x), int(start[1] * scale_y)),
            (int(end[0] * scale_x), int(end[1] * scale_y)),
            grid_color,
            2,
        )
    for index in range(GRID_ROWS + 1):
        metric_y = index * ARENA / GRID_ROWS
        start, end = _project([[0, metric_y], [ARENA, metric_y]])
        cv2.line(
            canvas,
            (int(start[0] * scale_x), int(start[1] * scale_y)),
            (int(end[0] * scale_x), int(end[1] * scale_y)),
            grid_color,
            2,
        )
    polygon = np.array(
        [[point[0] * scale_x, point[1] * scale_y] for point in _locked_points],
        dtype=np.int32,
    ).reshape(-1, 1, 2)
    cv2.polylines(canvas, [polygon], True, (0, 255, 0), 3)
    for row in range(GRID_ROWS):
        for column in range(GRID_COLUMNS):
            metric_x = (column + 0.5) * ARENA / GRID_COLUMNS
            metric_y = (row + 0.5) * ARENA / GRID_ROWS
            pixel = _project([[metric_x, metric_y]])[0]
            tracker._draw_badge(
                canvas,
                f"{chr(ord('A') + row)}{column + 1}",
                (int(pixel[0] * scale_x) - 28, int(pixel[1] * scale_y) + 8),
                (255, 255, 255),
            )


def _annotate(
    frame: np.ndarray,
    hits: list[dict[str, object]],
    scale_x: float,
    scale_y: float,
) -> np.ndarray:
    """Draw the locked perspective grid and the last boxes. No detector work here."""
    _draw_perspective(frame, scale_x, scale_y)
    for item in hits:
        label = str(item["label"])
        if "floor-blob" in label.lower():
            continue
        x1, y1, x2, y2 = item["bbox"]
        p1 = (int(round(x1)), int(round(y1)))
        cv2.rectangle(frame, p1, (int(round(x2)), int(round(y2))), (0, 255, 0), 3)
        tracker._draw_label(frame, f"[{item['sector']}] {label}", (p1[0], p1[1] - 8))
    return frame


def _publish(image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    if not ok:
        return
    global _latest_jpeg, _last_publish
    with _lock:
        _latest_jpeg = encoded.tobytes()
    _last_publish = time.monotonic()


def _frames():
    while not _stop.is_set():
        with _lock:
            payload = _latest_jpeg
        if payload is None:
            time.sleep(0.05)
            continue
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + payload + b"\r\n"
        time.sleep(0.05)


def _lan_ip() -> str:
    """Address this machine uses to reach the phone. Empty when that route is down."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.168.5.146", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return ""
    finally:
        sock.close()


def _health_body() -> dict[str, object]:
    now = time.monotonic()
    with _lock:
        frame = latest_frame
        seen = _frame_at
        error = _camera_error
    age_ms = None if seen <= 0 else int((now - seen) * 1000)
    stale = seen <= 0 or (now - seen) > STALE_SECONDS
    _user, password = tracker._camera_password()
    return {
        "ok": frame is not None and not stale and not error,
        "camera": "connected" if frame is not None and not stale else "unavailable",
        "stale": stale,
        "lastFrameAgeMs": age_ms,
        "frameWidth": None if frame is None else int(frame.shape[1]),
        "frameHeight": None if frame is None else int(frame.shape[0]),
        "rawJpeg": "/camera.jpg",
        "rawMjpeg": "/camera.mjpg",
        "annotatedMjpeg": "/stream.mjpg",
        "mapId": "demo-grid",
        "calibrated": False,
        "grid": "ROW_A_TOP",
        "viewerSessionId": _viewer_session,
        "source": VIDEO_URL,
        "cameraAuthConfigured": bool(password),
        "error": error or None,
        "note": "Uncalibrated demo grid. Not MappingEvidence and not a rover route.",
    }


def _jpeg(frame: np.ndarray) -> bytes | None:
    ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    if not ok:
        return None
    return encoded.tobytes()


def _raw_frames():
    """Send the newest camera frame. A stale capture closes the stream."""
    while not _stop.is_set():
        with _lock:
            frame = None if latest_frame is None else latest_frame.copy()
            seen = _frame_at
        if frame is None or time.monotonic() - seen > STALE_SECONDS:
            return
        payload = _jpeg(frame)
        if payload is None:
            return
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + payload + b"\r\n"
        time.sleep(0.05)


def _serve() -> None:
    app = Flask(__name__)

    @app.route("/")
    def index() -> str:
        return PAGE

    @app.route("/health")
    def health():
        body = _health_body()
        status = 200 if body["ok"] else 503
        return jsonify(body), status

    @app.route("/camera.jpg")
    def camera_jpeg():
        body = _health_body()
        if not body["ok"]:
            return jsonify(body), 503
        with _lock:
            frame = None if latest_frame is None else latest_frame.copy()
        payload = None if frame is None else _jpeg(frame)
        if payload is None:
            return jsonify(body), 503
        return Response(payload, mimetype="image/jpeg")

    @app.route("/camera.mjpg")
    def camera_stream() -> Response:
        return Response(_raw_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

    @app.route("/stream.mjpg")
    def stream() -> Response:
        return Response(_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

    app.run(host=VIEWER_HOST, port=VIEWER_PORT, threaded=True, use_reloader=False)


def _status_frame(text: str) -> np.ndarray:
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    cv2.putText(image, text, (40, 360), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    return image


def main() -> int:
    threading.Thread(target=_serve, daemon=True).start()
    threading.Timer(1.5, lambda: webbrowser.open(PAGE_URL)).start()
    print(f"browser {PAGE_URL}", flush=True)
    print(f"viewer_port {VIEWER_PORT} viewer_host {VIEWER_HOST} use_grok {USE_GROK}", flush=True)
    lan = _lan_ip()
    if lan:
        print(f"lan_health http://{lan}:{VIEWER_PORT}/health", flush=True)
        print(f"lan_camera http://{lan}:{VIEWER_PORT}/camera.mjpg", flush=True)
    print("desktop window opening", flush=True)
    cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW, 1280, 720)
    connecting = _status_frame("Connecting to phone camera...")
    _publish(connecting)
    cv2.imshow(WINDOW, connecting)
    cv2.waitKey(1)
    threading.Thread(target=_capture_loop, daemon=True).start()
    frame = None
    while frame is None and not _stop.is_set():
        with _lock:
            frame = None if latest_frame is None else latest_frame.copy()
        cv2.imshow(WINDOW, connecting)
        if cv2.waitKey(30) & 0xFF in {ord("q"), 27}:
            _stop.set()
            cv2.destroyAllWindows()
            return 0
    if frame is None or not _select_corners(frame):
        _stop.set()
        cv2.destroyAllWindows()
        return 0
    with _lock:
        _points = list(_locked_points or [])
    threading.Thread(target=_track_loop, daemon=True).start()
    if USE_GROK:
        threading.Thread(target=_grok_loop, daemon=True).start()
    frames = 0
    fps_stamp = time.monotonic()
    while not _stop.is_set():
        with _lock:
            frame = None if latest_frame is None else latest_frame.copy()
            hits = list(_hits)
        if frame is None:
            shown = _status_frame("Connecting to phone camera...")
        else:
            preview = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_AREA)
            scale_x = 1280 / frame.shape[1]
            scale_y = 720 / frame.shape[0]
            scaled_hits = []
            for item in hits:
                x1, y1, x2, y2 = item["bbox"]
                scaled_hits.append(
                    {
                        **item,
                        "bbox": (x1 * scale_x, y1 * scale_y, x2 * scale_x, y2 * scale_y),
                    }
                )
            shown = _annotate(preview, scaled_hits, scale_x, scale_y)
        if time.monotonic() - _last_publish >= 0.08:
            _publish(shown)
        cv2.imshow(WINDOW, shown)
        frames += 1
        now = time.monotonic()
        if now - fps_stamp >= 1.0:
            print(f"display_fps {frames}", flush=True)
            frames = 0
            fps_stamp = now
        key = cv2.waitKey(1) & 0xFF
        if key in {ord("q"), 27}:
            break
    _stop.set()
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
