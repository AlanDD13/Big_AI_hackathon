"""Calibrate one floor photo from a 14.2 cm pen.

The wooden-floor outline is unwrapped to a top-down view. Two clicks, the pen
tip and then the pen end, set the scale. A 0.5 m grid is drawn on that view
and warped back onto the original photo. Row A is the top of the unwrapped
floor. This pen scale is for this photo. It is not shared MappingEvidence.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

MAPPING = Path(__file__).resolve().parent
sys.path.insert(0, str(MAPPING))
import auto_dynamic_tracker as tracker

PHOTO = MAPPING / "calibration_photo.jpg"
OUTPUT = MAPPING / "floor_calibration.json"
PEN_LENGTH_M = 0.142
CELL_M = 0.5
FLAT = "Top-down floor"
FINAL = "Calibrated Physical Grid"


def _load_photo() -> np.ndarray:
    if not PHOTO.is_file():
        raise SystemExit(
            "mapping/calibration_photo.jpg is missing. Place that photo inside the mapping/ folder."
        )
    image = cv2.imdecode(np.fromfile(PHOTO, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit("mapping/calibration_photo.jpg could not be read as a JPEG.")
    return image


def _quad(points: list[tuple[float, float]], width: int, height: int) -> np.ndarray:
    """Four corners of the floor outline: top-left, top-right, bottom-right, bottom-left."""
    if len(points) < 4:
        return np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype=np.float32,
        )
    pts = np.array(points, dtype=np.float32).reshape(-1, 2)
    total = pts.sum(axis=1)
    difference = pts[:, 0] - pts[:, 1]
    quad = np.array(
        [
            pts[int(np.argmin(total))],
            pts[int(np.argmin(difference))],
            pts[int(np.argmax(total))],
            pts[int(np.argmax(difference))],
        ],
        dtype=np.float32,
    )
    if len({(round(float(x), 1), round(float(y), 1)) for x, y in quad}) < 4:
        return np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype=np.float32,
        )
    return quad


def _birdseye(image: np.ndarray, quad: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    top_left, top_right, bottom_right, bottom_left = quad
    out_width = int(
        max(
            np.linalg.norm(top_right - top_left),
            np.linalg.norm(bottom_right - bottom_left),
        )
    )
    out_height = int(
        max(
            np.linalg.norm(bottom_left - top_left),
            np.linalg.norm(bottom_right - top_right),
        )
    )
    out_width = max(out_width, 2)
    out_height = max(out_height, 2)
    destination = np.array(
        [[0, 0], [out_width - 1, 0], [out_width - 1, out_height - 1], [0, out_height - 1]],
        dtype=np.float32,
    )
    homography = cv2.getPerspectiveTransform(quad, destination)
    flat = cv2.warpPerspective(image, homography, (out_width, out_height))
    return flat, homography


def _click_pen(flat: np.ndarray) -> list[tuple[int, int]]:
    clicks: list[tuple[int, int]] = []
    canvas = flat.copy()

    def _on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if event != cv2.EVENT_LBUTTONDOWN or len(clicks) >= 2:
            return
        clicks.append((x, y))
        cv2.circle(canvas, (x, y), 8, (0, 0, 255), -1)
        if len(clicks) == 2:
            cv2.line(canvas, clicks[0], clicks[1], (0, 0, 255), 2)
        cv2.imshow(FLAT, canvas)

    print("Click the TIP of the pen, then click the END of the pen.", flush=True)
    cv2.namedWindow(FLAT, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(FLAT, min(1280, flat.shape[1]), min(720, flat.shape[0]))
    cv2.setMouseCallback(FLAT, _on_mouse)
    while len(clicks) < 2:
        cv2.imshow(FLAT, canvas)
        key = cv2.waitKey(20) & 0xFF
        if key in {ord("q"), 27}:
            cv2.destroyAllWindows()
            raise SystemExit("cancelled before the pen was marked")
    return clicks


def _draw_metric_grid(flat: np.ndarray, sector_px: float) -> np.ndarray:
    canvas = flat.copy()
    height, width = canvas.shape[:2]
    color = (255, 255, 0)
    columns = max(1, int(width / sector_px))
    rows = max(1, int(height / sector_px))
    for index in range(columns + 1):
        x = int(round(index * sector_px))
        if 0 <= x < width:
            cv2.line(canvas, (x, 0), (x, height - 1), color, 2)
    for index in range(rows + 1):
        y = int(round(index * sector_px))
        if 0 <= y < height:
            cv2.line(canvas, (0, y), (width - 1, y), color, 2)
    for row in range(rows):
        for column in range(columns):
            name = f"{chr(ord('A') + row)}{column + 1}"
            origin = (int((column + 0.08) * sector_px), int((row + 0.55) * sector_px))
            cv2.putText(canvas, name, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA)
    return canvas


def _overlay(image: np.ndarray, grid: np.ndarray, homography: np.ndarray, quad: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    inverse = np.linalg.inv(homography)
    back = cv2.warpPerspective(grid, inverse, (width, height))
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillConvexPoly(mask, quad.astype(np.int32), 255)
    result = image.copy()
    result[mask == 255] = back[mask == 255]
    return result


def _save(
    image: np.ndarray,
    quad: np.ndarray,
    homography: np.ndarray,
    clicks: list[tuple[int, int]],
    pixel_distance: float,
    meters_per_pixel: float,
    sector_px: float,
    bird_size: tuple[int, int],
) -> None:
    height, width = image.shape[:2]
    payload = {
        "source": str(PHOTO),
        "imageWidth": width,
        "imageHeight": height,
        "FLOOR_POLYGON_PX": [[round(float(x), 2), round(float(y), 2)] for x, y in quad],
        "pointCount": 4,
        "cellMetres": CELL_M,
        "rows": "A top to D bottom",
        "columns": "1 left to 4 right",
        "calibrated": False,
        "penLengthM": PEN_LENGTH_M,
        "penClicksBirdseyePx": [[int(x), int(y)] for x, y in clicks],
        "penPixelDistance": round(pixel_distance, 3),
        "metersPerPixel": meters_per_pixel,
        "sectorSizePx": sector_px,
        "homography": [[float(value) for value in row] for row in homography],
        "birdseyeSize": [int(bird_size[0]), int(bird_size[1])],
        "note": "Pen scale on this unwrapped photo. Not shared MappingEvidence.",
    }
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"saved {OUTPUT}", flush=True)


def main() -> int:
    image = _load_photo()
    height, width = image.shape[:2]
    points = tracker._floor_polygon(image)
    if len(points) < 4:
        print("floor outline was not isolated; using the photo border", flush=True)
        points = [(0.0, 0.0), (width - 1.0, 0.0), (width - 1.0, height - 1.0), (0.0, height - 1.0)]
    quad = _quad(points, width, height)
    flat, homography = _birdseye(image, quad)
    clicks = _click_pen(flat)
    pixel_distance = float(np.hypot(clicks[1][0] - clicks[0][0], clicks[1][1] - clicks[0][1]))
    if pixel_distance < 2:
        cv2.destroyAllWindows()
        raise SystemExit("the two pen clicks are too close to set a scale")
    meters_per_pixel = PEN_LENGTH_M / pixel_distance
    sector_px = CELL_M / meters_per_pixel
    print(
        f"pen_px {pixel_distance:.2f}  meters_per_pixel {meters_per_pixel:.6f}  "
        f"sector_px {sector_px:.2f}",
        flush=True,
    )
    grid = _draw_metric_grid(flat, sector_px)
    shown = _overlay(image, grid, homography, quad)
    _save(image, quad, homography, clicks, pixel_distance, meters_per_pixel, sector_px, (flat.shape[1], flat.shape[0]))
    cv2.destroyWindow(FLAT)
    cv2.namedWindow(FINAL, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(FINAL, min(1280, width), min(720, height))
    cv2.imshow(FINAL, shown)
    print("Press q to close.", flush=True)
    while True:
        key = cv2.waitKey(20) & 0xFF
        if key in {ord("q"), 27}:
            break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
