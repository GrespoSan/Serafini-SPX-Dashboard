"""Isolated SPX GEX laboratory: official/replay separation and delayed ES.

All data synthetic. Never fetches live market data or modifies Serafini Gamma.
"""
import hashlib
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go

from edge_replay_panel import _latest_es_quote
from edge_latest_source import latest_gex_reading
from gex_price_sensitivity_lab import (
    _add_delayed_es_marker, curve_extrema, scenario_gex,
)

ROME = ZoneInfo("Europe/Rome")
ROOT = Path(__file__).resolve().parents[1]


class FakeStore:
    def __init__(self, snapshot_names=(), replay_names=()):
        self.snapshot_names = list(snapshot_names)
        self.replay_names = list(replay_names)

    def list(self, path):
        if path.endswith("/snapshots/"):
            return [{"name": name} for name in self.snapshot_names]
        if path.startswith("replay-edge/"):
            return [{"name": name} for name in self.replay_names]
        return []


class LatestSourceTests(unittest.TestCase):
    """Real download timestamps, no source radio and no cross-source mixing."""

    def setUp(self):
        self.day = (datetime.now(ROME) - timedelta(days=1)).date()
        self.week = f"{self.day.isocalendar().year}-W{self.day.isocalendar().week:02d}"
        self.snapshot = f"SPX_CBOE_{self.day:%Y%m%d}_155925.json.gz"
        self.path = f"{self.week}/snapshots/{self.snapshot}"
        self.replay = f"{self.day.isoformat()}_18-30.json"
        self.official_clock = datetime.combine(
            self.day, datetime.min.time(), tzinfo=ROME).replace(
                hour=17, minute=0, second=18)
        self.replay_clock = self.official_clock.replace(hour=18, minute=30)

    def test_later_official_beats_earlier_replay(self):
        early = self.official_clock.replace(hour=15, minute=46)
        store = FakeStore([self.snapshot], [self.replay])
        index = {self.path: ("USA 17:00", "17:00", self.official_clock)}
        with patch("gamma_section._snapshot_log_index", return_value=index), \
             patch("edge_latest_source.read_pair", return_value=(
                 {}, {}, {"mode":"0DTE"}, early)):
            chosen = latest_gex_reading(
                self.week, store, [{"name": "official.json"}])
        self.assertEqual(chosen["kind"], "Ufficiale")
        self.assertEqual(chosen["filename"], self.snapshot)
        self.assertEqual(chosen["clock"], self.official_clock)

    def test_later_replay_beats_earlier_official(self):
        store = FakeStore([self.snapshot], [self.replay])
        index = {self.path: ("USA 17:00", "17:00", self.official_clock)}
        with patch("gamma_section._snapshot_log_index", return_value=index), \
             patch("edge_latest_source.read_pair", return_value=(
                 {}, {}, {"mode":"0DTE"}, self.replay_clock)):
            chosen = latest_gex_reading(
                self.week, store, [{"name": "official.json"}])
        self.assertEqual(chosen["kind"], "Replay")
        self.assertEqual(chosen["clock"], self.replay_clock)
        self.assertEqual(chosen["slot"], "18:30")

    def test_invalid_latest_replay_does_not_hide_official(self):
        store = FakeStore([self.snapshot], [self.replay])
        index = {self.path: ("USA 17:00", "17:00", self.official_clock)}
        with patch("gamma_section._snapshot_log_index", return_value=index), \
             patch("edge_latest_source.read_pair", side_effect=ValueError("SHA errato")):
            chosen = latest_gex_reading(self.week, store, [])
        self.assertEqual(chosen["kind"], "Ufficiale")

    def test_no_source_and_single_curve_controls(self):
        with patch("gamma_section._snapshot_log_index", return_value={}):
            self.assertIsNone(latest_gex_reading(self.week, FakeStore(), []))
        edge = (ROOT / "edge_triptych.py").read_text(encoding="utf-8")
        replay = (ROOT / "edge_replay_panel.py").read_text(encoding="utf-8")
        lab = (ROOT / "gex_price_sensitivity_lab.py").read_text(encoding="utf-8")
        self.assertNotIn('"Fonte dei rilevamenti GEX"', edge)
        self.assertNotIn('"Fotografia per il Laboratorio GEX"', replay)
        self.assertNotIn('with st.expander("Curva teorica GEX Edge"', replay)
        self.assertNotIn('with st.expander("Curva teorica GEX",', edge)
        self.assertIn("latest_gex_reading(week, store, logs or [])", edge)
        self.assertIn('key=f"{key}_historical_curve"', lab)

    def test_main_replay_latest_independent_of_optional_curve_history(self):
        panel = (ROOT / "edge_replay_panel.py").read_text(encoding="utf-8")
        start = panel.index("def show_replay(")
        body = panel[start:]
        self.assertIn('current = str(selected["slot"])', body)
        self.assertIn('compare = "Nessuno"', body)
        self.assertIn('history_options=names[:index]', body)
        self.assertNotIn('current = st.selectbox("Rilevamento Replay da studiare"', body)


class DelayedESQuoteTests(unittest.TestCase):
    def test_last_real_closed_five_minute_bar_with_source(self):
        now = datetime.now(ROME)
        day = now.date()
        close = now - timedelta(minutes=8)
        candles = pd.DataFrame({
            "bar_close": [close, now + timedelta(minutes=5)],
            "close": [7635.25, 999999.0]
        })
        candles.attrs["candle_source"] = "Test ES Futures 5m"
        _latest_es_quote.clear()
        with patch("edge_replay_panel.load_es_candles", return_value=candles):
            result = _latest_es_quote(day)
        self.assertEqual(result["price"], 7635.25)
        self.assertEqual(result["source"], "Test ES Futures 5m")
        self.assertGreaterEqual(result["age_minutes"], 7)
        self.assertLess(result["age_minutes"], 10)
        self.assertIn(close.strftime("%H:%M"), result["clock"])

    def test_historical_day_has_no_present_quote(self):
        _latest_es_quote.clear()
        past = datetime.now(ROME).date() - timedelta(days=3)
        with patch("edge_replay_panel.load_es_candles") as fetch:
            self.assertIsNone(_latest_es_quote(past))
            fetch.assert_not_called()


class CurveDisplayTests(unittest.TestCase):
    def test_max_min_from_same_curve_points_and_es_shift(self):
        curve = [
            {"spot_spx": 7475, "net_gex": -10e9},
            {"spot_spx": 7525, "net_gex": -13e9},
            {"spot_spx": 7630, "net_gex": 25e9},
            {"spot_spx": 7725, "net_gex": 18e9},
        ]
        extrema = curve_extrema(curve, 23)
        self.assertEqual(extrema["min"],
                         {"es": 7548., "spx": 7525., "net_gex": -13e9})
        self.assertEqual(extrema["max"],
                         {"es": 7653., "spx": 7630., "net_gex": 25e9})
        self.assertIsNone(curve_extrema([], 23))

    def test_third_marker_is_separate_from_original_and_scenario(self):
        chart = go.Figure()
        _add_delayed_es_marker(chart, es_price=7629.75,
                               gex_on_curve=3.38e9)
        self.assertEqual(len(chart.data), 1)
        point = chart.data[0]
        self.assertEqual(point.name, "Ultimo ES disponibile")
        self.assertEqual(point.marker.color, "#4cc6e5")
        self.assertEqual(point.x[0], 7629.75)
        self.assertAlmostEqual(point.y[0], 3.38)
        self.assertEqual(chart.layout.shapes[0].x0, 7629.75)

    def test_no_gex_extrapolation_outside_price_grid(self):
        curve = [{"spot_spx": 7500, "net_gex": -1e9},
                 {"spot_spx": 7600, "net_gex": 2e9}]
        self.assertIsNone(scenario_gex(curve, 7601))
        self.assertEqual(scenario_gex(curve, 7550), 5e8)

    def test_no_change_to_serafini_gamma_engine(self):
        lab = (ROOT / "gex_price_sensitivity_lab.py").read_text(encoding="utf-8")
        self.assertNotIn("from net_gamma import", lab)
        self.assertIn("scenario_gex(curve, delayed_price - basis)", lab)
        self.assertIn("Prezzo minimo ES della curva", lab)
        self.assertIn("Prezzo massimo ES della curva", lab)
        self.assertNotIn("curve_extrema(curve, basis)", lab)


if __name__ == "__main__":
    unittest.main()
