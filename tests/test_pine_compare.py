"""Regression tests for manual-scale Pine exports (no external data)."""
import unittest

from pine_export import make_pine, _top_gamma_rows
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
        self.assertIn('showExtension = input.bool(true, "Estensione automatica NET GAMMA")', code)
        self.assertIn("Scala MANUALE", code)
        self.assertNotIn("float actualScale = math.max(gammaPerCandle", code)
        self.assertIn("renderProfile(odteLevels, odteValues", code)
        self.assertIn('showCurrentOdte = input.bool(true, "Mostra 0DTE corrente")', code)
        self.assertIn('showCurrentWeekly = input.bool(true, "Mostra Weekly corrente")', code)
        self.assertIn("if showCurrentOdte\n            renderProfile(odteLevels", code)
        self.assertIn("if showCurrentWeekly\n            renderProfile(weeklyLevels", code)
        self.assertIn("bool beyondLimit = (showCurrentOdte and", code)
        self.assertIn("showCurrentWeekly ? requiredScale(showExtension ? weeklyValues : weeklyCoreValues", code)
        self.assertIn('offsetOdte = input.int(100, "Distanza 0DTE da ultima candela", minval=45, maxval=250)', code)
        self.assertIn('offsetWeekly = input.int(100, "Distanza Weekly dalle 0DTE", minval=20, maxval=600)', code)
        self.assertIn("bool beyondLimit = (showCurrentOdte and offsetOdte >= 480) or (showCurrentWeekly and weeklyCenter >= 480)", code)
        self.assertIn("Distanza Weekly/0DTE oltre il limite TradingView", code)
        self.assertIn("int weeklyCenter = offsetOdte + offsetWeekly", code)
        self.assertIn("requiredScale(showExtension ? weeklyValues : weeklyCoreValues, weeklyCenter)", code)
        self.assertIn("bar_index + weeklyCenter", code)
        self.assertNotIn("bar_index + offsetWeekly, \"Weekly", code)

    def test_all_comparisons_have_manual_scale_and_3_closer_columns(self):
        for previous, current in [
            ("07:30", "13:30"),  # storico 11/11: non reinterpretare
            ("07:30", "14:46"),  # dal 12/11: 07:46 Chicago
            ("07:30", "15:50"),
            ("15:50", "17:00"),
            ("17:00", "20:30"),
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
                self.assertIn('showCurrentOdte = input.bool(true, "Mostra 0DTE corrente")', code)
                self.assertIn('showCurrentWeekly = input.bool(true, "Mostra Weekly corrente")', code)
                self.assertIn("if showCurrentOdte\n            renderProfile(odteLevels", code)
                self.assertIn("if showCurrentWeekly\n            renderProfile(weeklyLevels", code)
                self.assertIn("showCurrentWeekly and weeklyCenter >= 480", code)
                self.assertIn("showCurrentOdte ? requiredScale(activeCurrent", code)
                self.assertIn('offsetOdteCompare = input.int(95, "Distanza 0DTE corrente dalle precedenti", minval=20, maxval=300, active=showPrevious)', code)
                self.assertNotIn("offsetPrevious =", code)
                self.assertNotIn("offsetWeeklyCompare =", code)
                self.assertIn('offsetOdte = input.int(100, "Distanza 0DTE da ultima candela", minval=45, maxval=250)', code)
                self.assertIn('offsetWeekly = input.int(100, "Distanza Weekly dalle 0DTE", minval=20, maxval=600)', code)
                self.assertIn("bool beyondLimit = (showCurrentOdte and currentCenter >= 480) or (showCurrentWeekly and weeklyCenter >= 480)", code)
                self.assertIn("int prevCenter = offsetOdte", code)
                self.assertIn("int currentCenter = offsetOdte + (showPrevious ? offsetOdteCompare : 0)", code)
                self.assertIn("int weeklyCenter = currentCenter + offsetWeekly", code)
                # Con precedente ON: 100,195,295. Con OFF: corrente100, Weekly200.
                # Spostare offsetOdte di 100 sposta *tutti* i profili di 100.
                self.assertEqual((100, 100 + 95, 100 + 95 + 100), (100, 195, 295))
                self.assertEqual((200, 200 + 95, 200 + 95 + 100), (200, 295, 395))
                self.assertEqual((200, 200 + 100), (200, 300))
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

    def test_2040_is_current_only_without_any_previous_source(self):
        # Il quinto Pine ufficiale è autonomo: solo 0DTE + Weekly ore 20:30.
        code = make_pine(self.odte, self.weekly, 36,
                         "2026-11-12", "2026-11-12 19:30:20", "Sera 20:30")
        self.assertIn('0DTE 20:30', code)
        self.assertIn('Weekly 20:30', code)
        self.assertIn('Serafini SPX NET GAMMA | 2026-11-12 | Sera 20:30', code)
        self.assertNotIn('showPrevious', code)
        self.assertNotIn('prevLevels', code)
        self.assertNotIn('0DTE precedente', code)
        self.assertIn('Mostra 0DTE corrente', code)
        self.assertIn('Mostra Weekly corrente', code)
        self.assertIn('Scala MANUALE', code)

    def test_1446_chicago_labels_are_internal_to_comparison_pine(self):
        code = make_pine_comparison(
            self.earlier, self.odte, self.weekly, 36,
            "2026-11-12", "07:30", "14:46", "2026-11-12 13:46:00")
        self.assertIn('0DTE 07:30', code)
        self.assertIn('0DTE 14:46', code)
        self.assertIn('Weekly 14:46', code)
        self.assertNotIn('Weekly 13:30', code)
        self.assertIn('Mostra 0DTE precedente', code)

    def test_rank_only_real_0dte_positive_negative_bars(self):
        rows = [
            {"strike_spx": 100.0, "net_gamma": 0.0},
            {"strike_spx": 105.0, "net_gamma": 2.0},
            {"strike_spx": 101.0, "net_gamma": 5.0},
            {"strike_spx": 99.0, "net_gamma": 5.0},
            {"strike_spx": 102.0, "net_gamma": -1.0},
            {"strike_spx": 106.0, "net_gamma": -8.0},
            {"strike_spx": 107.0, "net_gamma": -12.0},
            {"strike_spx": 108.0, "net_gamma": -4.0},
        ]
        self.assertEqual(
            [r["strike_spx"] for r in _top_gamma_rows(rows, True)],
            [99.0, 101.0, 105.0],
        )
        self.assertEqual(
            [r["strike_spx"] for r in _top_gamma_rows(rows, False)],
            [107.0, 106.0, 108.0],
        )
        self.assertEqual(_top_gamma_rows(rows[:1], True), [])
        self.assertEqual(_top_gamma_rows(rows[:1], False), [])

    def test_top3_lines_start_at_last_candle_no_labels_and_only_current_0dte(self):
        enriched = dict(self.odte)
        enriched["display_rows"] = [
            {"strike_spx": 7490.0, "net_gamma": 999.0},
            {"strike_spx": 7495.0, "net_gamma": -999.0},
            *self.odte["rows"],
        ]
        for code in (
            make_pine(enriched, self.weekly, 36,
                      "2026-10-28", "2026-10-28 03:19:18"),
            make_pine_comparison(
                self.earlier, enriched, self.weekly, 36,
                "2026-10-28", "07:30", "15:50"),
        ):
            with self.subTest(code_type="comparison" if "showPrevious" in code else "standard"):
                self.assertIn('showTop3Odte = input.bool(true, "Proietta Top 3 Call / Put 0DTE")', code)
                self.assertIn("var array<float> topCallFull = array.from(", code)
                self.assertIn("var array<float> topPutFull = array.from(", code)
                self.assertIn("7526.0", code)  # 7490 + ES-SPX basis 36, actual strike
                self.assertIn("7531.0", code)  # 7495 + ES-SPX basis 36, actual strike
                self.assertIn("var array<float> topCallCore = array.from(", code)
                self.assertIn("var array<float> topPutCore = array.from(", code)
                self.assertEqual(code.count("drawOdteTopProjections(showExtension ?"), 1)
                self.assertIn("if showTop3Odte\n        drawOdteTopProjections", code)
                self.assertIn("line c = line.new(bar_index, y, bar_index + 1, y", code)
                self.assertIn("line p = line.new(bar_index, y, bar_index + 1, y", code)
                self.assertEqual(code.count("extend=extend.right, color=positiveColor, style=line.style_dashed, width=1"), 1)
                self.assertEqual(code.count("extend=extend.right, color=negativeColor, style=line.style_dashed, width=1"), 1)
                helper = code.split("drawOdteTopProjections(array<float>", 1)[1].split("if barstate.islast and validTicker", 1)[0]
                self.assertNotIn("label.new(", helper)
                self.assertNotIn("weeklyLevels", helper)

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
