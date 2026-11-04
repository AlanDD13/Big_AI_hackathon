"""Turn a camera point or a burst of sector labels into one demo waypoint.

Sector names are a row letter plus a column number, such as ``B2`` or ``A-1``.
The demo room is four columns by four rows of 0.5 metre cells covering the
whole camera frame, 2.0 m by 2.0 m. Column 1 is the left strip. Row A is the
top strip and row D is the bottom strip. The origin is the bottom centre of
that frame, the rover home: B2 is (-0.25, 1.25).

``pixel_to_sector`` only splits the frame into those equal cells. It does not
measure the floor. These metres are an image-proportion estimate until a
measured map is agreed. An unknown sector, a point outside the frame, or a
burst with no strict majority fails instead of inventing a point.

A camera burst is accepted only as already-labelled sector names. This result
is not calibrated MappingEvidence.
"""

from __future__ import annotations

CELL_METRES = 0.5
GRID_COLUMNS = 4
GRID_ROWS = 4
# Row A is the top strip of the image and row D is the bottom strip.
GRID_CONVENTION = "ROW_A_TOP"


def _canonical(sector_name: str) -> str:
    if not isinstance(sector_name, str):
        raise ValueError("sector name must be a string")
    compact = sector_name.strip().upper().replace("-", "").replace(" ", "")
    if len(compact) < 2 or not compact[0].isalpha() or not compact[1:].isdigit():
        raise ValueError(f"unrecognised sector: {sector_name!r}")
    column = int(compact[1:])
    if column < 1:
        raise ValueError(f"unrecognised sector: {sector_name!r}")
    return f"{compact[0]}{column}"


def get_consensus_sector(sector_list: list[str]) -> str:
    """Return the strict majority sector from one burst of frame labels.

    A result counts only when one sector appears more often than every other
    sector and in more than half of the samples. An empty burst or a tie
    raises ``ValueError``.
    """
    if not sector_list:
        raise ValueError("sector list is empty")

    counts: dict[str, int] = {}
    for sector_name in sector_list:
        canonical = _canonical(sector_name)
        counts[canonical] = counts.get(canonical, 0) + 1

    winner, votes = max(counts.items(), key=lambda item: item[1])
    leaders = [name for name, count in counts.items() if count == votes]
    if len(leaders) != 1 or votes * 2 <= len(sector_list):
        raise ValueError("no clear sector consensus")
    return winner


def _sector_votes(payload: dict) -> list[str]:
    """Return one sector vote per frame from a vision burst payload.

    The payload is the debug burst object: ``frameCount`` plus
    ``multiFrameDetections``. Each frame must carry exactly one sector. A
    missing label, a conflict inside one frame, or a ``(0, 0)`` contact point
    fails closed. Image bytes and pixel positions are ignored.
    """
    if not isinstance(payload, dict):
        raise ValueError("burst payload must be an object")

    frame_count = payload.get("frameCount")
    if type(frame_count) is not int or frame_count < 1:
        raise ValueError("burst frameCount must be a positive integer")

    detections = payload.get("multiFrameDetections")
    if not isinstance(detections, list):
        raise ValueError("burst multiFrameDetections must be a list")

    by_frame: dict[int, list[str]] = {index: [] for index in range(frame_count)}
    for detection in detections:
        if not isinstance(detection, dict):
            raise ValueError("detection must be an object")
        contact = detection.get("contactPointPx")
        if contact == [0, 0] or contact == [0.0, 0.0]:
            raise ValueError("refusing (0, 0) contact point")

        frame_index = detection.get("frameIndex")
        if type(frame_index) is not int or frame_index < 0 or frame_index >= frame_count:
            raise ValueError("detection frameIndex is outside the burst")

        sector = detection.get("sector")
        if sector is None:
            continue
        if not isinstance(sector, str) or not sector.strip() or sector.strip().lower() == "unknown":
            raise ValueError(f"unrecognised sector: {sector!r}")
        canonical = _canonical(sector)
        labels = by_frame[frame_index]
        if canonical not in labels:
            labels.append(canonical)

    votes: list[str] = []
    for frame_index in range(frame_count):
        labels = by_frame[frame_index]
        if not labels:
            raise ValueError(
                "burst frame has no sector label; pixels are not floor coordinates"
            )
        if len(labels) != 1:
            raise ValueError("frame has conflicting sectors")
        votes.append(labels[0])
    return votes


def waypoint_from_burst(payload: dict) -> dict[str, object]:
    """Return one demo waypoint from a burst whose frames already name sectors.

    The point is the demo cell centre. ``calibrated`` is always false: a camera
    burst does not satisfy the measured-floor mapping contract.
    """
    sector = get_consensus_sector(_sector_votes(payload))
    x, y = sector_to_coordinates(sector)
    if (x, y) == (0.0, 0.0):
        raise ValueError("refusing (0, 0) waypoint")
    return {
        "sector": sector,
        "demoPointM": [x, y],
        "mapId": "demo-grid",
        "calibrated": False,
    }


def sector_to_coordinates(sector_name: str) -> tuple[float, float]:
    """Return the estimated 0.5 m sector centre as ``(x, y)`` metres.

    ``x`` is metres right of the frame's horizontal centre. ``y`` is metres
    forward from the bottom edge, where the rover home sits. Row A is the far
    strip at the top of the image. A sector outside the 4 by 4 demo room fails
    instead of returning a point past the room.
    """
    canonical = _canonical(sector_name)
    column = int(canonical[1:])
    row_from_top = ord(canonical[0]) - ord("A")
    if row_from_top < 0 or row_from_top >= GRID_ROWS or column > GRID_COLUMNS:
        raise ValueError(f"sector outside the demo room: {canonical}")
    centre = CELL_METRES / 2
    room_width = GRID_COLUMNS * CELL_METRES
    x = (column - 1) * CELL_METRES + centre - room_width / 2
    row_from_bottom = GRID_ROWS - 1 - row_from_top
    y = row_from_bottom * CELL_METRES + centre
    point = (x, y)
    if point == (0.0, 0.0):
        raise ValueError("refusing (0, 0) waypoint")
    return point


def pixel_to_sector(cx: float, cy: float, img_width: int, img_height: int) -> str:
    """Return the 0.5 m demo sector that contains a pixel point.

    The frame is split into ``GRID_COLUMNS`` equal vertical strips and
    ``GRID_ROWS`` equal horizontal strips. Left is column 1. Image y grows
    downward, so the top strip is row A. A point on or outside the far edge
    fails. The label is an uncalibrated demo sector, not a floor measurement.
    """
    if isinstance(cx, bool) or not isinstance(cx, (int, float)):
        raise ValueError("cx must be a number")
    if isinstance(cy, bool) or not isinstance(cy, (int, float)):
        raise ValueError("cy must be a number")
    if type(img_width) is not int or img_width < 1:
        raise ValueError("img_width must be a positive integer")
    if type(img_height) is not int or img_height < 1:
        raise ValueError("img_height must be a positive integer")
    if cx < 0 or cy < 0 or cx >= img_width or cy >= img_height:
        raise ValueError("pixel is outside the frame")

    column = _image_band(float(cx), img_width, GRID_COLUMNS) + 1
    row_from_top = _image_band(float(cy), img_height, GRID_ROWS)
    return f"{chr(ord('A') + row_from_top)}{column}"


def _image_band(position: float, size: int, count: int) -> int:
    """Return the 0-based strip index. The far edge is outside the frame."""
    if position < 0 or position >= size:
        raise ValueError("pixel is outside the frame")
    index = int(position * count / size)
    if index < 0 or index >= count:
        raise ValueError("pixel is outside the frame")
    return index
