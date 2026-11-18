"""SPX Trend Daily and Range card must use the same Massive ES OHLC.

Synthetic fixtures only, no API calls or credentials.
"""
import unittest
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from massive_futures_daily import parse_session, previous_daily_context
from trend_daily_section import (massive_frame, evaluate, impulse_reference,
                                 previous_week_range, weekly_exploration,
                                 weekly_exploration_html, parse_weekly_intraday)
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

    def test_weekly_levels_are_previous_finished_week_from_massive_es(self):
        # Anchor Mon 16 Nov: previous week is Mon 09 to Fri 13.
        previous = [
            session("2026-11-09", 7530., 7620., 7480., 7560.),
            session("2026-11-10", 7560., 7660., 7540., 7650.),
            session("2026-11-11", 7650., 7690., 7580., 7590.),
            session("2026-11-12", 7590., 7630., 7470., 7490.),
            session("2026-11-13", 7490., 7620., 7460., 7560.75),
        ]
        frame = massive_frame(previous, date(2026,11,16), contract="ESZ6")
        weekly = previous_week_range(frame, date(2026,11,16))
        self.assertEqual(weekly["week_start"], date(2026,11,9))
        self.assertEqual(weekly["week_end"], date(2026,11,13))
        self.assertEqual(weekly["sessions"], 5)
        self.assertEqual(weekly["low"], 7460.)
        self.assertEqual(weekly["high"], 7690.)
        self.assertEqual(weekly["close"], 7560.75)
        self.assertEqual(weekly["close_day"], date(2026,11,13))
        self.assertAlmostEqual(weekly["from_low_pct"],
                               100 * (7560.75 / 7460. - 1))
        self.assertAlmostEqual(weekly["from_high_pct"],
                               100 * (7560.75 / 7690. - 1))

    def test_previous_week_does_not_include_current_week_or_future_candles(self):
        previous = [
            session("2026-11-09", 7530., 7620., 7480., 7560.),
            session("2026-11-10", 7560., 7660., 7540., 7650.),
            session("2026-11-11", 7650., 7690., 7580., 7590.),
            session("2026-11-12", 7590., 7630., 7470., 7490.),
            session("2026-11-13", 7490., 7620., 7460., 7560.75),
        ]
        rows = previous + [
            session("2026-11-16", 7560., 7800., 7400., 7750.),
            session("2026-11-17", 7750., 7900., 7390., 7880.),
        ]
        monday = massive_frame(rows, date(2026,11,16), contract="ESZ6")
        self.assertEqual(previous_week_range(
            monday,date(2026,11,16))["close"], 7560.75)
        tuesday = massive_frame(rows, date(2026,11,17), contract="ESZ6")
        reference = previous_week_range(tuesday, date(2026,11,17))
        self.assertEqual(reference["low"], 7460.)
        self.assertEqual(reference["high"], 7690.)
        self.assertEqual(reference["close"], 7750.)
        self.assertAlmostEqual(reference["from_high_pct"],
                               100 * (7750./7690. - 1))

    def test_incomplete_previous_week_is_nd_not_an_estimate(self):
        sample = [
            session("2026-11-04", 7500., 7600., 7480., 7550.),
            session("2026-11-05", 7550., 7620., 7500., 7600.),
            session("2026-11-06", 7600., 7650., 7550., 7610.),
            session("2026-11-12", 7610., 7670., 7540., 7650.),
            session("2026-11-13", 7650., 7690., 7480., 7560.),
        ]
        frame = massive_frame(sample, date(2026,11,16), contract="ESZ6")
        self.assertIsNone(previous_week_range(frame, date(2026,11,16)))

    def test_weekly_exploration_partial_and_both_extremes(self):
        previous = [
            session("2026-11-09", 7530., 7620., 7480., 7560.),
            session("2026-11-10", 7560., 7660., 7540., 7650.),
            session("2026-11-11", 7650., 7690., 7580., 7590.),
            session("2026-11-12", 7590., 7630., 7470., 7490.),
            session("2026-11-13", 7490., 7620., 7460., 7560.75),
        ]
        now = date(2026, 11, 18)
        only_previous = massive_frame(previous, now, contract="ESZ6")
        week = previous_week_range(only_previous, now)
        self.assertIsNone(weekly_exploration(only_previous, now, week))
        current = previous + [
            session("2026-11-16", 7560., 7600., 7500., 7530.),
            session("2026-11-17", 7530., 7610., 7460., 7580.),
            session("2026-11-18", 7580., 7800., 7300., 7650.),
        ]
        frame = massive_frame(current, now, contract="ESZ6")
        progress = weekly_exploration(frame, now, previous_week_range(frame, now))
        self.assertEqual(progress["open"], 7560.)
        self.assertEqual(progress["high"], 7610.)
        self.assertEqual(progress["low"], 7460.)
        self.assertFalse(progress["upper_touched"])
        self.assertTrue(progress["lower_touched"])
        self.assertEqual(progress["last_day"], date(2026, 11, 17))
        html = weekly_exploration_html(progress)
        self.assertIn("spx-weekly-fill", html)
        self.assertIn("17/11/2026", html)
        self.assertIn("Toccato", html)
        self.assertNotIn("7800", html)
        after = massive_frame(current, date(2026, 11, 19), contract="ESZ6")
        progress2 = weekly_exploration(after, date(2026, 11, 19),
                                       previous_week_range(after, date(2026, 11, 19)))
        self.assertTrue(progress2["upper_touched"])
        self.assertTrue(progress2["lower_touched"])

    def test_intraday_weekly_uses_only_closed_es_five_minute_bars(self):
        chicago = ZoneInfo("America/Chicago")
        opening = datetime(2026, 11, 15, 17, tzinfo=chicago)
        monday = opening + timedelta(hours=3)
        future = opening + timedelta(days=3)
        def agg(when, op, hi, lo, close):
            return {
                "window_start": int(when.timestamp() * 1_000_000_000),
                "ticker": "ESZ6", "open": op,
                "high": hi, "low": lo, "close": close,
            }
        payload = {
            "status": "DELAYED",
            "results": [
                agg(opening, 7577.25, 7580., 7570., 7576.),
                agg(monday, 7576., 7610., 7380., 7500.),
                agg(future, 7500., 8000., 7200., 7700.),
            ],
        }
        now = datetime(2026, 11, 16, 5, 30, tzinfo=timezone.utc)
        got = parse_weekly_intraday(payload, "ESZ6", date(2026, 11, 16), now)
        self.assertEqual(got["open"], 7577.25)
        self.assertEqual(got["high"], 7610.)
        self.assertEqual(got["low"], 7380.)
        self.assertEqual(got["current_price"], 7500.)
        self.assertEqual(got["provider_status"], "DELAYED")
        self.assertNotEqual(got["high"], 8000.)
        payload["next_url"] = "https://api.massive.com/futures/v1/aggs/ESZ6?cursor=abc"
        with self.assertRaisesRegex(ValueError, "incompleto"):
            parse_weekly_intraday(payload, "ESZ6", date(2026, 11, 16), now)

    def test_weekly_price_line_and_outside_label(self):
        progress = {
            "previous_high": 7600., "previous_low": 7400.,
            "high": 7700., "low": 7350., "open": 7500.,
            "current_price": 7700.,
            "upper_touched": True, "lower_touched": True,
            "asof": datetime(2026, 11, 18, 15, tzinfo=timezone.utc),
            "source": "Massive ES · 5m chiusi",
        }
        html = weekly_exploration_html(progress)
        self.assertIn('class="spx-weekly-current"', html)
        self.assertIn('Sopra massimo precedente', html)
        self.assertIn("Massive ES · 5m chiusi", html)
        self.assertIn("18/11/2026", html)

    def test_spx_preserves_vx_buffer_and_reuses_trend_bars(self):
        app=(ROOT/"app.py").read_text()
        vol=(ROOT/"vix_regime.py").read_text()
        trend=(ROOT/"trend_daily_section.py").read_text()
        daily=(ROOT/"daily_volatility.py").read_text()
        # Il contesto operativo usa la data corrente e resta autonomo da Supabase.
        self.assertIn("trend_day = datetime.now(ZoneInfo('Europe/Rome')).date()", app)
        self.assertIn("render_vix_context(trend_day, return_context=True)", app)
        self.assertNotIn("context_day", app)
        self.assertIn("snaps, logs = [], []", app)
        self.assertIn("daily_bars=es_daily_context.get('daily_bars')",app)
        self.assertIn('return ctx if return_context else pct',vol)
        self.assertIn("combined_breakout_buffer",vol)
        self.assertIn('context["daily_bars"] = bars',daily)
        self.assertIn("history_days=75",daily)
        self.assertIn("Chiusura vs baricentro",trend)
        self.assertNotIn('class="spx-trend-label">Baricentro Daily</div>',trend)
        self.assertIn("Baricentro impulso {pd.Timestamp(impulse['day'])",trend)
        self.assertIn("spx-trend-impulse",trend)
        self.assertNotIn(">Variazione del range</div>",trend)
        self.assertNotIn(">Range</div>",trend)
        self.assertNotIn("yf.download",trend)


if __name__=="__main__":
    unittest.main()
