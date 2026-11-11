"""Pine macro verticals: future-only relative to each Cboe snapshot.

No real HTTP, credentials, or forward-looking Gamma.  All hours are
Europe/Rome, including the US/EU daylight-saving transition windows.
"""
from datetime import date
from pathlib import Path
import ast
import unittest

from pine_export import make_pine
from pine_compare import make_pine_comparison
from pine_news_overlay import append_news, future_news_only

ROOT = Path(__file__).resolve().parents[1]
DAY = date(2026, 11, 5)


def news(clock, kind="CPI", day=DAY, priority="ALTA"):
    return {"day": day, "clock": clock, "kind": kind, "priority": priority}


def profiles():
    p0 = {
        "expiry": DAY.isoformat(),
        "rows": [
            {"strike_spx": 7650.0 + i * 5,
             "net_gamma": (1 if i % 2 else -1) * (i + 1) * .1}
            for i in range(50)
        ],
    }
    weekly = {
        "expiry": "2026-11-06",
        "rows": [
            {"strike_spx": 7650.0 + i * 5,
             "net_gamma": (1 if i % 2 else -1) * (i + 1) * .15}
            for i in range(50)
        ],
    }
    return p0, weekly


class MacroNewsPineTests(unittest.TestCase):
    def test_0730_includes_upcoming_1440_and_1550_excludes_it(self):
        events = [
            news("06:45", "PPI"),
            news("07:30", "ISM"),
            news("14:40", "CPI"),
            news("15:50", "NFP"),
            news("16:00", "FOMC"),
            news("19:00", "FED"),
        ]
        morning = future_news_only(events, "2026-11-05T07:31:05+01:00")
        self.assertEqual([x["clock"] for x in morning],
                         ["14:40", "15:50", "16:00", "19:00"])
        afternoon = future_news_only(events, "2026-11-05T15:50:30+01:00")
        self.assertEqual([x["clock"] for x in afternoon],["16:00", "19:00"])
        late = future_news_only(events, "2026-11-05T20:30:00+01:00")
        self.assertFalse(late)

    def test_real_pine_visual_and_toggle_with_only_future_events(self):
        odte, weekly = profiles()
        base = make_pine(odte, weekly, 105.0, DAY.isoformat(),
                         "2026-11-05 06:30:00", "Mattina 07:30")
        with_news = append_news(
            base, [news("14:40","CPI"), news("16:00","FOMC")],
            captured_at="2026-11-05T07:31:00+01:00")
        self.assertTrue(with_news.startswith(base))
        self.assertIn('showMacroNews = input.bool(true, "News macro · linee verticali")',
                      with_news)
        self.assertIn('if showMacroNews and validTicker and timeframe.isintraday',
                      with_news)
        self.assertEqual(with_news.count('line.new(newsTime'), 2)
        self.assertIn('timestamp("Europe/Rome", 2026, 11, 5, 14, 40)',
                      with_news)
        self.assertIn('timestamp("Europe/Rome", 2026, 11, 5, 16, 0)',
                      with_news)
        self.assertIn('style=line.style_dashed, width=1',with_news)
        self.assertIn('xloc=xloc.bar_time, extend=extend.both',with_news)
        self.assertIn('var array<line> macroNewsLines',with_news)
        self.assertIn('line.delete(ln)',with_news)
        self.assertIn('array.clear(macroNewsLines)',with_news)

        updated = append_news(
            base, [news("14:40","CPI"), news("16:00","FOMC")],
            captured_at="2026-11-05T15:50:00+01:00")
        self.assertNotIn('2026, 11, 5, 14, 40',updated)
        self.assertIn('2026, 11, 5, 16, 0',updated)
        self.assertEqual(updated.count('line.new(newsTime'),1)
        no_more_news = append_news(
            base,[news("14:40")],captured_at="2026-11-05T15:50:00+01:00")
        self.assertEqual(no_more_news,base)

    def test_current_comparison_1550_not_previous_0730_controls_news(self):
        odte, weekly = profiles()
        prev = {**odte, "rows":[
            dict(row, net_gamma=row["net_gamma"]*.8)
            for row in odte["rows"]]}
        original = make_pine_comparison(
            prev, odte, weekly, 105.0, DAY.isoformat(),
            "07:30", "15:50", "2026-11-05 14:49:00")
        result = append_news(
            original, [news("14:40","PCE"),news("16:10","FOMC")],
            captured_at="2026-11-05T15:50:10+01:00")
        self.assertNotIn('2026, 11, 5, 14, 40',result)
        self.assertIn('2026, 11, 5, 16, 10',result)
        self.assertIn("showPrevious",result)
        self.assertIn('showMacroNews = input.bool(true',result)
        self.assertEqual(result.count("line.new(newsTime"),1)

    def test_same_minute_news_collapse_and_nonblocking_invalid_clock(self):
        events = [
            news("16:00", "FOMC"),
            news("16:00", "Conferenza stampa Fed"),
            news("16:15", "Consumer Confidence",priority="SECONDARIA"),
        ]
        pine = "//@version=6\nvalidTicker=true\n"
        out = append_news(pine,events,captured_at="2026-11-05T15:50:30+01:00")
        self.assertEqual(out.count("line.new(newsTime"),2)
        self.assertIn("color=color.red",out)
        self.assertIn("color=color.orange",out)
        for bad in (None, "", "2026-11-05T15:50:00", "not a timestamp"):
            self.assertEqual(append_news(pine,events,captured_at=bad),pine)
        self.assertEqual(append_news(pine,[],captured_at="2026-11-05T15:50:00+01:00"),pine)

    def test_rome_dst_and_date_guard(self):
        # 30 Oct: Rome CET, New York still EDT. Offset is NOT fixed.
        events = [
            news("14:40", day=date(2026,10,30)),
            news("15:10", day=date(2026,10,30)),
            news("15:55",day=date(2026,11,2)),
        ]
        result = future_news_only(events,"2026-10-30T13:50:00Z")
        self.assertEqual([x["clock"] for x in result],["15:10"])
        result2=future_news_only(events,"2026-11-02T14:50:00Z")
        self.assertEqual([x["clock"] for x in result2],["15:55"])

    def test_github_export_flow_uses_dual_source_and_verified_capture(self):
        main=(ROOT/"gamma_section.py").read_text(encoding="utf-8")
        src=(ROOT/"pine_sources.py").read_text(encoding="utf-8")
        overlay=(ROOT/"pine_news_overlay.py").read_text(encoding="utf-8")
        for n,s in (("gamma_section.py",main),("pine_sources.py",src),
                    ("pine_news_overlay.py",overlay)):
            ast.parse(s,filename=n)
        self.assertIn('from macro_calendar import load_calendar',main)
        self.assertIn('macro_events, health = load_calendar(day, day)',main)
        self.assertNotIn('_fetch(day.isoformat(), day.isoformat(), finance_key)',main)
        self.assertIn('captured_at=(sources.get(slot) or {}).get("download_rome")',main)
        self.assertIn('"download_rome": frame.get("download_rome")',src)
        self.assertIn('if event_dt.astimezone(timezone.utc) > cutoff:',overlay)
        self.assertIn('showMacroNews = input.bool(true',overlay)
        self.assertNotIn('_fetch(day.isoformat(), day.isoformat())',main)


if __name__=="__main__":
    unittest.main()
