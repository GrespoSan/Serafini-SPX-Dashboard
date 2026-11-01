"""SPX ES previous Daily range and signed return must use Massive futures.

API credentials stay in Streamlit Secrets, never in tests or source code.
"""
import unittest
from datetime import date
from unittest.mock import patch

from daily_volatility import previous_es_daily_range
from massive_futures_daily import previous_daily_context, parse_session
from vix_regime import combined_breakout_buffer, volatility_context


class DailyMovementTests(unittest.TestCase):
    def test_es_change_and_range_use_same_trade_closes(self):
        bars = [
            parse_session({"ticker":"ESZ6","session_end_date":"2026-10-28",
                           "open":7780.,"high":7819.5,"low":7750.25,"close":7760.75,
                           "settlement_price":7783.25},"ESZ6"),
            parse_session({"ticker":"ESZ6","session_end_date":"2026-10-29",
                           "open":7760.,"high":7773.75,"low":7709.50,"close":7710.25,
                           "settlement_price":7754.25},"ESZ6"),
        ]
        result = previous_daily_context(bars)
        self.assertAlmostEqual(result["range_points"],64.25)
        self.assertAlmostEqual(result["range_pct"],64.25/7710.25*100,places=5)
        self.assertAlmostEqual(result["change_pct"],(7710.25/7760.75-1)*100,places=5)
        self.assertLess(result["change_pct"],0)
        self.assertEqual(result["ticker"],"ESZ6")
        self.assertEqual(result["open"],7760.)

    def test_serafini_threshold_uses_red_daily_high_low_range_not_close_to_close(self):
        # 15 punti quando il range e' > 1% e Close < Open.
        self.assertEqual(combined_breakout_buffer(1.2, 1.81, True)["buffer_es_points"], 15)
        self.assertEqual(combined_breakout_buffer(1.2, 1.01, True)["buffer_es_points"], 15)
        self.assertEqual(combined_breakout_buffer(1.2, 1.00, True)["buffer_es_points"], 10)
        self.assertEqual(combined_breakout_buffer(1.2, 0.65, True)["buffer_es_points"], 10)
        # Range ampio ma candela verde / doji: 10 in contango.
        self.assertEqual(combined_breakout_buffer(1.2, 1.81, False)["buffer_es_points"], 10)
        # Backwardation prevale indipendentemente dal colore/range.
        self.assertEqual(combined_breakout_buffer(-0.2, 0.65, False)["buffer_es_points"], 15)
        self.assertEqual(combined_breakout_buffer(-0.2, None, None)["buffer_es_points"], 15)
        self.assertEqual(combined_breakout_buffer(None, 1.81, True)["buffer_es_points"], 15)
        # Mancanze non devono forzare 10 punti.
        self.assertIsNone(combined_breakout_buffer(None, 0.65, True)["buffer_es_points"])
        self.assertIsNone(combined_breakout_buffer(1.1, 1.81, None)["buffer_es_points"])
        self.assertIsNone(combined_breakout_buffer(1.1, None, None)["buffer_es_points"])
        self.assertIn("High-Low", combined_breakout_buffer(1.2, 1.81, True)["rule"])
        self.assertNotIn("Close/Close", combined_breakout_buffer(1.2, 1.81, True)["rule"])

    def test_volatility_context_uses_red_candle_even_with_positive_close_to_close(self):
        # Il Close/Close positivo non esclude una candela rossa.
        day = date(2026, 10, 30)
        es = {"status": "VALIDO", "ticker": "ESZ6", "day": "2026-10-29",
              "open": 101.0, "high": 101.50, "low": 99.80, "close": 100.50,
              "range_pct": 1.69, "change_pct": 0.70}
        vx = {"contango_pct": 1.2, "day": "2026-10-29"}
        with patch("vix_regime.previous_vix_curve", return_value=vx):
            with patch("daily_volatility.previous_es_daily_range", return_value=es):
                result = volatility_context(day)
        buffer = result["breakout_buffer"]
        self.assertEqual(buffer["buffer_es_points"], 15)
        self.assertTrue(buffer["inputs"]["previous_es_daily_candle_red"])
        self.assertEqual(buffer["inputs"]["previous_es_daily_range_pct"], 1.69)

    def test_missing_daily_open_does_not_force_low_buffer(self):
        day = date(2026, 10, 30)
        es = {"status": "VALIDO", "open": None, "close": 100.0,
              "range_pct": 1.5, "change_pct": -2.0}
        with patch("vix_regime.previous_vix_curve", return_value={"contango_pct": 1.0}):
            with patch("daily_volatility.previous_es_daily_range", return_value=es):
                result = volatility_context(day)
        self.assertIsNone(result["breakout_buffer"]["buffer_es_points"])

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
