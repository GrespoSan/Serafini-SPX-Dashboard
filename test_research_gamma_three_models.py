"""Synthetic checks for the independent research modules.

They intentionally do not download market data or write to Supabase.
"""
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from research_gamma_three_models_ui import oi_only_rows
from edge_curve_replay import _curve_points, _last_price

ROME = ZoneInfo("Europe/Rome")


class OIOnlyResearchTests(unittest.TestCase):
    def test_only_oi_and_reconcile_serafini(self):
        strike = 7501.0
        core = [{"strike_spx": float(7400 + i), "net_gamma": 0.}
                for i in range(49)]
        core.append({"strike_spx": strike, "net_gamma": 2.4})
        snapshot = {"data": {"options": [
            {"option": "SPXW261113C07501000", "gamma": .2,
             "open_interest": 10, "volume": 5},
            {"option": "SPXW261113P07501000", "gamma": .1,
             "open_interest": 4, "volume": 2},
            {"option": "SPXW261120C07501000", "gamma": 100,
             "open_interest": 1000, "volume": 0},
        ]}}
        rows = oi_only_rows(snapshot, date(2026, 11, 13), core)
        item = next(row for row in rows if row["Strike SPX"] == strike)
        self.assertEqual(len(rows), 50)
        self.assertAlmostEqual(item["NET GAMMA Serafini"], 2.4)
        self.assertAlmostEqual(item["NET GAMMA solo OI"], 1.6)

    def test_mismatched_original_is_rejected(self):
        core = [{"strike_spx": float(7400 + i), "net_gamma": 0.}
                for i in range(49)]
        core.append({"strike_spx": 7501.0, "net_gamma": 99.})
        snapshot = {"data": {"options": [
            {"option": "SPXW261113C07501000", "gamma": .2,
             "open_interest": 10, "volume": 5},
        ]}}
        with self.assertRaises(ValueError):
            oi_only_rows(snapshot, date(2026, 11, 13), core)


class GEXCurveReplayTests(unittest.TestCase):
    def test_curve_uses_saved_values_and_fixed_basis(self):
        edge = {"curve": [{"spot_spx": 7480, "net_gex": -2e9},
                          {"spot_spx": 7520, "net_gex": 3e9}]}
        self.assertEqual(_curve_points(edge, 15),
                         [(7495., -2.), (7535., 3.)])

    def test_duplicate_curve_prices_rejected(self):
        edge = {"curve": [{"spot_spx": 7480, "net_gex": -2e9},
                          {"spot_spx": 7480, "net_gex": 3e9}]}
        with self.assertRaises(ValueError):
            _curve_points(edge, 10)

    def test_only_closed_es_candle_and_staleness(self):
        at = datetime(2026, 11, 13, 16, 0, tzinfo=ROME)
        candles = pd.DataFrame({
            "bar_close": [at-timedelta(minutes=5), at+timedelta(minutes=5)],
            "close": [7500., 7600.]
        })
        self.assertEqual(_last_price(candles, at), 7500.)
        self.assertIsNone(_last_price(candles, at+timedelta(minutes=17)))


if __name__ == "__main__":
    unittest.main()
