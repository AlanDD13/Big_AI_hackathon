"""Trace an N-point floor outline, then label YOLO boxes inside it.

Start at the top-left and walk clockwise. At least four points are required.
c or Enter closes the polygon and runs detection. r clears the points.
u removes the last point. Esc cancels.

Row A is the top of the outline and column 1 is the left. The metres are the
uncalibrated 0.5 m demo estimate, not a measured floor point.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

MAPPING = Path(__file__).resolve().parent
REPO = MAPPING.parent
sys.path.insert(0, str(MAPPING))
from grid_mapper import GRID_COLUMNS, GRID_ROWS, sector_to_coordinates

CAMERA_URL = "http://192.168.5.146:8081/video"
VISION_URL = "https://192.168.5.192:8443/api/detect-frame"
WINDOW = "Floor outline: click around the floor. c/Enter=close, r=reset, u=undo, Esc=cancel"


def _load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _last_jpeg(blob: bytes) -> bytes | None:
    frames: list[bytes] = []
    start = 0
    while True:
        begin = blob.find(b"\xff\xd8", start)
        if begin < 0:
            break
        end = blob.find(b"\xff\xd9", begin + 2)
        if end < 0:
            break
        frame = blob[begin : end + 2]
        if len(frame) > 5000:
            frames.append(frame)
        start = end + 2
    return frames[-1] if frames else None


def _capture_phone() -> bytes | None:
    env = _load_env(REPO / ".env")
    user = os.environ.get("CAMERA_USER") or env.get("CAMERA_USER") or "admin"
    password = os.environ.get("CAMERA_PASS") or env.get("CAMERA_PASS") or ""
    destination = Path(tempfile.gettempdir()) / "mapping-phone-fresh.bin"
    destination.unlink(missing_ok=True)
    completed = subprocess.run(
        [
            "curl.exe",
            "--noproxy",
            "*",
            "--max-time",
            "12",
            *(["--user", f"{user}:{password}"] if password else []),
            "-o",
            str(destination),
            "-w",
            "%{http_code}",
            CAMERA_URL,
        ],
        capture_output=True,
    )
    code = completed.stdout.decode("ascii", errors="replace").strip()
    if not destination.is_file() or destination.stat().st_size < 5000:
        print(f"camera_unavailable http={code or 'none'}", flush=True)
        return None
    frame = _last_jpeg(destination.read_bytes())
    print(f"camera_http {code or '28'} frame_bytes {0 if frame is None else len(frame)}", flush=True)
    return frame


def _fallback_image() -> Path | None:
    named = MAPPING / "new_photo.jpg"
    if named.is_file():
        return named
    candidates = [
        Path(tempfile.gettempdir()) / "phone-fresh-latest.jpg",
        Path(tempfile.gettempdir()) / "phone-sector-latest.jpg",
    ]
    for path in candidates:
        if path.is_file():
            return path
    images = sorted(MAPPING.glob("*.jpg"), key=lambda path: path.stat().st_mtime, reverse=True)
    return images[0] if images else None


def _load_frame() -> tuple[np.ndarray, str]:
    encoded = _capture_phone()
    if encoded:
        image = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            captured = MAPPING / "new_photo.jpg"
            captured.write_bytes(encoded)
            return image, str(captured)
    fallback = _fallback_image()
    if fallback is None:
        raise SystemExit("no camera frame and no local photo")
    image = cv2.imread(str(fallback))
    if image is None:
        raise SystemExit(f"could not read {fallback}")
    print(f"using_local {fallback}", flush=True)
    return image, str(fallback)


def _square_perimeter(fraction: float) -> tuple[float, float]:
    """Map a clockwise walk, starting at the top-left, onto the unit square."""
    side = (fraction % 1.0) * 4.0
    if side < 1.0:
        return side, 0.0
    if side < 2.0:
        return 1.0, side - 1.0
    if side < 3.0:
        return 1.0 - (side - 2.0), 1.0
    return 0.0, 1.0 - (side - 3.0)


def _unit_destinations(points: list[tuple[float, float]]) -> np.ndarray:
    """Place every outline vertex on the unit-square perimeter by edge length."""
    source = np.asarray(points, dtype=np.float32)
    following = np.roll(source, -1, axis=0)
    lengths = np.linalg.norm(following - source, axis=1)
    total = float(lengths.sum())
    positions = np.zeros(len(source), dtype=np.float32)
    if total > 0 and len(source) > 1:
        positions[1:] = np.cumsum(lengths[:-1]) / total
    return np.float32([_square_perimeter(float(position)) for position in positions])


def _floor_homography(points: list[tuple[float, float]]) -> np.ndarray:
    """Fit image pixels to the normalized floor with every clicked vertex."""
    source = np.float32(points).reshape(-1, 1, 2)
    destination = _unit_destinations(points).reshape(-1, 1, 2)
    homography, _mask = cv2.findHomography(source, destination, cv2.RANSAC, 0.05)
    if homography is None:
        raise ValueError("floor homography could not be fit")
    return homography


def _sector_for_point(homography: np.ndarray, polygon: np.ndarray, cx: float, cy: float) -> str | None:
    if cv2.pointPolygonTest(polygon, (float(cx), float(cy)), False) <= 0:
        return None
    mapped = cv2.perspectiveTransform(np.float32([[[cx, cy]]]), homography)[0, 0]
    across, down = float(mapped[0]), float(mapped[1])
    if across < -0.02 or down < -0.02 or across > 1.02 or down > 1.02:
        return None
    across = min(max(across, 0.0), 0.999999)
    down = min(max(down, 0.0), 0.999999)
    column = int(across * GRID_COLUMNS) + 1
    row = int(down * GRID_ROWS)
    return f"{chr(ord('A') + row)}{column}"


def _scaled_polygon(points: list[tuple[float, float]], scale: float) -> np.ndarray:
    return np.float32([[x * scale, y * scale] for x, y in points]).reshape(-1, 1, 2)


def _draw_outline(
    canvas: np.ndarray,
    points: list[tuple[float, float]],
    scale: float,
    closed: bool,
) -> None:
    if not points:
        return
    scaled = _scaled_polygon(points, scale).astype(np.int32)
    cv2.polylines(canvas, [scaled], closed and len(points) >= 4, (0, 200, 255), 2)
    for index, point in enumerate(scaled.reshape(-1, 2)):
        anchor = (int(point[0]), int(point[1]))
        cv2.circle(canvas, anchor, 5, (0, 255, 255), -1)
        cv2.putText(canvas, str(index + 1), (anchor[0] + 6, anchor[1] - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
    if not closed or len(points) < 4:
        return
    homography = _floor_homography(points)
    inverse = np.linalg.inv(homography)
    mask = np.zeros(canvas.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [scaled], 255)
    overlay = canvas.copy()

    def image_point(across: float, down: float) -> tuple[int, int]:
        mapped = cv2.perspectiveTransform(np.float32([[[across, down]]]), inverse)[0, 0]
        return int(round(float(mapped[0]) * scale)), int(round(float(mapped[1]) * scale))

    for step in range(1, GRID_COLUMNS):
        fraction = step / GRID_COLUMNS
        cv2.line(overlay, image_point(fraction, 0), image_point(fraction, 1), (0, 200, 255), 1)
    for step in range(1, GRID_ROWS):
        fraction = step / GRID_ROWS
        cv2.line(overlay, image_point(0, fraction), image_point(1, fraction), (0, 200, 255), 1)
    for row in range(GRID_ROWS):
        for column in range(GRID_COLUMNS):
            anchor = image_point((column + 0.5) / GRID_COLUMNS, (row + 0.5) / GRID_ROWS)
            if cv2.pointPolygonTest(scaled, anchor, False) <= 0:
                continue
            cv2.putText(overlay, f"{chr(ord('A') + row)}{column + 1}", anchor, cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)
    canvas[mask == 255] = overlay[mask == 255]
    cv2.polylines(canvas, [scaled], True, (0, 200, 255), 2)


def _detect(jpeg: bytes) -> list[dict]:
    env = _load_env(REPO / ".env")
    token = env.get("PEER_TOKEN") or env.get("VISION_TOKEN") or ""
    cert = env.get("VISION_CA_CERT") or str(Path(tempfile.gettempdir()) / "vision-dev.crt")
    if not token or not Path(cert).is_file():
        print("detect_skipped missing_token_or_cert", flush=True)
        return []
    with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as handle:
        handle.write(jpeg)
        body_path = handle.name
    completed = subprocess.run(
        [
            "curl.exe",
            "--noproxy",
            "*",
            "--cacert",
            cert,
            "--max-time",
            "25",
            "-sS",
            "-w",
            "\n%{http_code}",
            "-X",
            "POST",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Content-Type: image/jpeg",
            "--data-binary",
            f"@{body_path}",
            VISION_URL,
        ],
        capture_output=True,
    )
    Path(body_path).unlink(missing_ok=True)
    output = completed.stdout
    if b"\n" not in output:
        print("detect_failed no_response", flush=True)
        return []
    payload, status_text = output.rsplit(b"\n", 1)
    status = int(status_text.strip() or "0")
    print(f"detect_status {status}", flush=True)
    if status != 200:
        print(payload.decode("utf-8", errors="replace")[:180], flush=True)
        return []
    detections = json.loads(payload).get("detections")
    return detections if isinstance(detections, list) else []


def _select_points(image: np.ndarray) -> list[tuple[float, float]] | None:
    height, width = image.shape[:2]
    scale = min(1.0, 1100 / width)
    display_size = (int(round(width * scale)), int(round(height * scale)))
    clicked: list[tuple[float, float]] = []

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked.append((x / scale, y / scale))

    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WINDOW, on_mouse)
    print(
        "WINDOW_OPEN Click the floor boundary, starting at the top-left and walking clockwise. "
        "At least 4 points. c or Enter closes the polygon. r=reset, u=undo, Esc=cancel.",
        flush=True,
    )
    while True:
        canvas = cv2.resize(image, display_size)
        _draw_outline(canvas, clicked, scale, False)
        hint = f"points {len(clicked)}  c/Enter closes"
        cv2.putText(canvas, hint, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 3)
        cv2.putText(canvas, hint, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 1)
        cv2.imshow(WINDOW, canvas)
        key = cv2.waitKey(30) & 0xFF
        if key == 27:
            cv2.destroyAllWindows()
            return None
        if key in (ord("r"), ord("R")):
            clicked.clear()
        elif key in (ord("u"), ord("U")) and clicked:
            clicked.pop()
        elif key in (13, 10, ord("c"), ord("C")):
            if len(clicked) >= 4:
                cv2.destroyAllWindows()
                return list(clicked)
            print("need_at_least_4_points", flush=True)


def main() -> int:
    image, source = _load_frame()
    height, width = image.shape[:2]
    print(f"source {source} frame {width}x{height}", flush=True)
    points = _select_points(image)
    if points is None or len(points) < 4:
        print("cancelled", flush=True)
        return 2

    polygon_px = [[round(x, 2), round(y, 2)] for x, y in points]
    calibration = {
        "source": source,
        "imageWidth": width,
        "imageHeight": height,
        "FLOOR_POLYGON_PX": polygon_px,
        "pointCount": len(polygon_px),
        "cellMetres": 0.5,
        "rows": "A top to D bottom",
        "columns": "1 left to 4 right",
        "calibrated": False,
    }
    (MAPPING / "floor_calibration.json").write_text(json.dumps(calibration, indent=2), encoding="utf-8")

    homography = _floor_homography(points)
    polygon = np.float32(points).reshape(-1, 1, 2)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise SystemExit("could not encode the frame")
    detections = _detect(encoded.tobytes())

    annotated = image.copy()
    _draw_outline(annotated, points, 1.0, True)
    print(f"{'object':<16} {'score':>6} {'sector':>6} {'x_m':>7} {'y_m':>7} {'distance_m':>10}", flush=True)
    kept = 0
    for item in detections:
        bbox = item.get("bboxPx")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = (float(value) for value in bbox)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        sector = _sector_for_point(homography, polygon, cx, cy)
        label = str(item.get("detectorLabel") or "object")
        score = float(item.get("detectorScore") or 0)
        if sector is None:
            print(f"{label:<16} {score:6.3f} {'out':>6}", flush=True)
            continue
        x_m, y_m = sector_to_coordinates(sector)
        distance = math.hypot(x_m, y_m)
        print(f"{label:<16} {score:6.3f} {sector:>6} {x_m:7.2f} {y_m:7.2f} {distance:10.2f}", flush=True)
        cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
        cv2.putText(
            annotated,
            f"[{sector}] {label}",
            (int(x1), max(24, int(y1) - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
        )
        kept += 1
    temp_output = Path(tempfile.gettempdir()) / "n_point_grid_result.jpg"
    if not cv2.imwrite(str(temp_output), annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
        raise SystemExit(f"could not write {temp_output}")
    output = MAPPING / "n_point_grid_result.jpg"
    output.write_bytes(temp_output.read_bytes())
    print(f"inside_floor {kept} saved {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
