"""Tests for burst sector consensus and demo grid coordinates."""

from __future__ import annotations

import unittest

from grid_mapper import get_consensus_sector, sector_to_coordinates


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


if __name__ == "__main__":
    unittest.main()
