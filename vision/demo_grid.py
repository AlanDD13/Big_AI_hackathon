"""Display-only grid using mapping's canonical uncalibrated sector helper."""
import cv2
from mapping.grid_mapper import GRID_COLUMNS, GRID_ROWS, pixel_to_sector


def demo_cells(width, height):
    return [(pixel_to_sector(x, y, width, height), x, y)
            for row in range(GRID_ROWS) for column in range(GRID_COLUMNS)
            for x, y in [((column + .5) * width / GRID_COLUMNS,
                          (row + .5) * height / GRID_ROWS)]]


def draw_demo_grid(frame):
    height, width = frame.shape[:2]
    colour = (0, 200, 255)
    thickness = max(1, round(width / 1200))
    font_scale = max(.45, width / 2400)
    for column in range(1, GRID_COLUMNS):
        x = round(column * width / GRID_COLUMNS)
        cv2.line(frame, (x, 0), (x, height - 1), colour, thickness)
    for row in range(1, GRID_ROWS):
        y = round(row * height / GRID_ROWS)
        cv2.line(frame, (0, y), (width - 1, y), colour, thickness)
    for label, x, y in demo_cells(width, height):
        origin = (round(x) - 12, round(y))
        cv2.putText(frame, label, origin, cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness + 3)
        cv2.putText(frame, label, origin, cv2.FONT_HERSHEY_SIMPLEX, font_scale, colour, thickness)
    cv2.putText(frame, 'DEMO GRID - uncalibrated; not measured 0.5m',
                (12, max(24, round(height / 35))), cv2.FONT_HERSHEY_SIMPLEX,
                font_scale, colour, thickness)
