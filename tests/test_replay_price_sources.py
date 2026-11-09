"""Regressioni Replay ES: solo Massive; dati parziali non visualizzati."""
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import replay_prices
from replay_prices import load_es_candles, massive_covers_requested_interval

ROME = ZoneInfo("Europe/Rome")
DAY = date(2026, 11, 9)


def moment(h, m):
    return datetime(2026, 11, 9, h, m, tzinfo=ROME)


def candles(first, last):
    times = pd.date_range(start=first, end=last, freq="5min", tz=ROME)
    data = pd.DataFrame({
        "bar_close": times,
        "open": [7435.] * len(times),
        "high": [7437.] * len(times),
        "low": [7434.] * len(times),
        "close": [7436.] * len(times),
    })
    data.attrs["candle_source"] = "Massive Futures ESZ6 · CME 5m"
    return data


class ReplayMassiveOnlyTests(unittest.TestCase):
    def test_no_yahoo_fetcher_exists(self):
        self.assertFalse(hasattr(replay_prices, "_yahoo_candles"))
        self.assertFalse(hasattr(replay_prices, "remove_suspect_gap_bars"))

    def test_full_massive_coverage_is_required(self):
        complete = candles(moment(1, 5), moment(15, 50))
        snapshot = moment(15, 50) + timedelta(seconds=9)
        self.assertTrue(massive_covers_requested_interval(complete, snapshot))
        partial = complete.loc[complete.bar_close <= moment(8, 30)]
        self.assertFalse(massive_covers_requested_interval(partial, snapshot))
        with_gap = complete.loc[complete.bar_close != moment(11, 45)]
        self.assertFalse(massive_covers_requested_interval(with_gap, snapshot))

    def test_full_massive_is_returned_unchanged(self):
        complete = candles(moment(1, 5), moment(15, 50))
        with patch("replay_prices.st.secrets", {
            "MASSIVE_API_KEY": "demo-test-key",
            "MASSIVE_ES_CONTRACT": "ESZ6",
        }), patch("replay_prices._massive_candles", return_value=complete) as source:
            result = load_es_candles(
                DAY, required_until=moment(15, 50) + timedelta(seconds=9))
        self.assertIs(result, complete)
        self.assertEqual(result.attrs["candle_source"],
                         "Massive Futures ESZ6 · CME 5m")
        source.assert_called_once_with(DAY, "ESZ6", "demo-test-key")

    def test_partial_massive_does_not_silently_fallback(self):
        partial = candles(moment(1, 5), moment(8, 30))
        with patch("replay_prices.st.secrets", {
            "MASSIVE_API_KEY": "demo-test-key",
            "MASSIVE_ES_CONTRACT": "ESZ6",
        }), patch("replay_prices._massive_candles", return_value=partial):
            with self.assertRaisesRegex(ValueError, "ultima candela 08:30"):
                load_es_candles(
                    DAY, required_until=moment(15, 50) + timedelta(seconds=9))

    def test_missing_key_fails_without_fetching_other_sources(self):
        with patch("replay_prices.st.secrets", {}), patch(
            "replay_prices._massive_candles"
        ) as source:
            with self.assertRaisesRegex(ValueError, "non configurato"):
                load_es_candles(DAY, required_until=moment(7, 30))
        source.assert_not_called()

    def test_naive_or_missing_snapshot_time_rejected(self):
        with self.assertRaisesRegex(ValueError, "privo di fuso"):
            load_es_candles(DAY, required_until=datetime(2026, 11, 9, 7, 30))
        with self.assertRaisesRegex(ValueError, "assente"):
            load_es_candles(DAY)


if __name__ == "__main__":
    unittest.main()
