"""Edge-only checks for cash SPX freshness and existing curve what-if.

No real Cboe downloads or new gamma assumptions. Serafini is out of scope.
"""
import gzip
import json
import unittest
from datetime import datetime

from gex_cash_provenance import extract_spx_trade_time, describe_spx_trade
from gex_price_sensitivity_lab import scenario_gex


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

    def test_lab_does_not_claim_wall_recomputation_or_change_serafini(self):
        from pathlib import Path
        root=Path(__file__).resolve().parents[1]
        panel=(root/"edge_replay_panel.py").read_text(encoding="utf-8")
        lab=(root/"gex_price_sensitivity_lab.py").read_text(encoding="utf-8")
        self.assertIn("show_price_lab(edge, shift, last_es", panel)
        self.assertIn("NON si ricostruiscono nuove",lab)
        self.assertIn("interpolazione lineare",lab)
        self.assertNotIn("from net_gamma import",lab)


if __name__=="__main__":
    unittest.main()
