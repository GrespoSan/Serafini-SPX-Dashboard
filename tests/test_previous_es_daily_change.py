"""SPX ES previous Daily range and signed return must use Massive futures.

API credentials stay in Streamlit Secrets, never in tests or source code.
"""
import unittest
from datetime import date
from unittest.mock import patch

from daily_volatility import previous_es_daily_range
from massive_futures_daily import previous_daily_context, parse_session
from vix_regime import combined_breakout_buffer


class DailyMovementTests(unittest.TestCase):
    def test_es_change_and_range_use_same_trade_closes(self):
        bars = [
            parse_session({"ticker":"ESZ6","session_end_date":"2026-10-28",
                           "high":7819.5,"low":7750.25,"close":7760.75,
                           "settlement_price":7783.25},"ESZ6"),
            parse_session({"ticker":"ESZ6","session_end_date":"2026-10-29",
                           "high":7773.75,"low":7709.50,"close":7710.25,
                           "settlement_price":7754.25},"ESZ6"),
        ]
        result = previous_daily_context(bars)
        self.assertAlmostEqual(result["range_points"],64.25)
        self.assertAlmostEqual(result["range_pct"],64.25/7710.25*100,places=5)
        self.assertAlmostEqual(result["change_pct"],(7710.25/7760.75-1)*100,places=5)
        self.assertLess(result["change_pct"],0)
        self.assertEqual(result["ticker"],"ESZ6")

    def test_serafini_threshold_uses_signed_close_to_close_not_range(self):
        self.assertEqual(combined_breakout_buffer(1.2, -1.81)["buffer_es_points"], 15)
        self.assertEqual(combined_breakout_buffer(1.2, -0.65)["buffer_es_points"], 10)
        self.assertEqual(combined_breakout_buffer(1.2, -1.0)["buffer_es_points"], 10)
        self.assertEqual(combined_breakout_buffer(-0.2, 1.8)["buffer_es_points"], 15)
        self.assertEqual(combined_breakout_buffer(None, -1.81)["buffer_es_points"], 15)
        self.assertIsNone(combined_breakout_buffer(None, -0.65)["buffer_es_points"])
        self.assertIsNone(combined_breakout_buffer(1.1, None)["buffer_es_points"])
        self.assertIn("Close/Close", combined_breakout_buffer(1.2, -1.81)["rule"])
        self.assertNotIn("range", combined_breakout_buffer(1.2, -1.81)["rule"].lower())

    def test_massive_range_expansion_and_compression_independent_of_direction(self):
        from massive_futures_daily import previous_daily_context
        # A bearish session can be compressing, and an up session expanding.
        before = parse_session({"ticker": "ESZ6", "session_end_date": "2026-10-28",
            "high": 110., "low": 90., "close": 100.}, "ESZ6")
        down = parse_session({"ticker": "ESZ6", "session_end_date": "2026-10-29",
            "high": 102., "low": 99., "close": 99.5}, "ESZ6")
        values = previous_daily_context([before, down])
        self.assertEqual(values["range_trend"], "COMPRESSIONE")
        self.assertAlmostEqual(values["previous_range_pct"], 20)
        self.assertLess(values["change_pct"], 0)

    def test_adapter_uses_streamlit_secrets_and_massive_not_yahoo(self):
        result = {"status":"VALIDO","ticker":"ESZ6",
                  "day":"2026-10-29","range_points":64.25,
                  "range_pct":.833,"change_pct":-.6507}
        with patch("daily_volatility.st.secrets",
                   {"MASSIVE_API_KEY":"fake-key","MASSIVE_ES_CONTRACT":"ESZ6"}):
            with patch("daily_volatility._fetch_massive_es",
                       return_value=result) as fetch:
                observed = previous_es_daily_range(date(2026,10,30))
        self.assertEqual(observed,result)
        fetch.assert_called_once_with(date(2026,10,30),"ESZ6","fake-key")

    def test_no_key_or_contract_does_not_fallback_to_yahoo(self):
        with patch("daily_volatility.st.secrets",{}):
            with self.assertRaisesRegex(ValueError,"MASSIVE_API_KEY"):
                previous_es_daily_range(date(2026,10,30))
        with patch("daily_volatility.st.secrets",
                   {"MASSIVE_API_KEY":"private-test-token"}):
            with self.assertRaisesRegex(ValueError,"MASSIVE_ES_CONTRACT"):
                previous_es_daily_range(date(2026,10,30))


if __name__=="__main__":
    unittest.main()
