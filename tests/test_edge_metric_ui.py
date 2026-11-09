"""Regression tests: Edge regime color and independent quality KPI."""
import math
from pathlib import Path
import unittest

from edge_triptych import (
    _colored_edge_metric, _edge_metric_color, _format_edge_quality,
)


class RecordingColumn:
    def __init__(self):
        self.calls = []

    def markdown(self, text, **kwargs):
        self.calls.append((text, kwargs))


class EdgeMetricUiTests(unittest.TestCase):
    def test_regime_positive_green_negative_red_neutral_unchanged(self):
        for value in ("positive", "Positive", "POSITIVO", "Positivo", 1):
            with self.subTest(value=value):
                self.assertEqual(_edge_metric_color(value), "#22b573")
        for value in ("negative", "Negative", "NEGATIVO", "Negativo", -1):
            with self.subTest(value=value):
                self.assertEqual(_edge_metric_color(value), "#e64b4b")
        for value in ("Zero", "N/D", "neutral", None, 0, math.nan):
            with self.subTest(value=value):
                self.assertEqual(_edge_metric_color(value), "inherit")

    def test_colored_value_is_in_metric_not_delta(self):
        col = RecordingColumn()
        _colored_edge_metric(col, "Regime Edge", "positive", "positive")
        self.assertEqual(len(col.calls), 1)
        html, kwargs = col.calls[0]
        self.assertIn("color:#22b573", html)
        self.assertIn("Regime Edge", html)
        self.assertIn("positive", html)
        self.assertTrue(kwargs["unsafe_allow_html"])

    def test_edge_quality_percentage_is_consistent_and_validated(self):
        self.assertEqual(_format_edge_quality(99.91), "99.91%")
        self.assertEqual(_format_edge_quality(92.75), "92.75%")
        self.assertEqual(_format_edge_quality(0), "0.00%")
        self.assertEqual(_format_edge_quality(100), "100.00%")
        for bad in (None, "", "N/D", -0.01, 101, math.nan, math.inf):
            with self.subTest(bad=bad):
                self.assertEqual(_format_edge_quality(bad), "N/D")

    def test_both_comparison_modes_use_quality_instead_of_repeated_sign(self):
        root = Path(__file__).resolve().parent.parent
        replay = (root / "edge_replay_panel.py").read_text(encoding="utf-8")
        official = (root / "edge_triptych.py").read_text(encoding="utf-8")
        self.assertIn('"Regime Edge"', replay)
        self.assertIn('"Regime Edge"', official)
        self.assertIn('"Qualità Edge"', replay)
        self.assertIn('"Qualità Edge"', official)
        self.assertNotIn('"Segno modellato"', replay)
        self.assertNotIn('"Segno Gamma modellato"', official)
        self.assertIn("_colored_edge_metric", replay)
        self.assertIn("_colored_edge_metric", official)
        self.assertIn("_format_edge_quality(quality)", replay)
        self.assertIn("_format_edge_quality(quality)", official)


if __name__ == "__main__":
    unittest.main()
