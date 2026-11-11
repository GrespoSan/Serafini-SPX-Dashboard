"""Macro Serafini: CPI 10/11/2026 sempre segnalato anche senza FinanceCalendar."""
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from bls_calendar import parse_ics, parse_month_html, fetch_bls
from macro_calendar import (
    _kind, _prepare, _rome_time, merge_calendars, load_calendar,
)
from pine_news_overlay import append_news


NOV10 = date(2026, 11, 10)
BLS_ICS = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20261110T083000
SUMMARY:Consumer Price Index for October 2026
END:VEVENT
BEGIN:VEVENT
DTSTART:20261113T133000Z
SUMMARY:Producer Price Index for October 2026
END:VEVENT
BEGIN:VEVENT
DTSTART:20261106T133000Z
SUMMARY:Employment Situation for October 2026
END:VEVENT
END:VCALENDAR
"""

BLS_HTML = """<html><body><h1>November 2026</h1><table>
<tr><th>Date</th><th>Time</th><th>Release</th></tr>
<tr><td>Tuesday, November 10, 2026</td><td>08:30 AM</td>
<td>Consumer Price Index for October 2026</td></tr>
<tr><td>Tuesday, November 10, 2026</td><td>08:30 AM</td>
<td>Real Earnings for October 2026</td></tr>
<tr><td>Friday, November 13, 2026</td><td>08:30 AM</td>
<td>Producer Price Index for October 2026</td></tr>
</table></body></html>
"""


def cpi():
    return {
        "day": NOV10, "clock": "14:30", "kind": "CPI",
        "priority": "ALTA", "status": "scheduled",
        "provider": "BLS", "official_schedule": True,
        "source": "https://www.bls.gov/schedule/news_release/cpi.htm",
    }


class BlsScheduleTests(unittest.TestCase):
    def test_cpi_10_nov_in_ics_and_local_rome_time(self):
        result = parse_ics(BLS_ICS, NOV10, date(2026,11,14))
        self.assertEqual([(e["kind"], e["clock"]) for e in result],
                         [("CPI", "14:30"), ("PPI", "14:30")])
        self.assertTrue(all(e["provider"] == "BLS" for e in result))

    def test_bls_html_schedule_fallback(self):
        out = parse_month_html(BLS_HTML, NOV10, date(2026,11,13))
        self.assertEqual([(e["kind"],e["day"],e["clock"]) for e in out],
                         [("CPI",NOV10,"14:30"),("PPI",date(2026,11,13),"14:30")])

    def test_march_dst_mismatch_0830_et_is_1330_rome(self):
        ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20270315T083000
SUMMARY:Consumer Price Index
END:VEVENT
END:VCALENDAR"""
        result = parse_ics(ics, date(2027,3,15), date(2027,3,15))
        self.assertEqual(result[0]["clock"], "13:30")

    def test_ics_missing_event_triggers_monthly_html_validation(self):
        with patch("bls_calendar._read_url", side_effect=[
            "BEGIN:VCALENDAR\nEND:VCALENDAR",
            BLS_HTML,
        ]) as req:
            out = fetch_bls.__wrapped__(NOV10, NOV10)
        self.assertEqual(out[0]["kind"], "CPI")
        self.assertEqual(req.call_count, 2)

    def test_ics_failure_falls_back_to_monthly_table(self):
        with patch("bls_calendar._read_url", side_effect=[
            OSError("unavailable"), BLS_HTML
        ]) as req:
            out = fetch_bls.__wrapped__(NOV10, NOV10)
        self.assertEqual(out[0]["clock"], "14:30")
        self.assertEqual(req.call_count, 2)


class DualSourceTests(unittest.TestCase):
    def test_fc_v2_release_at_utc_is_primary(self):
        event = {
            "series_code": "us-cpi", "country": "US", "date": "2026-11-10",
            "release_at_utc": "2026-11-10T13:30:00+00:00",
            "time_utc": "2026-11-10T15:30:00+00:00",
            "status": "scheduled",
        }
        prepared = _prepare([event], NOV10)
        self.assertEqual(prepared[0]["clock"], "14:30")
        self.assertEqual(prepared[0]["kind"], "CPI")

    def test_cpi_present_when_financecalendar_returns_empty(self):
        with patch("macro_calendar._fetch", return_value=[]), \
             patch("macro_calendar.st.secrets", {"FINANCE_CALENDAR_API_KEY":"test"}), \
             patch("bls_calendar.fetch_bls", return_value=[cpi()]):
            events, health = load_calendar(NOV10, NOV10)
        self.assertEqual([(e["kind"],e["clock"]) for e in events],
                         [("CPI","14:30")])
        self.assertTrue(health["bls_ok"])
        self.assertTrue(health["financecalendar_ok"])
        self.assertTrue(any("assente" in msg for msg in health["messages"]))

    def test_cpi_still_visible_without_financecalendar_api_key(self):
        with patch("macro_calendar.st.secrets", {}), \
             patch("bls_calendar.fetch_bls", return_value=[cpi()]):
            events, health = load_calendar(NOV10, NOV10)
        self.assertEqual(events[0]["kind"], "CPI")
        self.assertFalse(health["financecalendar_ok"])
        self.assertTrue(health["bls_ok"])

    def test_bls_time_wins_while_showing_disagreement(self):
        fc = [{
            **cpi(), "clock": "15:30", "source": "https://www.financecalendar.com",
            "provider": "FinanceCalendar", "official_schedule": False,
        }]
        merged, warnings = merge_calendars(fc, [cpi()],
                                          finance_ok=True, bls_ok=True)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["clock"], "14:30")
        self.assertIn("Orario discordante", merged[0]["source_warning"])
        self.assertTrue(warnings)

    def test_blssource_outage_does_not_hide_fc_event(self):
        fc = _prepare([{
            "series_code": "us-cpi", "date": "2026-11-10",
            "release_at_utc": "2026-11-10T13:30:00+00:00",
            "country": "US",
        }], NOV10)
        with patch("macro_calendar._fetch", return_value=[{
            "series_code": "us-cpi", "date": "2026-11-10",
            "release_at_utc": "2026-11-10T13:30:00+00:00",
            "country": "US",
        }]), \
             patch("macro_calendar.st.secrets", {"FINANCE_CALENDAR_API_KEY":"x"}), \
             patch("bls_calendar.fetch_bls", side_effect=OSError("BLS unavailable")):
            events, status = load_calendar(NOV10, NOV10)
        self.assertEqual(events[0]["clock"], "14:30")
        self.assertFalse(status["bls_ok"])

    def test_both_unavailable_are_not_misreported_as_zero_events(self):
        with patch("macro_calendar.st.secrets", {}), \
             patch("bls_calendar.fetch_bls", side_effect=OSError("BLS unavailable")):
            with self.assertRaisesRegex(RuntimeError, "Entrambe"):
                load_calendar(NOV10, NOV10)

    def test_macro_ui_keeps_bls_link_no_yesterday_and_no_yellow_fc_warning(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "macro_calendar.py").read_text(encoding="utf-8")
        self.assertIn("Calendario ufficiale BLS</a>", source)
        self.assertNotIn('with st.expander(f"Ieri', source)
        self.assertNotIn('st.warning("Calendario parziale:', source)
        self.assertIn('st.caption("FinanceCalendar temporaneamente non disponibile', source)
        self.assertIn('elif not health["financecalendar_ok"]:', source)

    def test_pine_marks_cpi_only_before_verified_download(self):
        events=[cpi()]
        before = append_news(
            "//@version=6\nindicator('test',overlay=true)",
            events, captured_at="2026-11-10T07:30:10+01:00")
        after = append_news(
            "//@version=6\nindicator('test',overlay=true)",
            events, captured_at="2026-11-10T15:50:10+01:00")
        self.assertIn("newsTime0", before)
        # Etichetta TradingView intenzionalmente verticale C / P / I.
        self.assertIn('timestamp("Europe/Rome", 2026, 11, 10, 14, 30)', before)
        self.assertIn("newsLabel0", before)
        self.assertNotIn("newsTime0", after)


if __name__ == "__main__":
    unittest.main()
