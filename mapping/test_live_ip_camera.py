"""Grab one live phone frame, outline the floor, and sector the trash on it.

IP Camera Lite on this phone does not serve /shot.jpg. The script tries that
URL first, then takes one JPEG from the live /video stream. Row A is the top
of the detected floor. The metres are the uncalibrated demo estimate.
One Grok call may name an object that is already inside the floor. There is
no window and no retry loop.
"""

from __future__ import annotations

import math
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

MAPPING = Path(__file__).resolve().parent
sys.path.insert(0, str(MAPPING))
import auto_dynamic_tracker as tracker
import calibrate_and_test_new_photo as floor
import test_trash_detection as trash
from grid_mapper import GRID_CONVENTION, sector_to_coordinates

SHOT_URL = "http://192.168.5.146:8081/shot.jpg"
VIDEO_URL = "http://192.168.5.146:8081/video"
RESULT = MAPPING / "live_ip_camera_result.jpg"
TRASH_PROMPTS = [
    "crumpled paper",
    "paper ball",
    "can",
    "plastic bottle",
    "trash",
    "garbage",
    "debris",
]
CONF_THRESHOLD = 0.10
BANNED_WORDS = ("shadow", "text", "arcade", "swallow", "beeper", "panel", "remove")
OVERLAY_WIDTH = 0.42
OVERLAY_HEIGHT = 0.22


BLACK_MEAN = 15.0


def _decode_jpeg(payload: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)


def _bright_enough(image: np.ndarray | None, source: str) -> np.ndarray | None:
    if image is None:
        return None
    brightness = float(image.mean())
    print(f"frame_mean {brightness:.1f} source {source}", flush=True)
    if brightness < BLACK_MEAN:
        print("black_frame_rejected", flush=True)
        return None
    return image


def _flush_video_capture() -> np.ndarray | None:
    """Read several MJPEG frames so an empty buffer cannot become the result."""
    user, password = tracker._camera_password()
    addresses = [VIDEO_URL]
    if password:
        addresses.append(VIDEO_URL.replace("://", f"://{user}:{password}@", 1))
    capture = None
    for address in addresses:
        capture = cv2.VideoCapture(address)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
        capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000)
        if capture.isOpened():
            break
        capture.release()
        capture = None
    if capture is None or not capture.isOpened():
        print("videocapture_unavailable", flush=True)
        return None
    best: np.ndarray | None = None
    best_mean = -1.0
    for _index in range(8):
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        brightness = float(frame.mean())
        if brightness > best_mean:
            best = frame
            best_mean = brightness
    capture.release()
    if best is None:
        print("videocapture_no_frame", flush=True)
        return None
    print(f"[INFO] Connected to IP Camera Lite at {VIDEO_URL}", flush=True)
    return _bright_enough(best, "videocapture")


def _brightest_stream_jpeg() -> np.ndarray | None:
    user, password = tracker._camera_password()
    destination = Path(tempfile.gettempdir()) / "mapping-phone-fresh.bin"
    destination.unlink(missing_ok=True)
    completed = subprocess.run(
        [
            "curl.exe",
            "--noproxy",
            "*",
            "--max-time",
            "8",
            *(["--user", f"{user}:{password}"] if password else []),
            "-o",
            str(destination),
            "-w",
            "%{http_code}",
            VIDEO_URL,
        ],
        capture_output=True,
    )
    code = completed.stdout.decode("ascii", errors="replace").strip()
    if not destination.is_file():
        print(f"camera_unavailable http={code or 'none'}", flush=True)
        return None
    blob = destination.read_bytes()
    best: np.ndarray | None = None
    best_mean = -1.0
    start = 0
    while True:
        begin = blob.find(b"\xff\xd8", start)
        if begin < 0:
            break
        end = blob.find(b"\xff\xd9", begin + 2)
        if end < 0:
            break
        frame = _decode_jpeg(blob[begin : end + 2])
        start = end + 2
        if frame is None:
            continue
        brightness = float(frame.mean())
        if brightness > best_mean:
            best = frame
            best_mean = brightness
    print(f"camera_http {code or '28'} brightest_mean {best_mean:.1f}", flush=True)
    if best is None:
        return None
    print(f"[INFO] Connected to IP Camera Lite at {VIDEO_URL}", flush=True)
    return _bright_enough(best, VIDEO_URL)


def _fallback_photo() -> tuple[np.ndarray, str]:
    path = MAPPING / "new_photo.jpg"
    image = cv2.imread(str(path)) if path.is_file() else None
    if image is None:
        raise SystemExit("live frames were black and mapping/new_photo.jpg is missing")
    print(f"using_fallback {path}", flush=True)
    return image, str(path)


def _live_frame() -> tuple[np.ndarray, str]:
    payload = tracker._fetch_snapshot()
    if payload:
        image = _bright_enough(_decode_jpeg(payload), SHOT_URL)
        if image is not None:
            print(f"[INFO] Connected to IP Camera Lite at {SHOT_URL}", flush=True)
            return image, SHOT_URL
    else:
        print(f"[INFO] {SHOT_URL} did not return a JPEG", flush=True)
    captured = _flush_video_capture()
    if captured is None:
        captured = _brightest_stream_jpeg()
    if captured is not None:
        return captured, VIDEO_URL
    return _fallback_photo()


def _banned(label: str) -> bool:
    text = label.lower()
    return any(word in text for word in BANNED_WORDS)


def _overlaps_overlay(bbox: tuple[float, float, float, float], width: int, height: int) -> bool:
    """True when a box sits on the phone's top-left status text."""
    x1, y1, x2, y2 = bbox
    ox2 = OVERLAY_WIDTH * width
    oy2 = OVERLAY_HEIGHT * height
    ix1, iy1 = max(x1, 0.0), max(y1, 0.0)
    ix2, iy2 = min(x2, ox2), min(y2, oy2)
    if ix2 <= ix1 or iy2 <= iy1:
        return False
    intersection = (ix2 - ix1) * (iy2 - iy1)
    area = max(1.0, (x2 - x1) * (y2 - y1))
    return intersection / area >= 0.25


def _is_trash(classification: dict | None) -> bool:
    """The peer classifier has no is_trash flag. TRASH at MEDIUM or HIGH is that gate."""
    if not classification:
        return False
    if classification.get("is_trash") is True:
        return True
    category = str(classification.get("category") or "").upper()
    confidence = str(classification.get("confidence") or "").upper()
    kind = str(classification.get("subject_kind") or "").upper()
    carrying = str(classification.get("carrying") or "").upper()
    if category != "TRASH" or confidence not in {"MEDIUM", "HIGH"}:
        return False
    if kind not in {"", "INANIMATE"}:
        return False
    return carrying not in {"YES", "UNKNOWN"}


def _iou(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = first
    bx1, by1, bx2, by2 = second
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    intersection = (ix2 - ix1) * (iy2 - iy1)
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - intersection
    return float(intersection / union) if union > 0 else 0.0


def _floor_blobs(image: np.ndarray, mask: np.ndarray, polygon: np.ndarray) -> list[dict[str, object]]:
    """Propose small bright or dark objects on the floor that a detector box can miss."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    floor_pixels = gray[mask == 255]
    if floor_pixels.size == 0:
        return []
    median = float(np.median(floor_pixels))
    signed = gray.astype(np.int16)
    regions = {
        "bright": ((signed - median) > 32).astype(np.uint8) * 255,
        "dark": ((median - signed) > 40).astype(np.uint8) * 255,
    }
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    height, width = gray.shape
    frame_area = float(height * width)
    boxes: list[dict[str, object]] = []
    for kind, raw in regions.items():
        region = cv2.bitwise_and(raw, mask)
        region = cv2.morphologyEx(region, cv2.MORPH_OPEN, kernel)
        region = cv2.morphologyEx(region, cv2.MORPH_CLOSE, kernel)
        contours, _hierarchy = cv2.findContours(region, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < 0.00012 * frame_area or area > 0.012 * frame_area:
                continue
            x, y, box_w, box_h = cv2.boundingRect(contour)
            if box_w < 10 or box_h < 10 or box_w > width * 0.18 or box_h > height * 0.18:
                continue
            cx, cy = x + box_w / 2, y + box_h / 2
            if cv2.pointPolygonTest(polygon, (float(cx), float(cy)), False) <= 0:
                continue
            if area / float(box_w * box_h) < 0.35:
                continue
            crop = gray[y : y + box_h, x : x + box_w]
            contrast = abs(float(crop.mean()) - median)
            if contrast < 28:
                continue
            pad = 10
            boxes.append(
                {
                    "label": "floor-blob",
                    "score": min(0.99, contrast / 80.0),
                    "source": kind,
                    "bbox": (
                        float(max(0, x - pad)),
                        float(max(0, y - pad)),
                        float(min(width - 1, x + box_w + pad)),
                        float(min(height - 1, y + box_h + pad)),
                    ),
                }
            )
    kept: list[dict[str, object]] = []
    for item in boxes:
        if any(_iou(item["bbox"], other["bbox"]) > 0.4 for other in kept):
            continue
        kept.append(item)
    print(f"floor_blobs {len(kept)}", flush=True)
    return kept


def _grok_name(classification: dict, source: str) -> str:
    description = str(classification.get("description") or "").lower()
    if source == "bright" and any(word in description for word in ("crumpled", "paper", "tissue", "white")):
        return "crumpled paper"
    if "paper" in description or "tissue" in description or "crumpled" in description:
        return "crumpled paper"
    if "bottle" in description:
        return "plastic bottle"
    if "can" in description:
        return "can"
    return trash._short_class("trash", classification)


def _low_conf_boxes(image: np.ndarray) -> list[dict[str, object]]:
    model = tracker._load_model()
    if model is None:
        return []
    results = model.predict(source=image, conf=CONF_THRESHOLD, device="cpu", verbose=False)
    found = results[0].boxes
    names = results[0].names
    boxes: list[dict[str, object]] = []
    if found is None:
        return boxes
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


def _prompted_local_boxes(image: np.ndarray) -> list[dict[str, object]]:
    """Ask a text-prompt YOLOE for the trash list. Prompt-free weights cannot do this."""
    weights = tracker.REPO / "yoloe-26s-seg-pf.pt"
    if not weights.is_file():
        return []
    if "-pf" in weights.name:
        print("yoloe_prompts_unavailable prompt-free weights cannot take class prompts", flush=True)
        return []
    try:
        from ultralytics import YOLOE

        model = YOLOE(str(weights))
        model.set_classes(TRASH_PROMPTS)
    except Exception as exc:
        print(f"yoloe_prompts_unavailable {type(exc).__name__}: {exc}", flush=True)
        return []
    print(f"yoloe_prompts {TRASH_PROMPTS} conf {CONF_THRESHOLD}", flush=True)
    results = model.predict(source=image, conf=CONF_THRESHOLD, device="cpu", verbose=False)
    found = results[0].boxes
    names = results[0].names
    boxes: list[dict[str, object]] = []
    if found is None:
        return boxes
    for box in found:
        x1, y1, x2, y2 = (float(value) for value in box.xyxy[0].tolist())
        boxes.append(
            {
                "label": str(names[int(box.cls[0])]),
                "score": float(box.conf[0]),
                "bbox": (x1, y1, x2, y2),
            }
        )
    return boxes


def main() -> int:
    image, source = _live_frame()
    height, width = image.shape[:2]
    points = tracker._floor_polygon(image)
    polygon = np.float32(points).reshape(-1, 1, 2)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [polygon.astype(np.int32)], 255)
    annotated = image.copy()
    tracker._draw_floor(annotated, points, polygon, mask)
    print(
        f"source {source} frame {width}x{height} floor_vertices {len(points)} convention {GRID_CONVENTION}",
        flush=True,
    )

    ok, jpeg_encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise SystemExit("could not encode the live frame")
    jpeg = jpeg_encoded.tobytes()
    boxes = _low_conf_boxes(image)
    remote = tracker._detect_boxes(image, jpeg, None)
    if not remote:
        remote = tracker._detect_boxes(image, jpeg, None)
    for item in remote:
        item["source"] = "yolo"
    boxes.extend(item for item in remote if float(item["score"]) >= CONF_THRESHOLD)
    boxes.extend(_floor_blobs(image, mask, polygon))

    env = floor._load_env(floor.REPO / ".env")
    token = env.get("PEER_TOKEN") or env.get("VISION_TOKEN") or ""
    cert = env.get("VISION_CA_CERT") or ""
    print(
        f"{'object class':<16} {'confidence':>10} {'sector':>8} {'distance_m':>11}",
        flush=True,
    )
    candidates: list[dict[str, object]] = []
    for item in boxes:
        label = str(item["label"])
        score = float(item["score"])
        x1, y1, x2, y2 = item["bbox"]
        if score < CONF_THRESHOLD or _banned(label):
            print(f"{label:<16} {score:10.2f} {'ignored':>8}", flush=True)
            continue
        if _overlaps_overlay(item["bbox"], width, height):
            print(f"{label:<16} {score:10.2f} {'overlay':>8}", flush=True)
            continue
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if cv2.pointPolygonTest(polygon, (float(cx), float(cy)), False) <= 0:
            print(f"{label:<16} {score:10.2f} {'out':>8}", flush=True)
            continue
        sector = tracker._floor_sector(points, polygon, cx, cy)
        if sector is None:
            print(f"{label:<16} {score:10.2f} {'out':>8}", flush=True)
            continue
        candidates.append({**item, "sector": sector})

    def _priority(item: dict[str, object]) -> tuple[int, float]:
        source = str(item.get("source") or "")
        rank = 0 if source == "bright" else 1 if source == "dark" else 2
        return (rank, -float(item["score"]))

    candidates.sort(key=_priority)
    unique: list[dict[str, object]] = []
    for item in candidates:
        if any(_iou(item["bbox"], other["bbox"]) > 0.45 for other in unique):
            continue
        unique.append(item)
    candidates = unique
    print(f"candidates {len(candidates)} sending {min(10, len(candidates))} to grok", flush=True)
    kept = 0
    for index, item in enumerate(candidates[:10], start=1):
        x1, y1, x2, y2 = item["bbox"]
        sector = str(item["sector"])
        label = str(item["label"])
        score = float(item["score"])
        if not token or not Path(cert).is_file():
            print(f"{label:<16} {score:10.2f} {'no-grok':>8}", flush=True)
            continue
        pad_x = max(8.0, (x2 - x1) * 0.25)
        pad_y = max(8.0, (y2 - y1) * 0.25)
        crop = image[
            max(0, int(y1 - pad_y)) : min(height, int(y2 + pad_y)),
            max(0, int(x1 - pad_x)) : min(width, int(x2 + pad_x)),
        ]
        crop_ok, crop_encoded = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not crop_ok:
            continue
        classification = trash._classify(token, cert, crop_encoded.tobytes(), jpeg, f"live-{index}")
        is_trash = _is_trash(classification)
        if classification:
            print(
                f"grok {index} is_trash={is_trash} category={classification.get('category')} "
                f"confidence={classification.get('confidence')} "
                f"description={classification.get('description')}",
                flush=True,
            )
        if not is_trash:
            continue
        label = _grok_name(classification, str(item.get("source") or ""))
        _x_m, _y_m = sector_to_coordinates(sector)
        distance = math.hypot(_x_m, _y_m)
        print(f"{label:<16} {score:10.2f} {sector:>8} {distance:11.2f}", flush=True)
        p1 = (int(round(x1)), int(round(y1)))
        cv2.rectangle(annotated, p1, (int(round(x2)), int(round(y2))), (0, 255, 0), 3)
        tracker._draw_label(annotated, f"[{sector}] {label}", (p1[0], p1[1] - 8))
        kept += 1
    if kept == 0:
        print("(no confirmed trash inside the floor)", flush=True)
    print("calibrated false  mapId demo-grid  metres are a sector estimate", flush=True)
    ok, saved = cv2.imencode(".jpg", annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise SystemExit("could not encode the result")
    RESULT.write_bytes(saved.tobytes())
    print(f"saved {RESULT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
