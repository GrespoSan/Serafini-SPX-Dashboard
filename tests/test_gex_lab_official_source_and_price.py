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

from edge_replay_panel import _latest_official_edge_for_lab, _latest_es_quote
from gex_price_sensitivity_lab import (
    _add_delayed_es_marker, curve_extrema, scenario_gex,
)

ROME = ZoneInfo("Europe/Rome")
ROOT = Path(__file__).resolve().parents[1]


class OfficialLabSourceTests(unittest.TestCase):
    def test_1700_official_is_selected_after_1546_replay(self):
        day = date(2026, 11, 12)
        early = datetime(2026, 11, 12, 15, 51, tzinfo=ROME)
        official = datetime(2026, 11, 12, 17, 1, tzinfo=ROME)
        first = "2026-W46/snapshots/SPX_CBOE_20261112_morning.json.gz"
        second = "2026-W46/snapshots/SPX_CBOE_20261112_evening.json.gz"
        index = {
            first: ("Pomeriggio · 08:50 Chicago", "15:51", early),
            second: ("USA · 10:00 Chicago (17:00 ordinario)", "17:01", official),
        }
        raw = b"official-original-cboe"
        report = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "symbol": "SPX", "mode": "0DTE", "expiries": [day.isoformat()],
            "curve": [{"spot_spx": 7600, "net_gex": 10e9},
                      {"spot_spx": 7650, "net_gex": 15e9}],
        }
        logs = [{"name": "official-log-01.json"}]
        with patch("gamma_section._snapshot_log_index", return_value=index), \
             patch("edge_triptych._cached_official_snapshot", return_value=raw) as fetch, \
             patch("edge_auto.result_from_snapshot", return_value=report) as calc:
            e, stamp, logical, filename = _latest_official_edge_for_lab(
                "2026-W46", object(), logs, day, 23.0)
        self.assertIs(e, report)
        self.assertEqual(stamp, official)
        self.assertEqual(logical, "17:00")
        self.assertEqual(filename, second.rsplit("/", 1)[1])
        calc.assert_called_once_with(raw, 23.0)
        self.assertEqual(fetch.call_args.args[1], filename)

    def test_no_official_source_is_not_replaced_with_replay(self):
        with patch("gamma_section._snapshot_log_index", return_value={}):
            with self.assertRaisesRegex(ValueError, "Nessun download SPX UFFICIALE"):
                _latest_official_edge_for_lab(
                    "2026-W46", object(), [], date(2026, 11, 12), 23)

    def test_wrong_official_0dte_expiry_is_not_used(self):
        day = date(2026, 11, 12)
        path = "2026-W46/snapshots/SPX_CBOE_20261112_1731.json.gz"
        index = {path: ("USA · 10:00 Chicago", "17:01",
                        datetime(2026, 11, 12, 17, 1, tzinfo=ROME))}
        raw = b"official-cboe"
        invalid = {"sha256": hashlib.sha256(raw).hexdigest(),
                   "symbol": "SPX", "mode": "0DTE",
                   "expiries": ["2026-11-13"]}
        with patch("gamma_section._snapshot_log_index", return_value=index), \
             patch("edge_triptych._cached_official_snapshot", return_value=raw), \
             patch("edge_auto.result_from_snapshot", return_value=invalid):
            with self.assertRaisesRegex(ValueError, "non coerente"):
                _latest_official_edge_for_lab(
                    "2026-W46", object(), [], day, 23)

    def test_replay_controls_render_below_summary_and_before_curve(self):
        panel = (ROOT / "edge_replay_panel.py").read_text(encoding="utf-8")
        start = panel.index("def show_replay(")
        body = panel[start:]
        self.assertLess(body.index("summary_panel = st.container()"),
                        body.index("replay_controls_panel = st.container()"))
        self.assertLess(body.index("replay_controls_panel = st.container()"),
                        body.index("curve_lab_panel = st.container()"))
        self.assertLess(body.index("with replay_controls_panel:"),
                        body.index('current = st.selectbox("Rilevamento Replay da studiare"'))
        self.assertLess(body.index("with summary_panel:"),
                        body.index("Confronto informativo su dati 0DTE"))
        self.assertLess(body.index("Confronto informativo su dati 0DTE"),
                        body.index('with st.expander("Curva teorica GEX Edge"'))
        self.assertLess(body.index("with curve_lab_panel:"),
                        body.index('with st.expander("Curva teorica GEX Edge"'))
        self.assertIn('compare = st.selectbox(', body)
        self.assertEqual(body.count('Rilevamento Replay da studiare'), 1)
        self.assertIn("Valutazione modello GEX · REPLAY", body)

    def test_official_quality_warning_is_not_confused_with_replay(self):
        panel = (ROOT / "edge_replay_panel.py").read_text(encoding="utf-8")
        self.assertIn("validazione GEX Edge NON", panel)
        self.assertIn("qualità del download ufficiale", panel)
        self.assertIn("una fotografia DIFFERENTE", panel)
        self.assertIn("quality_pct", panel)
        self.assertIn("download {lab_clock:%d/%m/%Y %H:%M:%S}", panel)

    def test_laboratory_default_official_and_replay_remains_available(self):
        replay = (ROOT / "edge_replay_panel.py").read_text(encoding="utf-8")
        official = (ROOT / "edge_triptych.py").read_text(encoding="utf-8")
        self.assertIn('("Ultimo download ufficiale", f"Replay {current}")', replay)
        self.assertIn('source_kind, lab_last_es = "Ufficiale", None', replay)
        self.assertIn('show_replay(week, store, _plot, logs=logs or [])', official)
        self.assertIn('source_kind="Ufficiale"', official)


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
        self.assertIn("curve_extrema(curve, basis)", lab)


if __name__ == "__main__":
    unittest.main()
