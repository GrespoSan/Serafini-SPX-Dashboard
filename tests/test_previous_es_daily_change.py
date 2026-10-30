"""SPX ES previous Daily range and signed return must use Massive futures.

API credentials stay in Streamlit Secrets, never in tests or source code.
"""
import unittest
from datetime import date
from unittest.mock import patch

from daily_volatility import previous_es_daily_range
from massive_futures_daily import previous_daily_context, parse_session


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
        self.assertAlmostEqual(result["range_pct"],64.25/7710.25*100)
        self.assertAlmostEqual(result["change_pct"],(7710.25/7760.75-1)*100)
        self.assertLess(result["change_pct"],0)
        self.assertEqual(result["ticker"],"ESZ6")

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
