"""Macro Serafini: CPI BLS and other public sources without private API keys."""
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from bls_calendar import parse_ics, parse_month_html, fetch_bls
from macro_calendar import load_calendar, _event
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


class PublicSourceTests(unittest.TestCase):
    def tearDown(self):
        load_calendar.clear()

    def test_cpi_remains_available_if_other_sources_fail(self):
        patches = (
            patch("bls_calendar.fetch_bls", return_value=[cpi()]),
            patch("macro_calendar._claims_events", side_effect=OSError("offline")),
            patch("macro_calendar._bea_events", side_effect=OSError("offline")),
            patch("macro_calendar._census_events", side_effect=OSError("offline")),
            patch("macro_calendar._fed_events", side_effect=OSError("offline")),
            patch("macro_calendar._ism_events", side_effect=OSError("offline")),
        )
        load_calendar.clear()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            events, health = load_calendar(NOV10, NOV10)
        self.assertEqual([(e["kind"], e["clock"]) for e in events],
                         [("CPI", "14:30")])
        self.assertIn("BLS", health["healthy"])
        self.assertNotIn("BLS", health["failed"])
        self.assertFalse(health["complete"])
        self.assertEqual(len(health["failed"]), 5)

    def test_all_sources_unavailable_are_not_zero_events_verified(self):
        patches = (
            patch("bls_calendar.fetch_bls", side_effect=OSError("unavailable")),
            patch("macro_calendar._claims_events", side_effect=OSError("offline")),
            patch("macro_calendar._bea_events", side_effect=OSError("offline")),
            patch("macro_calendar._census_events", side_effect=OSError("offline")),
            patch("macro_calendar._fed_events", side_effect=OSError("offline")),
            patch("macro_calendar._ism_events", side_effect=OSError("offline")),
        )
        load_calendar.clear()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            events, health = load_calendar(NOV10, NOV10)
        self.assertEqual(events, [])
        self.assertEqual(health["healthy"], [])
        self.assertFalse(health["complete"])
        self.assertEqual(len(health["failed"]), 6)

    def test_duplicates_preserve_first_verified_public_source(self):
        bea_duplicate = {**cpi(), "provider": "BEA"}
        patches = (
            patch("bls_calendar.fetch_bls", return_value=[cpi()]),
            patch("macro_calendar._claims_events", return_value=[]),
            patch("macro_calendar._bea_events", return_value=[bea_duplicate]),
            patch("macro_calendar._census_events", return_value=[]),
            patch("macro_calendar._fed_events", return_value=[]),
            patch("macro_calendar._ism_events", return_value=[]),
        )
        load_calendar.clear()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            events, health = load_calendar(NOV10, NOV10)
        self.assertTrue(health["complete"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["provider"], "BLS")

    def test_public_calendar_chicago_new_york_rome_dst_conversion(self):
        self.assertEqual(
            _event(date(2026, 11, 10), 8, 30, "CPI", "BLS",
                   "https://www.bls.gov/")["clock"], "14:30")
        self.assertEqual(
            _event(date(2027, 3, 15), 8, 30, "CPI", "BLS",
                   "https://www.bls.gov/")["clock"], "13:30")

    def test_macro_ui_distinguishes_partial_and_unavailable(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] /
                  "macro_calendar.py").read_text(encoding="utf-8")
        self.assertIn('st.warning("Calendario PARZIALE: "', source)
        self.assertIn('st.error("Fonti macro non raggiungibili', source)
        self.assertIn("BLS_URL", source)
        self.assertIn("SOURCES", source)
        self.assertNotIn("FINANCE_CALENDAR_API_KEY", source)

    def test_pine_marks_cpi_only_before_verified_download(self):
        events = [cpi()]
        before = append_news(
            "//@version=6\\nindicator('test',overlay=true)",
            events, captured_at="2026-11-10T07:30:10+01:00")
        after = append_news(
            "//@version=6\\nindicator('test',overlay=true)",
            events, captured_at="2026-11-10T15:50:10+01:00")
        self.assertIn("newsTime0", before)
        self.assertIn('timestamp("Europe/Rome", 2026, 11, 10, 14, 30)', before)
        self.assertIn("newsLabel0", before)
        self.assertNotIn("newsTime0", after)


if __name__ == "__main__":
    unittest.main()
