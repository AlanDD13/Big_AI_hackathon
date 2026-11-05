"""Apply the saved floor outline to a new frame and report 0.5 m sectors.

No window and no clicks. The phone stream is tried first. The polygon in
mapping/floor_calibration.json masks out everything that is not floor.
YOLOE boxes come from the configured vision server. Grok names an object
when the server accepts the crop. The metres stay an uncalibrated demo estimate.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

MAPPING = Path(__file__).resolve().parent
sys.path.insert(0, str(MAPPING))
import calibrate_and_test_new_photo as floor
import test_trash_detection as trash
from grid_mapper import sector_to_coordinates


def _load_polygon(width: int, height: int) -> list[tuple[float, float]]:
    payload = json.loads((MAPPING / "floor_calibration.json").read_text(encoding="utf-8"))
    raw = payload.get("FLOOR_POLYGON_PX")
    if not isinstance(raw, list) or len(raw) < 4:
        raise SystemExit("floor_calibration.json has no usable FLOOR_POLYGON_PX")
    saved_width = int(payload.get("imageWidth") or width)
    saved_height = int(payload.get("imageHeight") or height)
    scale_x = width / saved_width
    scale_y = height / saved_height
    points = [(float(point[0]) * scale_x, float(point[1]) * scale_y) for point in raw]
    if len(points) > 4 and abs(points[0][0] - points[-1][0]) < 1.5 and abs(points[0][1] - points[-1][1]) < 1.5:
        points = points[:-1]
    return points


def main() -> int:
    image, source = floor._load_frame()
    height, width = image.shape[:2]
    points = _load_polygon(width, height)
    print(f"source {source} frame {width}x{height} polygon_points {len(points)}", flush=True)

    polygon = np.float32(points).reshape(-1, 1, 2)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [polygon.astype(np.int32)], 255)
    masked = cv2.bitwise_and(image, image, mask=mask)
    homography = floor._floor_homography(points)

    annotated = masked.copy()
    floor._draw_outline(annotated, points, 1.0, True)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise SystemExit("could not encode the frame")
    jpeg = encoded.tobytes()
    detections = floor._detect(jpeg)
    env = floor._load_env(floor.REPO / ".env")
    token = env.get("PEER_TOKEN") or env.get("VISION_TOKEN") or ""
    cert = env.get("VISION_CA_CERT") or str(Path(tempfile.gettempdir()) / "vision-dev.crt")

    rows: list[dict[str, object]] = []
    print(f"{'object class':<16} {'confidence':>10} {'sector':>8} {'x_m':>7} {'y_m':>7} {'distance_m':>11}", flush=True)
    for index, item in enumerate(detections, start=1):
        bbox = item.get("bboxPx")
        if not isinstance(bbox, list) or len(bbox) != 4:
            continue
        x1, y1, x2, y2 = (float(value) for value in bbox)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        sector = floor._sector_for_point(homography, polygon, cx, cy)
        detector_label = str(item.get("detectorLabel") or "object")
        score = float(item.get("detectorScore") or 0)
        if sector is None:
            print(f"{detector_label:<16} {score:10.2f} {'out':>8}", flush=True)
            continue
        class_name = detector_label
        pad_x = max(8.0, (x2 - x1) * 0.25)
        pad_y = max(8.0, (y2 - y1) * 0.25)
        crop = image[
            max(0, int(y1 - pad_y)) : min(height, int(y2 + pad_y)),
            max(0, int(x1 - pad_x)) : min(width, int(x2 + pad_x)),
        ]
        crop_ok, crop_encoded = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if crop_ok and token and Path(cert).is_file():
            classification = trash._classify(token, cert, crop_encoded.tobytes(), jpeg, f"auto-{index}")
            if classification:
                class_name = trash._short_class(detector_label, classification)
        x_m, y_m = sector_to_coordinates(sector)
        distance = math.hypot(x_m, y_m)
        row = {
            "objectClass": class_name,
            "confidence": round(score, 3),
            "sector": sector,
            "xM": round(x_m, 2),
            "yM": round(y_m, 2),
            "distanceM": round(distance, 2),
            "calibrated": False,
        }
        rows.append(row)
        print(f"{class_name:<16} {score:10.2f} {sector:>8} {x_m:7.2f} {y_m:7.2f} {distance:11.2f}", flush=True)
        cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
        cv2.putText(
            annotated,
            f"[{sector}] {class_name} {score:.2f}",
            (int(x1), max(24, int(y1) - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 255, 0),
            2,
        )

    temp_output = Path(tempfile.gettempdir()) / "auto_floor_result.jpg"
    if not cv2.imwrite(str(temp_output), annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
        raise SystemExit(f"could not write {temp_output}")
    output = MAPPING / "auto_floor_result.jpg"
    output.write_bytes(temp_output.read_bytes())
    print(json.dumps({"insideFloor": rows, "saved": str(output)}, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
