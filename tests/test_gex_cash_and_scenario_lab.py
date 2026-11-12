"""Edge-only checks for cash SPX freshness and existing curve what-if.

No real Cboe downloads or new gamma assumptions. Serafini is out of scope.
"""
import gzip
import json
import unittest
from datetime import datetime

from gex_cash_provenance import extract_spx_trade_time, describe_spx_trade
from gex_price_sensitivity_lab import (
    scenario_gex, _signed_metric_color, _signed_gex_metric,
    _gex_reference_times, _add_price_reference_markers,
)


class SPXIndexPriceProvenance(unittest.TestCase):
    def test_original_json_index_timestamp_not_option_timestamp(self):
        doc = {"symbol":"SPX", "timestamp":"2026-11-11 10:01:08",
               "data":{
                   "current_price":7520.9302,
                   "last_trade_time":"2026-11-10T16:14:59",
                   "options":[{"last_trade_time":"2026-11-11T10:01:01"}]}}
        self.assertEqual(extract_spx_trade_time(doc), "2026-11-10T16:14:59")
        raw = json.dumps(doc).encode("utf-8")
        self.assertEqual(extract_spx_trade_time(raw), "2026-11-10T16:14:59")
        self.assertEqual(extract_spx_trade_time(gzip.compress(raw)),
                         "2026-11-10T16:14:59")
        note, status = describe_spx_trade(
            "2026-11-10T16:14:59",
            datetime.fromisoformat("2026-11-11T11:01:19+01:00"))
        self.assertEqual(status,"seduta_precedente")
        self.assertIn("10/11/2026 16:14:59", note)
        self.assertIn("fuso non dichiarato",note)
        self.assertIn("non del future ES",note)

    def test_absent_index_time_not_substituted_with_chain_timestamp(self):
        doc = {"timestamp": "2026-11-11 10:01:08",
               "data": {"options": [
                   {"last_trade_time":"2026-11-11T10:01:01"}]}}
        self.assertIsNone(extract_spx_trade_time(doc))
        note, status = describe_spx_trade(None)
        self.assertIn("N/D", note)
        self.assertEqual(status, "non_verificabile")

    def test_timezone_aware_is_converted_for_display_only(self):
        note, status = describe_spx_trade(
            "2026-11-11T15:30:01+00:00",
            datetime.fromisoformat("2026-11-11T17:01:00+01:00"))
        self.assertIn("16:30:01",note)
        self.assertIn("Roma, fuso convertito",note)
        self.assertEqual(status,"stesso_giorno")

    def test_invalid_or_unknown_index_timestamp_is_nd(self):
        for raw in ("", "garbage", "2026-30-40", 123, None):
            with self.subTest(raw=raw):
                self.assertIsNone(extract_spx_trade_time(
                    {"data":{"last_trade_time":raw}}))
        self.assertEqual(extract_spx_trade_time(b"not json"), None)

    def test_only_gex_sections_use_timestamp_helpers(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        replay=(root/"edge_replay_panel.py").read_text(encoding="utf-8")
        official=(root/"edge_triptych.py").read_text(encoding="utf-8")
        serafini=(root/"gamma_replay.py").read_text(encoding="utf-8")
        self.assertIn('describe_spx_trade(',replay)
        self.assertIn('extract_spx_trade_time(raw)',official)
        self.assertNotIn("gex_cash_provenance",serafini)


class ExistingCurveScenarioTests(unittest.TestCase):
    def setUp(self):
        self.curve = [
            {"spot_spx": 7490.0, "net_gex": -4.0e9},
            {"spot_spx": 7520.0, "net_gex": -2.0e9},
            {"spot_spx": 7540.0, "net_gex": 2.0e9},
            {"spot_spx": 7580.0, "net_gex": 5.0e9},
        ]

    def test_existing_curve_exact_and_midpoint_interpolation(self):
        self.assertEqual(scenario_gex(self.curve, 7520.), -2.0e9)
        self.assertEqual(scenario_gex(self.curve, 7530.), 0)
        self.assertEqual(scenario_gex(self.curve, 7580.), 5.0e9)
        self.assertEqual(scenario_gex(self.curve, 7505.), -3.0e9)

    def test_no_price_extrapolation_and_invalid_curve(self):
        self.assertIsNone(scenario_gex(self.curve, 7489.99))
        self.assertIsNone(scenario_gex(self.curve, 7580.01))
        self.assertIsNone(scenario_gex([], 7520))
        self.assertIsNone(scenario_gex([
            {"spot_spx": 7500, "net_gex": 1},
            {"spot_spx": 7500, "net_gex": 2}], 7500))
        with self.assertRaises(ValueError):
            scenario_gex(self.curve, float("nan"))

    def test_lab_shows_real_snapshot_and_separate_valuation_clock(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        panel = (root / "edge_replay_panel.py").read_text(encoding="utf-8")
        lab = (root / "gex_price_sensitivity_lab.py").read_text(encoding="utf-8")
        download = datetime.fromisoformat("2026-11-11T11:01:19+01:00")
        snapshot, valuation = _gex_reference_times(
            download, "2026-11-10T21:00:00+00:00")
        self.assertEqual(snapshot, "11/11/2026 11:01:19 Roma")
        self.assertEqual(valuation, "10/11/2026 22:00 Roma")
        self.assertIn("download_clock=lab_clock, slot=lab_slot", panel)
        self.assertIn("Fotografia Cboe del calcolo GEX", lab)
        self.assertIn("Valutazione teorica del modello", lab)
        self.assertIn("non è archiviato separatamente", lab)

    def test_lab_never_infers_timezone_or_invents_missing_calculation_clock(self):
        download = datetime.fromisoformat("2026-11-11T11:01:19")
        snapshot, valuation = _gex_reference_times(
            download, "2026-11-10T21:00:00")
        self.assertEqual(snapshot, "N/D")
        self.assertEqual(valuation, "10/11/2026 21:00 (fuso non dichiarato)")
        self.assertEqual(_gex_reference_times(None, None), ("N/D", "N/D"))
        self.assertEqual(_gex_reference_times(None, "not a date"), ("N/D", "N/D"))

    def test_laboratory_after_edge_curve_and_open_by_default(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        panel = (root / "edge_replay_panel.py").read_text(encoding="utf-8")
        lab = (root / "gex_price_sensitivity_lab.py").read_text(encoding="utf-8")
        self.assertLess(
            panel.index('with st.expander("Curva teorica GEX Edge"'),
            panel.index('    show_price_lab(\n        lab_edge, shift, lab_last_es'),
        )
        self.assertIn('st.expander("LABORATORIO GEX — scenario prezzo (sperimentale)"', lab)
        self.assertIn('expanded=True', lab)
        self.assertNotIn('expanded=False', lab)

    def test_gray_original_dot_and_yellow_scenario_dot_are_on_same_curve(self):
        import plotly.graph_objects as go
        chart = go.Figure()
        _add_price_reference_markers(
            chart,
            original_es=7543.93,
            original_gex_on_curve=-3.64e9,
            scenario_es=7608.75,
            scenario_gex_on_curve=5.32e9,
        )
        self.assertEqual(len(chart.data), 2)
        original, scenario = chart.data
        self.assertEqual(original.name, "Originale (curva)")
        self.assertEqual(original.mode, "markers")
        self.assertEqual(original.marker.color, "#a0a0a0")
        self.assertEqual(original.x[0], 7543.93)
        self.assertAlmostEqual(original.y[0], -3.64)
        self.assertEqual(scenario.name, "Scenario teorico")
        self.assertEqual(scenario.marker.color, "#eabf4a")
        self.assertEqual(scenario.x[0], 7608.75)
        self.assertAlmostEqual(scenario.y[0], 5.32)
        self.assertIn("Originale sulla curva", original.hovertemplate)
        self.assertIn("Scenario teorico", scenario.hovertemplate)

    def test_no_new_curve_or_model_and_marker_overlap_is_safe(self):
        import plotly.graph_objects as go
        chart = go.Figure()
        _add_price_reference_markers(
            chart, original_es=7543.93, original_gex_on_curve=-3.64e9,
            scenario_es=7543.93, scenario_gex_on_curve=-3.64e9,
        )
        self.assertEqual(chart.data[0].x, chart.data[1].x)
        self.assertEqual(chart.data[0].y, chart.data[1].y)
        self.assertEqual(chart.data[0].marker.color, "#a0a0a0")
        self.assertEqual(chart.data[1].marker.color, "#eabf4a")

    def test_three_gex_values_change_color_with_sign(self):
        import math
        self.assertEqual(_signed_metric_color(-3.64e9), "#e64b4b")
        self.assertEqual(_signed_metric_color(5.32e9), "#22b573")
        self.assertEqual(_signed_metric_color(8.96e9), "#22b573")
        self.assertEqual(_signed_metric_color(-8.96e9), "#e64b4b")
        self.assertEqual(_signed_metric_color(0), "inherit")
        self.assertEqual(_signed_metric_color(math.nan), "inherit")

        class Column:
            def __init__(self):
                self.markups = []
            def markdown(self, markup, **kwargs):
                self.markups.append((markup, kwargs))

        for label, amount, suffix, number, color in (
            ("NET GEX originale", -3.64e9, "mld USD",
             "-3.64 mld USD", "#e64b4b"),
            ("NET GEX teorico allo scenario", 5.32e9, "mld USD",
             "+5.32 mld USD", "#22b573"),
            ("Δ teorico dalla curva al cash SPX", 8.96e9, "mld",
             "+8.96 mld", "#22b573"),
            ("Δ teorico dalla curva al cash SPX", -2.5e9, "mld",
             "-2.50 mld", "#e64b4b"),
            ("NET GEX originale", 0, "mld USD",
             "0.00 mld USD", "inherit"),
        ):
            column = Column()
            _signed_gex_metric(column, label, amount, suffix=suffix)
            self.assertEqual(len(column.markups), 1)
            html, kwargs = column.markups[0]
            self.assertIn(label, html)
            self.assertIn(number, html)
            self.assertIn("color:" + color, html)
            self.assertTrue(kwargs["unsafe_allow_html"])

    def test_lab_does_not_claim_wall_recomputation_or_change_serafini(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        panel=(root/"edge_replay_panel.py").read_text(encoding="utf-8")
        lab=(root/"gex_price_sensitivity_lab.py").read_text(encoding="utf-8")
        self.assertIn("show_price_lab(\n        lab_edge, shift, lab_last_es", panel)
        self.assertIn("NON si ricostruiscono nuove",lab)
        self.assertIn("interpolazione lineare",lab)
        self.assertNotIn("from net_gamma import",lab)


if __name__=="__main__":
    unittest.main()
