"""Draw the 4 by 4 demo grid and YOLO boxes on one saved camera frame.

Run with the vision virtualenv, from the repository root:

    python mapping/test_yolo_grid_visual.py

The grid is the uncalibrated 0.5 m demo split from ``pixel_to_sector``.
YOLO only marks that a box exists. The drawn name is ``object N``.
The detector's own class string is printed as ``raw_label`` and is not the sector.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

MAPPING = Path(__file__).resolve().parent
REPO = MAPPING.parent
if str(MAPPING) not in sys.path:
    sys.path.insert(0, str(MAPPING))

from grid_mapper import GRID_COLUMNS, GRID_ROWS, pixel_to_sector

FRAME_NAMES = (
    "phone-sector-latest.jpg",
    "phone-sector-objects.jpg",
    "phone-grid-photo.jpg",
)


def _candidate_images() -> list[Path]:
    roots = [MAPPING, REPO, Path(os.environ.get("TEMP", ""))]
    found: list[Path] = []
    for name in FRAME_NAMES:
        for root in roots:
            path = root / name
            if path.is_file() and path not in found:
                found.append(path)
    return found


def _pick_image(argv: list[str]) -> Path:
    if len(argv) > 1:
        path = Path(argv[1])
        if not path.is_file():
            raise SystemExit(f"image not found: {path}")
        return path
    found = _candidate_images()
    for path in found:
        if path.name == "phone-sector-latest.jpg":
            return path
    if not found:
        raise SystemExit("no camera frame found")
    return found[0]


def _label(image, text: str, origin: tuple[int, int], color: tuple[int, int, int]) -> None:
    import cv2

    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.7
    thickness = 2
    (width, height), baseline = cv2.getTextSize(text, font, scale, thickness)
    x = max(0, origin[0])
    y = max(height + 4, origin[1])
    cv2.rectangle(
        image,
        (x, y - height - 6),
        (x + width + 6, y + baseline),
        (0, 0, 0),
        -1,
    )
    cv2.putText(image, text, (x + 3, y - 2), font, scale, color, thickness, cv2.LINE_AA)


def main(argv: list[str]) -> int:
    import cv2
    from ultralytics import YOLOE

    image_path = _pick_image(argv)
    model_path = Path(os.environ.get("MODEL_PATH", REPO / "yoloe-26s-seg-pf.pt"))
    if not model_path.is_file():
        raise SystemExit(f"model not found: {model_path}")

    image = cv2.imread(str(image_path))
    if image is None:
        raise SystemExit(f"could not read image: {image_path}")
    img_height, img_width = image.shape[:2]

    model = YOLOE(str(model_path))
    results = model.predict(source=str(image_path), conf=0.15, device="cpu", verbose=False)
    boxes = results[0].boxes
    names = results[0].names
    count = 0 if boxes is None else len(boxes)
    print(f"source {image_path}", flush=True)
    print(f"frame {img_width}x{img_height}", flush=True)
    print(f"object_present {count > 0}", flush=True)
    print(f"object_count {count}", flush=True)

    drawn = image.copy()
    if boxes is not None:
        for index, box in enumerate(boxes, start=1):
            x1, y1, x2, y2 = (int(round(float(value))) for value in box.xyxy[0].tolist())
            cx = (float(box.xyxy[0][0]) + float(box.xyxy[0][2])) / 2
            cy = (float(box.xyxy[0][1]) + float(box.xyxy[0][3])) / 2
            sector = pixel_to_sector(cx, cy, img_width, img_height)
            score = float(box.conf[0])
            raw = names[int(box.cls[0])]
            print(
                f"object {index}\tsector={sector}\tscore={score:.3f}\t"
                f"center=({cx:.0f},{cy:.0f})\t"
                f"bbox=({x1},{y1},{x2},{y2})\traw_label={raw}",
                flush=True,
            )
            cv2.rectangle(drawn, (x1, y1), (x2, y2), (0, 255, 0), 2)
            _label(drawn, f"[{sector}] object {index}", (x1, y1 - 8), (0, 255, 0))

    grid_color = (0, 200, 255)
    for column in range(1, GRID_COLUMNS):
        x = int(round(column * img_width / GRID_COLUMNS))
        cv2.line(drawn, (x, 0), (x, img_height - 1), grid_color, 2)
    for row in range(1, GRID_ROWS):
        y = int(round(row * img_height / GRID_ROWS))
        cv2.line(drawn, (0, y), (img_width - 1, y), grid_color, 2)
    for row in range(GRID_ROWS):
        for column in range(GRID_COLUMNS):
            cx = (column + 0.5) * img_width / GRID_COLUMNS
            cy = (row + 0.5) * img_height / GRID_ROWS
            sector = pixel_to_sector(cx, cy, img_width, img_height)
            _label(drawn, sector, (int(cx) - 24, int(cy) + 8), (255, 255, 255))

    output = MAPPING / "output_yolo_grid_test.jpg"
    if not cv2.imwrite(str(output), drawn, [int(cv2.IMWRITE_JPEG_QUALITY), 90]):
        raise SystemExit(f"could not write {output}")
    print(f"saved {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
