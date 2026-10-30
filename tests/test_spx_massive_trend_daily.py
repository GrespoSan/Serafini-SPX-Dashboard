"""SPX Trend Daily and Range card must use the same Massive ES OHLC.

Synthetic fixtures only, no API calls or credentials.
"""
import unittest
from datetime import date
from pathlib import Path
from massive_futures_daily import parse_session, previous_daily_context
from trend_daily_section import massive_frame, evaluate, impulse_reference
ROOT=Path(__file__).resolve().parent.parent


def session(day, op, hi, lo, close):
    return parse_session({
        "ticker":"ESZ6","session_end_date":day,
        "open":op,"high":hi,"low":lo,"close":close,
        "settlement_price": 999999.0,
    }, "ESZ6")


class SPXDailyTrendTests(unittest.TestCase):
    @staticmethod
    def sample():
        return [
            session("2026-10-23", 7730., 7799., 7710., 7770.),
            session("2026-10-26", 7770., 7810., 7740., 7800.),
            session("2026-10-27", 7800., 7845., 7760., 7790.),
            session("2026-10-28", 7800., 7819.5, 7750.25, 7760.75),
            session("2026-10-29", 7760., 7773.75, 7709.50, 7710.25),
        ]

    def test_same_close_high_low_and_range(self):
        bars=self.sample()
        daily=previous_daily_context(bars[-2:])
        frame=massive_frame(bars,date(2026,10,30),contract="ESZ6")
        t=evaluate(frame)
        self.assertAlmostEqual(daily["range_points"],64.25)
        self.assertAlmostEqual(t["range_pct"],daily["range_pct"],places=5)
        self.assertAlmostEqual(t["last_close"],7710.25)
        self.assertAlmostEqual(t["midpoint"],(7773.75+7709.50)/2)
        self.assertAlmostEqual(t["midpoint_delta_pct"],
                               (7710.25/t["midpoint"]-1)*100,places=6)
        self.assertEqual(t["midpoint_state"],"SOTTO")
        self.assertEqual(t["direction"],"RIBASSISTA")
        self.assertEqual(t["bar_color"],"ROSSA")

    def test_no_snapshot_future_candle_lookahead(self):
        rows=self.sample()+[session("2026-10-30", 7710.,7810.,7600.,7700.)]
        frame=massive_frame(rows,date(2026,10,30),contract="ESZ6")
        self.assertEqual(len(frame),5)
        self.assertAlmostEqual(frame.iloc[-1]["Close"],7710.25)

    def test_no_invented_open_or_cross_contract(self):
        rows=self.sample()
        with self.assertRaisesRegex(ValueError,"manca Open"):
            massive_frame([{**r,"open":None} if i==4 else r
                           for i,r in enumerate(rows)],
                          date(2026,10,30),contract="ESZ6")
        with self.assertRaisesRegex(ValueError,"contratti Massive"):
            massive_frame([{**r,"ticker":"NQZ6"} if i==4 else r
                           for i,r in enumerate(rows)],
                          date(2026,10,30),contract="ESZ6")

    def test_spx_preserves_vx_buffer_and_reuses_trend_bars(self):
        app=(ROOT/"app.py").read_text()
        vol=(ROOT/"vix_regime.py").read_text()
        trend=(ROOT/"trend_daily_section.py").read_text()
        daily=(ROOT/"daily_volatility.py").read_text()
        self.assertIn("render_vix_context(context_day, return_context=True)",app)
        self.assertIn("daily_bars=es_daily_context.get('daily_bars')",app)
        self.assertIn('return ctx if return_context else pct',vol)
        self.assertIn("combined_breakout_buffer",vol)
        self.assertIn('context["daily_bars"] = bars',daily)
        self.assertIn("history_days=75",daily)
        self.assertIn("Chiusura vs baricentro",trend)
        self.assertIn("Baricentro Daily",trend)
        self.assertNotIn(">Variazione del range</div>",trend)
        self.assertNotIn(">Range</div>",trend)
        self.assertNotIn("yf.download",trend)


if __name__=="__main__":
    unittest.main()
