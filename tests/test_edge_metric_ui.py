"""Regression tests: Edge regime color and independent quality KPI."""
import math
from datetime import datetime
from pathlib import Path
import unittest

import pandas as pd

from edge_replay_panel import _verified_es_last_candle, _model_valuation_context

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

    def test_replay_net_gex_uses_signed_value_color_instead_of_default_metric(self):
        root = Path(__file__).resolve().parent.parent
        replay = (root / "edge_replay_panel.py").read_text(encoding="utf-8")
        self.assertIn('if key == "net_gex":', replay)
        self.assertIn('_colored_edge_metric(col, title,', replay)
        self.assertIn('amount)', replay)
        self.assertIn('col.caption(f"Δ {(amount - old)/1e9:+.2f} mld")', replay)
        col = RecordingColumn()
        _colored_edge_metric(col, "NET GEX", "-3.99 mld USD", -3.99e9)
        self.assertIn("color:#e64b4b", col.calls[0][0])
        self.assertIn("-3.99 mld USD", col.calls[0][0])
        col = RecordingColumn()
        _colored_edge_metric(col, "NET GEX", "+2.11 mld USD", 2.11e9)
        self.assertIn("color:#22b573", col.calls[0][0])
        col = RecordingColumn()
        _colored_edge_metric(col, "NET GEX", "0.00 mld USD", 0.0)
        self.assertIn("color:inherit", col.calls[0][0])

    def test_11nov_cboe_close_is_not_misrepresented_as_live_es(self):
        download = datetime.fromisoformat("2026-11-11T11:01:19+01:00")
        candles = pd.DataFrame({
            "bar_close": pd.to_datetime([
                "2026-11-11T10:55:00+01:00",
                "2026-11-11T11:00:00+01:00",
                "2026-11-11T11:05:00+01:00",
            ]),
            "close": [7590.0, 7598.75, 7602.0],
        })
        candles.attrs["candle_source"] = "Yahoo ES=F · 5m verificato"
        result = _verified_es_last_candle(candles, download)
        self.assertEqual(result["price"], 7598.75)
        self.assertEqual(result["clock"], "11:00")
        self.assertIn("Yahoo", result["source"])
        self.assertNotEqual(result["price"], 7520.9302 + 23.0)
        self.assertEqual(result["age_minutes"], 1.32)

    def test_stale_es_candles_show_no_unverified_price(self):
        download = datetime.fromisoformat("2026-11-11T11:01:19+01:00")
        stale = pd.DataFrame({
            "bar_close": pd.to_datetime(["2026-11-11T10:35:00+01:00"]),
            "close": [7592.0],
        })
        self.assertIsNone(_verified_es_last_candle(stale, download))
        self.assertIsNone(_verified_es_last_candle(None, download))

    def test_report_model_valued_at_prior_cash_close(self):
        download = datetime.fromisoformat("2026-11-11T11:01:19+01:00")
        context = _model_valuation_context(
            {"valuation": "2026-11-10T21:00:00+00:00"}, download)
        self.assertEqual(context["label"], "10/11/2026 22:00")
        self.assertTrue(context["prior_cash_session"])

    def test_ui_no_longer_labels_previous_cash_spot_as_current_es(self):
        root = Path(__file__).resolve().parent.parent
        replay = (root / "edge_replay_panel.py").read_text(encoding="utf-8")
        official = (root / "edge_triptych.py").read_text(encoding="utf-8")
        self.assertIn('st.metric("ES future · ultima chiusura 5m"', replay)
        self.assertIn("last_es[\"price\"]", replay)
        self.assertIn("Copertura del modello, NON freschezza", replay)
        self.assertNotIn('st.metric("Spot ES equivalente"', replay)
        self.assertIn('SPX Cboe + base · riferimento', official)

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
