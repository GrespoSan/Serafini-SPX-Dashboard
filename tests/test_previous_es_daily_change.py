"""The previous ES session's direction is NOT the High-Low Daily range.

All inputs are fixtures: no network access, no SPX/GEX changes.
"""
import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

from daily_volatility import previous_es_daily_range
from vix_regime import render


def fixture(entries):
    idx = pd.to_datetime([day for day, _, _, _ in entries])
    return pd.DataFrame({
        "High": [high for _, high, _, _ in entries],
        "Low": [low for _, _, low, _ in entries],
        "Close": [close for _, _, _, close in entries],
    }, index=idx)


def calculate(rows, target):
    # Bypass the Streamlit cache when wrapped, so tests are deterministic.
    target_fn = getattr(previous_es_daily_range, "__wrapped__",
                        previous_es_daily_range)
    with patch("daily_volatility.yf.download", return_value=fixture(rows)):
        return target_fn(target)


class DailyMovementTests(unittest.TestCase):
    def test_green_when_yesterday_closed_above_previous_session(self):
        result = calculate([
            ("2026-10-27", 102, 99, 100),
            ("2026-10-28", 105, 100, 103),
            ("2026-10-29", 500, 499, 499),
        ], date(2026, 10, 29))
        self.assertEqual(result["day"], "2026-10-28")
        self.assertEqual(result["range_points"], 5)
        self.assertAlmostEqual(result["range_pct"], 5 / 103 * 100, places=5)
        self.assertAlmostEqual(result["change_pct"], 3.0)
        self.assertEqual(result["previous_close_day"], "2026-10-27")

    def test_red_when_yesterday_closed_below_previous_session(self):
        result = calculate([
            ("2026-10-28", 104, 98, 100),
            ("2026-10-29", 103, 97, 98),
        ], date(2026, 10, 30))
        self.assertEqual(result["range_points"], 6)
        self.assertEqual(result["change_pct"], -2.0)

    def test_no_previous_close_means_no_fabricated_daily_direction(self):
        result = calculate([
            ("2026-10-28", 105, 97, 100),
        ], date(2026, 10, 29))
        self.assertIsNone(result["change_pct"])
        self.assertIsNone(result["previous_close"])


if __name__ == "__main__":
    unittest.main()
