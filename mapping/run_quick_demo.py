"""Print saved YOLO boxes on the 17:57 phone frame as 0.5 m sectors.

The boxes are the detection pass already run on phone-sector-objects.jpg
(1920x1080). This script does not start a model. The metres are the
image-proportion estimate from grid_mapper, not a measured floor point.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grid_mapper import pixel_to_sector, sector_to_coordinates

FRAME_WIDTH = 1920
FRAME_HEIGHT = 1080

# label, score, center x, center y. Saved from the phone-sector-objects pass.
DETECTIONS = (
    ("assemble", 0.949, 993.0, 884.0),
    ("wood wall", 0.940, 1104.0, 541.0),
    ("zoom lens", 0.791, 279.0, 421.0),
    ("playpen", 0.648, 161.0, 940.0),
    ("traffic sign", 0.577, 236.0, 190.0),
    ("sensor", 0.569, 855.0, 423.0),
    ("backpack", 0.499, 1757.0, 625.0),
    ("remove", 0.334, 238.0, 27.0),
    ("assemble", 0.274, 923.0, 886.0),
    ("weight", 0.251, 951.0, 975.0),
    ("remove", 0.217, 239.0, 134.0),
    ("monkey wrench", 0.214, 289.0, 939.0),
)


def main() -> int:
    print(f"frame {FRAME_WIDTH}x{FRAME_HEIGHT} source phone-sector-objects.jpg", flush=True)
    print(
        f"{'object':<16} {'score':>6} {'sector':>6} {'x_m':>7} {'y_m':>7} {'distance_m':>10}",
        flush=True,
    )
    for label, score, cx, cy in DETECTIONS:
        sector = pixel_to_sector(cx, cy, FRAME_WIDTH, FRAME_HEIGHT)
        x, y = sector_to_coordinates(sector)
        distance = math.hypot(x, y)
        print(
            f"{label:<16} {score:6.3f} {sector:>6} {x:7.2f} {y:7.2f} {distance:10.2f}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
