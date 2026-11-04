"""Mark the floor, then classify the trash inside it.

The frame comes from the phone stream when it is up, otherwise from the newest
saved photo. Click at least four floor-boundary points, starting at the top-left
and walking clockwise. c or Enter closes the outline.

YOLOE boxes come from the configured vision server. Grok names the object.
Only centres inside the outline receive a 0.5 m sector. The metres are the
uncalibrated demo estimate.
"""

from __future__ import annotations

import base64
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

MAPPING = Path(__file__).resolve().parent
sys.path.insert(0, str(MAPPING))
import calibrate_and_test_new_photo as floor
from grid_mapper import sector_to_coordinates

CLASSIFY_URL = "https://192.168.5.192:8443/api/classify-object"


def _short_class(detector_label: str, classification: dict | None) -> str:
    if not classification:
        return detector_label
    description = str(classification.get("description") or "").lower()
    for name in ("paper", "tissue", "cup", "can", "bottle", "wrapper", "plastic"):
        if name in description or name in detector_label.lower():
            return name
    category = str(classification.get("category") or "").lower()
    return category or detector_label


def _post_json(url: str, body: bytes, token: str, cert: str) -> tuple[int, bytes]:
    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as handle:
        handle.write(body)
        body_path = handle.name
    completed = subprocess.run(
        [
            "curl.exe",
            "--noproxy",
            "*",
            "--cacert",
            cert,
            "--max-time",
            "40",
            "-sS",
            "-w",
            "\n%{http_code}",
            "-X",
            "POST",
            "-H",
            f"Authorization: Bearer {token}",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            f"@{body_path}",
            url,
        ],
        capture_output=True,
    )
    Path(body_path).unlink(missing_ok=True)
    output = completed.stdout
    if b"\n" not in output:
        return 0, completed.stderr[:180]
    payload, status_text = output.rsplit(b"\n", 1)
    return int(status_text.strip() or "0"), payload


def _classify(token: str, cert: str, crop: bytes, context: bytes, request_id: str) -> dict | None:
    body = json.dumps(
        {
            "requestId": request_id,
            "cropImageBase64": base64.b64encode(crop).decode("ascii"),
            "contextImageBase64": base64.b64encode(context).decode("ascii"),
            "includePickup": False,
        }
    ).encode("utf-8")
    status, payload = _post_json(CLASSIFY_URL, body, token, cert)
    print(f"classify {request_id} status {status}", flush=True)
    if status not in {200, 503}:
        return None
    result = json.loads(payload).get("classification") or {}
    parsed = result.get("result")
    return parsed if isinstance(parsed, dict) else None


def main() -> int:
    image, source = floor._load_frame()
    height, width = image.shape[:2]
    print(f"source {source} frame {width}x{height}", flush=True)
    points = floor._select_points(image)
    if points is None or len(points) < 4:
        print("cancelled", flush=True)
        return 2

    polygon_px = [[round(x, 2), round(y, 2)] for x, y in points]
    (MAPPING / "floor_calibration.json").write_text(
        json.dumps(
            {
                "source": source,
                "imageWidth": width,
                "imageHeight": height,
                "FLOOR_POLYGON_PX": polygon_px,
                "pointCount": len(polygon_px),
                "cellMetres": 0.5,
                "rows": "A top to D bottom",
                "columns": "1 left to 4 right",
                "calibrated": False,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    homography = floor._floor_homography(points)
    polygon = np.float32(points).reshape(-1, 1, 2)
    ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise SystemExit("could not encode the frame")
    jpeg = encoded.tobytes()
    detections = floor._detect(jpeg)
    env = floor._load_env(floor.REPO / ".env")
    token = env.get("PEER_TOKEN") or env.get("VISION_TOKEN") or ""
    cert = env.get("VISION_CA_CERT") or str(Path(tempfile.gettempdir()) / "vision-dev.crt")

    annotated = image.copy()
    floor._draw_outline(annotated, points, 1.0, True)
    print(f"{'object class':<16} {'confidence':>10} {'sector':>8} {'distance_m':>11}", flush=True)
    kept = 0
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
        pad_x = max(8.0, (x2 - x1) * 0.25)
        pad_y = max(8.0, (y2 - y1) * 0.25)
        crop = image[
            max(0, int(y1 - pad_y)) : min(height, int(y2 + pad_y)),
            max(0, int(x1 - pad_x)) : min(width, int(x2 + pad_x)),
        ]
        class_name = detector_label
        crop_ok, crop_encoded = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if crop_ok and token and Path(cert).is_file():
            classification = _classify(token, cert, crop_encoded.tobytes(), jpeg, f"trash-{index}")
            if classification:
                print(
                    f"grok {index} category={classification.get('category')} "
                    f"confidence={classification.get('confidence')} "
                    f"description={classification.get('description')}",
                    flush=True,
                )
                class_name = _short_class(detector_label, classification)
        x_m, y_m = sector_to_coordinates(sector)
        distance = math.hypot(x_m, y_m)
        print(f"{class_name:<16} {score:10.2f} {sector:>8} {distance:11.2f}", flush=True)
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
        kept += 1

    temp_output = Path(tempfile.gettempdir()) / "trash_test_result.jpg"
    if not cv2.imwrite(str(temp_output), annotated, [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
        raise SystemExit(f"could not write {temp_output}")
    output = MAPPING / "trash_test_result.jpg"
    output.write_bytes(temp_output.read_bytes())
    print(f"inside_floor {kept} saved {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
