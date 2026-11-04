"""Turn a burst of sector labels into one rover waypoint.

Sector names are a row letter plus a column number, such as ``B2`` or ``A-1``.
Each cell is 1 metre square. The returned point is the cell centre in metres
from the map origin: column 1 is x = 0.5, row A is y = 0.5.

These metres are a local demo grid. Replace them when the measured floor map
is agreed. An unknown sector or a burst with no strict majority fails instead
of inventing a point.

A camera burst is accepted only as already-labelled sector names. Contact
pixels are not floor metres, and this result is not calibrated MappingEvidence.
"""

from __future__ import annotations

CELL_METRES = 1.0


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
    """Return the sector centre as ``(x, y)`` metres."""
    canonical = _canonical(sector_name)
    column = int(canonical[1:]) - 1
    row = ord(canonical[0]) - ord("A")
    if row < 0:
        raise ValueError(f"unrecognised sector: {sector_name!r}")
    centre = 0.5 * CELL_METRES
    return (column * CELL_METRES + centre, row * CELL_METRES + centre)
