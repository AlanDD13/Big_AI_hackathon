"""Tests for burst sector consensus and demo grid coordinates."""

from __future__ import annotations

import unittest

from grid_mapper import get_consensus_sector, sector_to_coordinates, waypoint_from_burst


class ConsensusTests(unittest.TestCase):
    def test_majority_filters_a_single_false_sector(self) -> None:
        self.assertEqual(get_consensus_sector(["B2", "A1", "B2"]), "B2")

    def test_longer_noisy_burst_keeps_the_repeated_sector(self) -> None:
        self.assertEqual(
            get_consensus_sector(["B2", "B2", "B2", "A2", "B2"]),
            "B2",
        )

    def test_empty_burst_fails(self) -> None:
        with self.assertRaises(ValueError):
            get_consensus_sector([])

    def test_tie_has_no_consensus(self) -> None:
        with self.assertRaises(ValueError):
            get_consensus_sector(["A1", "B2"])

    def test_plurality_without_a_majority_fails(self) -> None:
        with self.assertRaises(ValueError):
            get_consensus_sector(["B2", "B2", "A1", "A2"])


class CoordinateTests(unittest.TestCase):
    def test_sector_centres_are_half_metre_offsets(self) -> None:
        self.assertEqual(sector_to_coordinates("A1"), (0.5, 0.5))
        self.assertEqual(sector_to_coordinates("B2"), (1.5, 1.5))

    def test_hyphenated_name_uses_the_same_cell(self) -> None:
        self.assertEqual(sector_to_coordinates("A-1"), sector_to_coordinates("A1"))

    def test_unknown_sector_fails(self) -> None:
        with self.assertRaises(ValueError):
            sector_to_coordinates("trash")


def _detection(frame_index: int, sector: str | None, contact: list[float] | None = None) -> dict:
    detection = {
        "frameIndex": frame_index,
        "detectorLabel": "cup",
        "detectorScore": 0.9,
        "contactPointPx": contact if contact is not None else [20.0, 40.0],
    }
    if sector is not None:
        detection["sector"] = sector
    return detection


class BurstPayloadTests(unittest.TestCase):
    def test_every_frame_sector_becomes_one_demo_waypoint(self) -> None:
        payload = {
            "frameCount": 3,
            "multiFrameDetections": [
                _detection(0, "B2"),
                _detection(0, "B2"),
                _detection(1, "A1"),
                _detection(2, "B2"),
            ],
        }
        self.assertEqual(
            waypoint_from_burst(payload),
            {
                "sector": "B2",
                "demoPointM": [1.5, 1.5],
                "mapId": "demo-grid",
                "calibrated": False,
            },
        )

    def test_pixels_without_a_sector_are_not_a_waypoint(self) -> None:
        payload = {
            "frameCount": 1,
            "multiFrameDetections": [_detection(0, None, [1200.0, 790.0])],
        }
        with self.assertRaises(ValueError):
            waypoint_from_burst(payload)

    def test_unknown_sector_fails(self) -> None:
        payload = {
            "frameCount": 1,
            "multiFrameDetections": [_detection(0, "unknown")],
        }
        with self.assertRaises(ValueError):
            waypoint_from_burst(payload)

    def test_conflicting_sectors_in_one_frame_fail(self) -> None:
        payload = {
            "frameCount": 1,
            "multiFrameDetections": [_detection(0, "A1"), _detection(0, "B2")],
        }
        with self.assertRaises(ValueError):
            waypoint_from_burst(payload)

    def test_zero_contact_point_fails(self) -> None:
        payload = {
            "frameCount": 1,
            "multiFrameDetections": [_detection(0, "A1", [0.0, 0.0])],
        }
        with self.assertRaises(ValueError):
            waypoint_from_burst(payload)


if __name__ == "__main__":
    unittest.main()
