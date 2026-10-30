"""Regression tests for manual-scale Pine exports (no external data)."""
import unittest

from pine_export import make_pine
from pine_compare import make_pine_comparison


def _profile(expiry, shift=0.0):
    return {
        "expiry": expiry,
        "rows": [
            {"strike_spx": float(7500 + 5 * k),
             "net_gamma": (float(k - 25) * 1.6) + shift}
            for k in range(50)
        ],
    }


class PineComparisons(unittest.TestCase):
    def setUp(self):
        self.odte = _profile("2026-10-28")
        self.weekly = _profile("2026-10-30", 15.0)
        self.earlier = _profile("2026-10-28", -12.0)

    def test_original_0730_is_manual_two_profiles(self):
        code = make_pine(self.odte, self.weekly, 36,
                         "2026-10-28", "2026-10-28 03:19:18", "Mattina 07:30")
        self.assertIn("V53-TV-MANUAL-SCALE", code)
        self.assertNotIn("showPrevious", code)
        self.assertIn("float actualScale = gammaPerCandle", code)
        self.assertIn("Scala MANUALE", code)
        self.assertNotIn("float actualScale = math.max(gammaPerCandle", code)
        self.assertIn("renderProfile(odteLevels, odteValues", code)
        self.assertIn('offsetOdte = input.int(100, "Distanza 0DTE da ultima candela", minval=45, maxval=250)', code)
        self.assertIn('offsetWeekly = input.int(100, "Distanza Weekly dalle 0DTE", minval=20, maxval=600)', code)
        self.assertIn("bool beyondLimit = offsetOdte >= 480 or weeklyCenter >= 480", code)
        self.assertIn("Distanza Weekly/0DTE oltre il limite TradingView", code)
        self.assertIn("int weeklyCenter = offsetOdte + offsetWeekly", code)
        self.assertIn("requiredScale(weeklyValues, weeklyCenter)", code)
        self.assertIn("bar_index + weeklyCenter", code)
        self.assertNotIn("bar_index + offsetWeekly, \"Weekly", code)

    def test_all_comparisons_have_manual_scale_and_3_closer_columns(self):
        for previous, current in [
            ("07:30", "13:30"),
            ("07:30", "15:50"),
            ("15:50", "20:30"),
        ]:
            with self.subTest(previous=previous, current=current):
                code = make_pine_comparison(
                    self.earlier, self.odte, self.weekly, 36,
                    "2026-10-28", previous, current, "2026-10-28 03:19:18")
                self.assertIn("V53-TV-MANUAL-CLOSE-THREE-COLUMNS", code)
                self.assertIn("float actualScale = gammaPerCandle", code)
                self.assertNotIn("float actualScale = math.max(gammaPerCandle", code)
                self.assertNotIn("minSeparation = 130", code)
                self.assertIn('showPrevious = input.bool(true, "Mostra 0DTE precedente")', code)
                self.assertIn('input.int(70, "1 · Posizione 0DTE precedente"', code)
                self.assertIn('input.int(165, "2 · Posizione 0DTE attuale"', code)
                self.assertIn('input.int(110, "3 · Distanza Weekly dalle 0DTE attuali", minval=20, maxval=600', code)
                self.assertIn('offsetOdte = input.int(100, "Distanza 0DTE da ultima candela", minval=45, maxval=250)', code)
                self.assertIn('offsetWeekly = input.int(100, "Distanza Weekly dalle 0DTE", minval=20, maxval=600)', code)
                self.assertIn("bool beyondLimit = currentCenter >= 480 or weeklyCenter >= 480", code)
                self.assertIn("int prevCenter = offsetPrevious", code)
                self.assertIn("int currentCenter = showPrevious ? offsetOdteCompare : offsetOdte", code)
                self.assertIn("int weeklyCenter = currentCenter + (showPrevious ? offsetWeeklyCompare : offsetWeekly)", code)
                self.assertIn("requiredGapScale", code)
                self.assertIn("requiredGapScale", code)
                self.assertIn("limite TradingView +500", code)
                self.assertIn('string currentTitle', code)
                self.assertIn('var array<float> prevLevels', code)
                self.assertIn('var array<float> weeklyLevels', code)
                self.assertIn(f"0DTE {previous}", code)
                self.assertIn(f"Weekly {current}", code)
                self.assertIn("if showPrevious\n            renderProfile(prevLevels", code)
                self.assertIn("renderProfile(odteLevels, odteValues", code)
                self.assertIn("renderProfile(weeklyLevels, weeklyValues", code)
                self.assertNotIn("Distanza 0DTE attuale (senza precedente)", code)

    def test_invalid_pair_fails_closed(self):
        with self.assertRaises(ValueError):
            make_pine_comparison(self.earlier, self.odte, self.weekly, 36,
                                 "2026-10-28", "13:30", "20:30")

    def test_missing_strikes_not_exported(self):
        prior = {"expiry": "2026-10-28", "rows": self.earlier["rows"][:-1]}
        with self.assertRaises(ValueError):
            make_pine_comparison(prior, self.odte, self.weekly, 36,
                                 "2026-10-28", "07:30", "13:30")

    def test_previous_expiry_must_match_current(self):
        prior = _profile("2026-10-27")
        with self.assertRaises(ValueError):
            make_pine_comparison(prior, self.odte, self.weekly, 36,
                                 "2026-10-28", "07:30", "13:30")


if __name__ == "__main__":
    unittest.main()
