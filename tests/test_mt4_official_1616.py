"""Separate MT4 producer branch: new Chicago-anchored SPX snapshots.

Pure, offline tests; production feed must not invent old/latest slots.
"""
import unittest
from datetime import date
from pathlib import Path

from gamma_replay import official_slots_for_day, SLOTS


class MT4ProspectiveChicagoTests(unittest.TestCase):
    def test_log_ids_before_and_after_monday_cutover(self):
        old = official_slots_for_day(date(2026, 11, 13))
        new = official_slots_for_day(date(2026, 11, 16))
        self.assertEqual(old["15:50"], "50 15 * * 1-5")
        self.assertEqual(old["17:00"], "0 17 * * 1-5")
        self.assertNotIn("16:16", old)
        self.assertNotIn("17:46", old)
        self.assertEqual(new["16:16"], "16 16 * * 1-5")
        self.assertEqual(new["17:46"], "46 17 * * 1-5")
        self.assertNotIn("15:50", new)
        self.assertNotIn("17:00", new)
        self.assertIn("17:46", SLOTS)

    def test_mt4_separates_identical_clock_labels(self):
        source = (Path(__file__).resolve().parents[1] /
                  "mt4_feed.py").read_text(encoding="utf-8")
        self.assertIn('frames["16:16 · Replay"]', source)
        self.assertIn('frames.update(official)', source)
        self.assertIn('if slot == "16:16 · Replay" else slot', source)
        self.assertIn("max(candidates, key=lambda t: t[0])", source)
        self.assertIn("('basis_es_spx',", source)


if __name__ == "__main__":
    unittest.main()
