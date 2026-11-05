"""Find the floor on each frame and label only the objects that sit on it.

Every cycle grabs one snapshot, builds an N-point floor outline, and draws the
uncalibrated 0.5 m demo grid inside that outline. Row A is the top of the
floor. The metres come from the sector name and are not a measured floor point.
There is no window and no Grok call.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

MAPPING = Path(__file__).resolve().parent
REPO = MAPPING.parent
sys.path.insert(0, str(MAPPING))
import calibrate_and_test_new_photo as floor
from grid_mapper import GRID_COLUMNS, GRID_CONVENTION, GRID_ROWS, sector_to_coordinates

SHOT_URL = "http://192.168.5.146:8081/shot.jpg"
OUTPUT = MAPPING / "auto_live_result.jpg"
SKIP_NAMES = {
    "auto_live_result.jpg",
    "auto_floor_result.jpg",
    "trash_test_result.jpg",
    "new_photo_grid_result.jpg",
    "output_fresh_grid.jpg",
    "output_yolo_grid_test.jpg",
}


def _camera_password() -> tuple[str, str]:
    env = floor._load_env(REPO / ".env")
    user = os.environ.get("CAMERA_USER") or env.get("CAMERA_USER") or "admin"
    password = os.environ.get("CAMERA_PASS") or env.get("CAMERA_PASS") or ""
    return user, password


def _fetch_snapshot() -> bytes | None:
    user, password = _camera_password()
    passwords = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    passwords.add_password(None, SHOT_URL, user, password)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPBasicAuthHandler(passwords),
    )
    request = urllib.request.Request(SHOT_URL, headers={"Accept": "image/jpeg"})
    try:
        with opener.open(request, timeout=4) as response:
            payload = response.read()
            code = response.status
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        print(f"snapshot_unavailable {exc}", flush=True)
        return None
    if code != 200 or len(payload) < 5000 or not payload.startswith(b"\xff\xd8"):
        print(f"snapshot_not_jpeg http={code} bytes={len(payload)}", flush=True)
        return None
    print(f"snapshot_http {code} frame_bytes {len(payload)}", flush=True)
    return payload


def _fallback_image() -> Path | None:
    named = MAPPING / "new_photo.jpg"
    if named.is_file():
        return named
    images = [
        path
        for path in MAPPING.glob("*.jpg")
        if path.name not in SKIP_NAMES
    ]
    images.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return images[0] if images else None


def _load_frame() -> tuple[np.ndarray, bytes, str]:
    encoded = _fetch_snapshot()
    source = SHOT_URL
    if encoded is None:
        fallback = _fallback_image()
        if fallback is None:
            raise SystemExit("no camera frame and no local photo")
        encoded = fallback.read_bytes()
        source = str(fallback)
        print(f"using_local {fallback}", flush=True)
    image = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit("could not decode the frame")
    return image, encoded, source


def _largest_contour(mask: np.ndarray) -> np.ndarray | None:
    contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def _region_ok(contour: np.ndarray, width: int, height: int) -> bool:
    area = cv2.contourArea(contour)
    frame = float(width * height)
    if area < 0.12 * frame or area > 0.985 * frame:
        return False
    moments = cv2.moments(contour)
    if moments["m00"] <= 0:
        return False
    cy = moments["m01"] / moments["m00"]
    return cy > height * 0.35


def _edge_contour(image: np.ndarray) -> np.ndarray | None:
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    free = cv2.bitwise_not(edges)
    seed = (width // 2, int(height * 0.78))
    filled = free.copy()
    flood_mask = np.zeros((height + 2, width + 2), np.uint8)
    cv2.floodFill(filled, flood_mask, seed, 128)
    region = np.where(filled == 128, 255, 0).astype(np.uint8)
    contour = _largest_contour(region)
    if contour is None or not _region_ok(contour, width, height):
        return None
    return contour


def _color_contour(image: np.ndarray) -> np.ndarray | None:
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    patch = hsv[int(height * 0.70) : int(height * 0.90), int(width * 0.35) : int(width * 0.65)]
    if patch.size == 0:
        return None
    median = np.median(patch.reshape(-1, 3), axis=0)
    chosen: np.ndarray | None = None
    for hue, saturation, _value in ((10, 50, 50), (18, 80, 80), (28, 120, 110)):
        lower = np.array(
            [max(0, median[0] - hue), max(0, median[1] - saturation), 0],
            dtype=np.uint8,
        )
        upper = np.array(
            [min(179, median[0] + hue), min(255, median[1] + saturation), 255],
            dtype=np.uint8,
        )
        mask = cv2.inRange(hsv, lower, upper)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contour = _largest_contour(mask)
        if contour is not None and _region_ok(contour, width, height):
            chosen = contour
            break
    return chosen


def _fit_polygon(contour: np.ndarray) -> list[tuple[float, float]]:
    """Simplify the convex floor outline. A concave bite would black out the floor."""
    hull = cv2.convexHull(contour)
    perimeter = cv2.arcLength(hull, True)
    chosen = cv2.approxPolyDP(hull, 0.02 * perimeter, True)
    for fraction in (0.01, 0.02, 0.03, 0.045, 0.06, 0.08):
        approx = cv2.approxPolyDP(hull, fraction * perimeter, True)
        if len(approx) < 4:
            continue
        chosen = approx
        if len(approx) <= 8:
            break
    if len(chosen) < 4:
        chosen = hull
    points = chosen.reshape(-1, 2).astype(np.float32)
    start = int(np.lexsort((points[:, 0], points[:, 1]))[0])
    ordered = np.roll(points, -start, axis=0)
    if cv2.contourArea(ordered.reshape(-1, 1, 2), oriented=True) < 0:
        ordered = np.vstack([ordered[0:1], ordered[:0:-1]])
    return [(float(x), float(y)) for x, y in ordered]


def _floor_roi(height: int, width: int) -> np.ndarray:
    """The phone is fixed. Drop the upper wall and the garage doors on the right."""
    roi = np.zeros((height, width), dtype=np.uint8)
    top = int(height * 0.22)
    right = int(width * 0.80)
    roi[top:, :right] = 255
    return roi


def _wood_floor_contour(image: np.ndarray) -> np.ndarray | None:
    """Isolate the light wooden floor. White walls and doors stay outside the hull."""
    height, width = image.shape[:2]
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    hue, saturation, value = cv2.split(hsv)
    lightness = lab[:, :, 0]
    warmth = lab[:, :, 2]
    white = (saturation <= 32) & (value >= 150)
    y0, y1 = int(height * 0.68), int(height * 0.90)
    x0, x1 = int(width * 0.28), int(width * 0.62)
    patch = lab[y0:y1, x0:x1]
    patch_hsv = hsv[y0:y1, x0:x1]
    patch_sat = float(np.median(patch_hsv[:, :, 1])) if patch_hsv.size else 0.0
    patch_warm = float(np.median(patch[:, :, 2])) if patch.size else 0.0
    if patch.size and patch_sat >= 18 and patch_warm >= 132:
        median = np.median(patch.reshape(-1, 3), axis=0)
    else:
        seed = (
            (hue >= 8)
            & (hue <= 36)
            & (saturation >= 25)
            & (saturation <= 140)
            & (value >= 90)
            & (value <= 220)
            & (warmth >= 132)
            & ~white
        )
        if int(seed.sum()) < 0.04 * height * width:
            return None
        median = np.median(lab[seed], axis=0)
    delta = lab.astype(np.int16) - median.reshape(1, 1, 3).astype(np.int16)
    chroma = np.sqrt(delta[:, :, 1].astype(np.float32) ** 2 + delta[:, :, 2].astype(np.float32) ** 2)
    light_delta = np.abs(lightness.astype(np.int16) - int(median[0]))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    activity = cv2.GaussianBlur(
        cv2.convertScaleAbs(cv2.Laplacian(gray, cv2.CV_16S, ksize=3)),
        (21, 21),
        0,
    )
    keep = (
        (chroma < 18)
        & (light_delta < 40)
        & (hue >= 8)
        & (hue <= 36)
        & (saturation >= 22)
        & (saturation <= 140)
        & (value >= 80)
        & (value <= 230)
        & (lightness >= 90)
        & (warmth >= 132)
        & (activity < 28)
        & ~white
    )
    mask = keep.astype(np.uint8) * 255
    mask = cv2.bitwise_and(mask, _floor_roi(height, width))
    opened = cv2.morphologyEx(
        mask,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (19, 19)),
    )
    closed = cv2.morphologyEx(
        opened,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)),
    )
    closed = cv2.bitwise_and(closed, _floor_roi(height, width))
    contour = _largest_contour(closed)
    if contour is None:
        return None
    area = cv2.contourArea(contour)
    frame = float(height * width)
    if area < 0.08 * frame or area > 0.92 * frame:
        return None
    return contour


def _floor_polygon(image: np.ndarray) -> list[tuple[float, float]]:
    """Convex outline of the wooden floor only. An uncertain frame draws no grid."""
    contour = _wood_floor_contour(image)
    if contour is None:
        return []
    return _fit_polygon(contour)


def _detect_boxes(image: np.ndarray, jpeg: bytes, model: object | None) -> list[dict[str, object]]:
    boxes: list[dict[str, object]] = []
    if model is not None:
        results = model.predict(source=image, conf=0.15, device="cpu", verbose=False)
        found = results[0].boxes
        names = results[0].names
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
    for item in floor._detect(jpeg):
        bbox = item.get("bboxPx")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        boxes.append(
            {
                "label": str(item.get("detectorLabel") or "object"),
                "score": float(item.get("detectorScore") or 0),
                "bbox": tuple(float(value) for value in bbox),
            }
        )
    return boxes


def _floor_sector(points: list[tuple[float, float]], polygon: np.ndarray, cx: float, cy: float) -> str | None:
    """Name the cell inside the floor box. Row A is the top edge of that box."""
    if cv2.pointPolygonTest(polygon, (float(cx), float(cy)), False) <= 0:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x
    span_y = max_y - min_y
    if span_x < 1 or span_y < 1:
        return None
    across = (cx - min_x) / span_x
    down = (cy - min_y) / span_y
    if across < 0 or down < 0 or across >= 1 or down >= 1:
        return None
    column = min(GRID_COLUMNS, int(across * GRID_COLUMNS) + 1)
    row = min(GRID_ROWS - 1, int(down * GRID_ROWS))
    return f"{chr(ord('A') + row)}{column}"


def _draw_badge(image: np.ndarray, text: str, origin: tuple[int, int], color: tuple[int, int, int]) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.9
    thickness = 2
    (width, height), baseline = cv2.getTextSize(text, font, scale, thickness)
    x = max(0, min(origin[0], image.shape[1] - width - 12))
    y = max(height + 10, origin[1])
    cv2.rectangle(image, (x, y - height - 10), (x + width + 12, y + baseline + 6), (0, 0, 0), -1)
    cv2.rectangle(image, (x, y - height - 10), (x + width + 12, y + baseline + 6), color, 2)
    cv2.putText(image, text, (x + 6, y - 2), font, scale, color, thickness, cv2.LINE_AA)


def _draw_floor(canvas: np.ndarray, points: list[tuple[float, float]], polygon: np.ndarray, mask: np.ndarray) -> None:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max_x - min_x
    span_y = max_y - min_y
    grid_color = (255, 255, 0)
    overlay = canvas.copy()
    cv2.rectangle(overlay, (int(min_x), int(min_y)), (int(max_x), int(max_y)), grid_color, 3)
    for step in range(1, GRID_COLUMNS):
        x = int(round(min_x + span_x * step / GRID_COLUMNS))
        cv2.line(overlay, (x, int(min_y)), (x, int(max_y)), grid_color, 3)
    for step in range(1, GRID_ROWS):
        y = int(round(min_y + span_y * step / GRID_ROWS))
        cv2.line(overlay, (int(min_x), y), (int(max_x), y), grid_color, 3)
    canvas[mask == 255] = overlay[mask == 255]
    cv2.polylines(canvas, [polygon.astype(np.int32)], True, (0, 255, 0), 3)
    for row in range(GRID_ROWS):
        for column in range(GRID_COLUMNS):
            name = f"{chr(ord('A') + row)}{column + 1}"
            cx = min_x + (column + 0.5) * span_x / GRID_COLUMNS
            cy = min_y + (row + 0.5) * span_y / GRID_ROWS
            if cv2.pointPolygonTest(polygon, (float(cx), float(cy)), False) <= 0:
                continue
            _draw_badge(canvas, name, (int(cx) - 28, int(cy) + 8), (255, 255, 255))


def _draw_label(image: np.ndarray, text: str, origin: tuple[int, int]) -> None:
    _draw_badge(image, text, origin, (0, 255, 0))


def _save(image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise SystemExit("could not encode the live result")
    OUTPUT.write_bytes(encoded.tobytes())


def _cycle(model: object | None) -> None:
    image, jpeg, source = _load_frame()
    height, width = image.shape[:2]
    points = _floor_polygon(image)
    if len(points) < 3:
        print("floor_not_isolated", flush=True)
        return
    polygon = np.float32(points).reshape(-1, 1, 2)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [polygon.astype(np.int32)], 255)
    masked = cv2.bitwise_and(image, image, mask=mask)
    annotated = masked.copy()
    _draw_floor(annotated, points, polygon, mask)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{stamp} source {source} floor_vertices {len(points)} convention {GRID_CONVENTION}", flush=True)
    print(
        f"{'object':<16} {'confidence':>10} {'sector':>8} {'x_m':>7} {'y_m':>7} {'distance_m':>11}",
        flush=True,
    )
    kept = 0
    for item in _detect_boxes(image, jpeg, model):
        x1, y1, x2, y2 = item["bbox"]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        sector = _floor_sector(points, polygon, cx, cy)
        label = str(item["label"])
        score = float(item["score"])
        if sector is None:
            print(f"{label:<16} {score:10.2f} {'out':>8}", flush=True)
            continue
        x_m, y_m = sector_to_coordinates(sector)
        distance = math.hypot(x_m, y_m)
        print(
            f"{label:<16} {score:10.2f} {sector:>8} {x_m:7.2f} {y_m:7.2f} {distance:11.2f}",
            flush=True,
        )
        p1 = (int(round(x1)), int(round(y1)))
        p2 = (int(round(x2)), int(round(y2)))
        cv2.rectangle(annotated, p1, p2, (0, 255, 0), 2)
        _draw_label(annotated, f"[{sector}] {label}", (p1[0], p1[1] - 8))
        kept += 1
    if kept == 0:
        print("(no object inside the floor)", flush=True)
    print("calibrated false  mapId demo-grid  metres are a sector estimate", flush=True)
    _save(annotated)
    print(f"saved {OUTPUT}", flush=True)


def _load_model() -> object | None:
    weights = Path(os.environ.get("MODEL_PATH", REPO / "yoloe-26s-seg-pf.pt"))
    if not weights.is_file():
        print("local_yolo_missing using_remote_detect", flush=True)
        return None
    from ultralytics import YOLOE

    print(f"local_yolo {weights.name}", flush=True)
    return YOLOE(str(weights))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Auto floor outline and 0.5 m sectors.")
    parser.add_argument("--cycles", type=int, default=0, help="Stop after N cycles. 0 runs until interrupted.")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args(argv[1:])
    model = _load_model()
    completed = 0
    while True:
        started = time.monotonic()
        _cycle(model)
        completed += 1
        if args.cycles and completed >= args.cycles:
            return 0
        time.sleep(max(0.0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
