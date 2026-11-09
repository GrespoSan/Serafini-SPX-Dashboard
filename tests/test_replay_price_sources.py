"""Massive vs Yahoo Replay: no source blending and no invented OHLC."""

import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from replay_prices import (load_es_candles, massive_covers_requested_interval,
                           remove_suspect_gap_bars)

ROME = ZoneInfo("Europe/Rome")
DAY = date(2026, 11, 9)


def _moment(hour, minute):
    return datetime(2026, 11, 9, hour, minute, tzinfo=ROME)


def _frame_times(start, end):
    ticks = pd.date_range(start=start, end=end, freq="5min", tz=ROME)
    return pd.DataFrame({
        "bar_close": ticks,
        "open": [7435.] * len(ticks), "high": [7437.] * len(ticks),
        "low": [7434.] * len(ticks), "close": [7436.] * len(ticks),
    })


class ReplayPriceSourceTests(unittest.TestCase):
    def test_yahoo_spurious_1150_is_excluded_without_creating_new_bar(self):
        rows = pd.DataFrame({
            "bar_close": [_moment(11, 35), _moment(11, 40),
                          _moment(11, 50), _moment(11, 55)],
            "open": [7438., 7438.5, 7402., 7438.5],
            "high": [7440., 7438.5, 7440.75, 7440.],
            "low": [7435.75, 7436.75, 7402., 7436.75],
            "close": [7438.75, 7436.75, 7438.25, 7438.],
        })
        cleaned, excluded = remove_suspect_gap_bars(rows)
        self.assertEqual(excluded, ["11:50"])
        self.assertEqual(cleaned["bar_close"].dt.strftime("%H:%M").tolist(),
                         ["11:35", "11:40", "11:55"])

    def test_normal_candle_or_normal_five_minute_sequence_is_preserved(self):
        rows = pd.DataFrame({
            "bar_close": [_moment(11, 40), _moment(11, 45), _moment(11, 50)],
            "open": [7438., 7402., 7438.5],
            "high": [7438.5, 7440.75, 7440.],
            "low": [7436., 7402., 7436.75],
            "close": [7436.75, 7438.25, 7438.],
        })
        cleaned, excluded = remove_suspect_gap_bars(rows)
        self.assertEqual(len(cleaned), 3)
        self.assertEqual(excluded, [])

    def test_massive_full_snapshot_coverage_is_required(self):
        all_bars = _frame_times(_moment(1, 5), _moment(15, 50))
        self.assertTrue(massive_covers_requested_interval(
            all_bars, _moment(15, 50) + timedelta(seconds=9)))
        partial = all_bars.loc[all_bars["bar_close"] <= _moment(8, 10)]
        self.assertFalse(massive_covers_requested_interval(
            partial, _moment(15, 50) + timedelta(seconds=9)))
        with_gap = all_bars.loc[all_bars["bar_close"] != _moment(11, 45)]
        self.assertFalse(massive_covers_requested_interval(
            with_gap, _moment(15, 50) + timedelta(seconds=9)))

    def test_source_never_blended_with_yahoo(self):
        partial_massive = _frame_times(_moment(1, 5), _moment(8, 10))
        yahoo = _frame_times(_moment(1, 5), _moment(15, 50))
        yahoo.attrs["candle_source"] = "Yahoo ES=F · 5m verificato"
        with patch("replay_prices.st.secrets", {
                "MASSIVE_API_KEY": "dummy", "MASSIVE_ES_CONTRACT": "ESZ6"}), \
             patch("replay_prices._massive_candles", return_value=partial_massive), \
             patch("replay_prices._yahoo_candles", return_value=yahoo):
            result = load_es_candles(
                DAY, required_until=_moment(15, 50) + timedelta(seconds=9))
        self.assertEqual(result.attrs["candle_source"], "Yahoo ES=F · 5m verificato")
        self.assertIn("ultima candela 08:10", result.attrs["massive_fallback_reason"])
        self.assertEqual(len(result), len(yahoo))


if __name__ == "__main__":
    unittest.main()
