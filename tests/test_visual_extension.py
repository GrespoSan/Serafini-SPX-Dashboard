"""Estensione grafica Cboe: test senza rete, nessuna mutazione a C1/P1."""
import unittest
from copy import deepcopy
from datetime import date

from net_gamma import profile, visual_profile
from pine_export import make_pine
from pine_compare import make_pine_comparison


DAY = date(2026, 10, 30)
WEEK = date(2026, 11, 6)
REFERENCE = 7800.0


def sample(expiry):
    options = []
    tag = expiry.strftime("%y%m%d")
    for strike in range(7100, 8410, 5):
        raw = f"{strike * 1000:08d}"
        for side, gamma in (("C", 0.007), ("P", 0.009)):
            options.append({
                "option": f"SPXW{tag}{side}{raw}",
                "gamma": gamma,
                "open_interest": 10,
                "volume": 2,
            })
    return options


def snapshot():
    return {"symbol": "SPX", "data": {
        "prev_day_close": REFERENCE, "current_price": 7610.0,
        "options": sample(DAY) + sample(WEEK),
    }}


class CboeVisualExtension(unittest.TestCase):
    def setUp(self):
        self.snap = snapshot()
        self.unchanged = deepcopy(self.snap)
        self.core = profile(self.snap, DAY, REFERENCE)
        self.other = profile(self.snap, WEEK, REFERENCE)

    def test_only_real_extra_strikes_and_no_change_to_core(self):
        ext = visual_profile(self.snap, self.core, DAY, REFERENCE, 7610)
        self.assertEqual(len(self.core["rows"]), 50)
        self.assertGreater(len(ext["rows"]), 50)
        self.assertLessEqual(len(ext["rows"]), 140)
        self.assertEqual(ext["core_strikes"], 50)
        self.assertLess(ext["rows"][0]["strike_spx"],
                        min(r["strike_spx"] for r in self.core["rows"]))
        core_gamma = {r["strike_spx"]: r["net_gamma"] for r in self.core["rows"]}
        extended_gamma = {r["strike_spx"]: r["net_gamma"] for r in ext["rows"]}
        for strike, value in core_gamma.items():
            self.assertAlmostEqual(extended_gamma[strike], value)
        self.assertEqual(self.snap, self.unchanged)

    def test_invalid_data_or_cap_never_invents_strikes(self):
        ext = visual_profile(self.snap, self.core, DAY, REFERENCE, 7000,
                             max_strikes=70)
        self.assertEqual(len(ext["rows"]), 70)
        self.assertFalse(ext["coverage_complete"])
        actual = {float(r["strike_spx"]) for r in self.core["rows"]}
        actual |= {float(r["strike_spx"]) for r in ext["rows"]}
        known = {float(r["strike_spx"]) for r in
                 visual_profile(self.snap, self.core, DAY, REFERENCE,
                                7000, max_strikes=140)["rows"]}
        self.assertTrue(actual.issubset(known))
        with self.assertRaises(ValueError):
            visual_profile(self.snap, self.core, DAY, REFERENCE, 7000,
                           max_strikes=200)

    def test_pine_can_hide_extension_and_preserves_50(self):
        ext = visual_profile(self.snap, self.core, DAY, REFERENCE, 7610)
        weekly_ext = visual_profile(self.snap, self.other, WEEK,
                                    REFERENCE, 7610)
        main = {**self.core, "display_rows": ext["rows"]}
        week = {**self.other, "display_rows": weekly_ext["rows"]}
        code = make_pine(main, week, 36, DAY.isoformat(), "snapshot")
        self.assertIn("max_lines_count=500", code)
        self.assertIn('showExtension = input.bool(true, "Estensione automatica NET GAMMA")', code)
        self.assertIn("if length > 0 and (showExtension or", code)
        self.assertIn("var array<float> odteCoreValues", code)
        self.assertIn("var array<float> weeklyCoreValues", code)
        self.assertIn("renderProfile(weeklyLevels", code)
        self.assertLessEqual(2 * 140 + 2, 500)
        comparison = make_pine_comparison(
            main, main, week, 36, DAY.isoformat(), "07:30", "15:50")
        self.assertIn("var array<float> prevCoreValues", comparison)
        self.assertIn("showExtension ? prevValues : prevCoreValues", comparison)
        self.assertIn("renderProfile(prevLevels", comparison)
        self.assertLessEqual(3 * 140 + 3, 500)

    def test_tampered_original_strike_cannot_be_exported(self):
        ext = visual_profile(self.snap, self.core, DAY, REFERENCE, 7610)
        ext["rows"][0]["net_gamma"] *= -1
        first = {**self.core, "display_rows": ext["rows"]}
        with self.assertRaises(ValueError):
            make_pine(first, self.other, 36, DAY.isoformat(), "snapshot")


if __name__ == "__main__":
    unittest.main()
