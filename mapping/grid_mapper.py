"""Turn a burst of sector labels into one rover waypoint.

Sector names are a row letter plus a column number, such as ``B2`` or ``A-1``.
Each cell is 1 metre square. The returned point is the cell centre in metres
from the map origin: column 1 is x = 0.5, row A is y = 0.5.

These metres are a local demo grid. Replace them when the measured floor map
is agreed. An unknown sector or a burst with no strict majority fails instead
of inventing a point.
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


def sector_to_coordinates(sector_name: str) -> tuple[float, float]:
    """Return the sector centre as ``(x, y)`` metres."""
    canonical = _canonical(sector_name)
    column = int(canonical[1:]) - 1
    row = ord(canonical[0]) - ord("A")
    if row < 0:
        raise ValueError(f"unrecognised sector: {sector_name!r}")
    centre = 0.5 * CELL_METRES
    return (column * CELL_METRES + centre, row * CELL_METRES + centre)
