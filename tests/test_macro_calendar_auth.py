"""No-secret public macro calendar regression, without live HTTP calls.

FinanceCalendar OAuth/token support was retired; do NOT reintroduce it.
"""
import unittest
from datetime import date
from unittest.mock import patch

from macro_calendar import _read, load_calendar, BEA_URL


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n):
        return b"<html><body>public calendar</body></html>"


class FreePublicCalendarTests(unittest.TestCase):
    def tearDown(self):
        load_calendar.clear()

    def test_public_html_request_never_requires_key(self):
        with patch("macro_calendar.urlopen", return_value=FakeResponse()) as request:
            html = _read(BEA_URL)
        self.assertIn("public calendar", html)
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, BEA_URL)
        self.assertNotIn("api_key", req.full_url)
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertIn("user-agent", headers)
        self.assertNotIn("x-api-key", headers)
        self.assertNotIn("authorization", headers)

    def test_six_public_sources_can_be_complete_without_secrets(self):
        day = date(2026, 11, 13)
        patches = [
            patch("bls_calendar.fetch_bls", return_value=[]),
            patch("macro_calendar._claims_events", return_value=[]),
            patch("macro_calendar._bea_events", return_value=[]),
            patch("macro_calendar._census_events", return_value=[]),
            patch("macro_calendar._fed_events", return_value=[]),
            patch("macro_calendar._ism_events", return_value=[]),
        ]
        load_calendar.clear()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            events, health = load_calendar(day, day)
        self.assertEqual(events, [])
        self.assertTrue(health["complete"])
        self.assertEqual(health["sources_count"], 6)
        self.assertEqual(health["failed"], {})
        self.assertNotIn("FinanceCalendar", health["healthy"])

    def test_unavailable_sources_are_diagnostic_not_a_false_empty_calendar(self):
        day = date(2026, 11, 13)
        patches = [
            patch("bls_calendar.fetch_bls", side_effect=OSError("offline")),
            patch("macro_calendar._claims_events", side_effect=OSError("offline")),
            patch("macro_calendar._bea_events", side_effect=OSError("offline")),
            patch("macro_calendar._census_events", side_effect=OSError("offline")),
            patch("macro_calendar._fed_events", side_effect=OSError("offline")),
            patch("macro_calendar._ism_events", side_effect=OSError("offline")),
        ]
        load_calendar.clear()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            events, health = load_calendar(day, day)
        self.assertEqual(events, [])
        self.assertFalse(health["complete"])
        self.assertEqual(health["healthy"], [])
        self.assertEqual(len(health["failed"]), 6)


if __name__ == "__main__":
    unittest.main()
