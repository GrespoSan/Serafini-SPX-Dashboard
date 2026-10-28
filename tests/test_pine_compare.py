"""Regression tests for the four Pine exports (no market-data calls)."""
import unittest

from pine_export import make_pine
from pine_compare import make_pine_comparison


def _profile(expiry, shift=0.0):
    rows = [
        {"strike_spx": float(7500 + 5 * k),
         "net_gamma": (float(k - 25) * 1.6) + shift}
        for k in range(50)
    ]
    return {"expiry": expiry, "rows": rows}


class PineComparisons(unittest.TestCase):
    def setUp(self):
        self.odte = _profile("2026-10-28")
        self.weekly = _profile("2026-10-30", 15.0)
        self.earlier = _profile("2026-10-28", -12.0)

    def test_original_0730_remains_two_profiles(self):
        script = make_pine(self.odte, self.weekly, 36,
                           "2026-10-28", "2026-10-28 03:19:18", "Mattina 07:30")
        self.assertIn("V49-TV-COMMON-SCALE-LABEL", script)
        self.assertNotIn("showPrevious", script)
        self.assertIn("renderProfile(odteLevels, odteValues", script)

    def test_each_comparison_has_three_profiles_and_optional_previous(self):
        for prev, current in [("07:30", "13:30"), ("07:30", "15:50"),
                              ("15:50", "20:30")]:
            with self.subTest(prev=prev, current=current):
                code = make_pine_comparison(
                    self.earlier, self.odte, self.weekly, 36,
                    "2026-10-28", prev, current, "2026-10-28 03:19:18")
                self.assertIn('showPrevious = input.bool(true, "Mostra 0DTE precedente")', code)
                self.assertIn("if showPrevious\n        renderProfile(prevLevels", code)
                self.assertIn(f"0DTE 2026-10-28 | {prev} (precedente)", code)
                self.assertIn(f"WEEKLY 2026-10-30 | {current}", code)
                self.assertIn("requiredCompactScale", code)
                self.assertIn("V51-TV-SPACED-THREE-COLUMNS", code)
                self.assertIn("minSeparation = 130", code)
                self.assertIn("int prevCenter", code)
                self.assertIn("currentCompareCenter", code)
                self.assertIn("weeklyCompareCenter", code)
                self.assertIn("compareRoom", code)
                self.assertIn('1 · Distanza 0DTE precedente', code)
                self.assertIn('2 · Distanza 0DTE attuale', code)
                self.assertIn('3 · Distanza Weekly attuale', code)
                self.assertIn('1 · 0DTE', code)
                self.assertIn('string currentTitle', code)
                self.assertIn("bar_index + currentCenter", code)
                self.assertIn("bar_index + weeklyCenter", code)
                self.assertIn("var array<float> prevLevels", code)
                self.assertIn("var array<float> weeklyLevels", code)

    def test_three_column_geometry_cannot_overlap(self):
        # Verifica il vincolo geometrico delle regole V51, anche con
        # impostazioni estreme dei tre offset indipendenti.
        from itertools import product
        for previous, current, weekly in product(
                [40, 70, 140], [165, 235, 300], [300, 400, 440]):
            min_separation = 130
            p = min(previous, 430 - 2 * min_separation)
            c = min(max(current, p + min_separation), 430 - min_separation)
            w = min(max(weekly, c + min_separation), 430)
            room = max(1., min(
                (c - p - 22) / 2.,
                (w - c - 22) / 2.,
                480 - w - 10,
                p - 10,
            ))
            self.assertGreaterEqual(c - p, min_separation)
            self.assertGreaterEqual(w - c, min_separation)
            self.assertGreaterEqual((c - room) - (p + room), 22)
            self.assertGreaterEqual((w - room) - (c + room), 22)
            self.assertGreaterEqual(p - room, 10)
            self.assertLessEqual(w + room, 480)

    def test_invalid_pair_fails_closed(self):
        with self.assertRaises(ValueError):
            make_pine_comparison(self.earlier, self.odte, self.weekly, 36,
                                 "2026-10-28", "13:30", "20:30")

    def test_missing_strikes_not_exported(self):
        prior = {"expiry": "2026-10-28", "rows": self.earlier["rows"][:-1]}
        with self.assertRaises(ValueError):
            make_pine_comparison(prior, self.odte, self.weekly, 36,
                                 "2026-10-28", "07:30", "13:30")


if __name__ == "__main__":
    unittest.main()
