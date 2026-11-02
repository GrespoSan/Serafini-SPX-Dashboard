"""SPX FinanceCalendar authentication regression tests (no real keys or HTTP)."""
import json
import unittest
from unittest.mock import patch

from macro_calendar import _fetch, API


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, n):
        return json.dumps({
            "events": [{
                "date": "2026-11-06", "series_code": "us-nfp",
                "country": "US", "time_utc": "2026-11-06T13:30:00Z",
                "status": "scheduled"
            }],
            "total": 1
        }).encode()


class FinanceCalendarKeyTests(unittest.TestCase):
    def test_authenticated_request_uses_secret_header_not_url(self):
        _fetch.clear()
        with patch("macro_calendar.urlopen", return_value=FakeResponse()) as mock_open:
            rows = _fetch("2026-11-02", "2026-11-09", "test-ONLY-private")
        self.assertEqual(len(rows), 1)
        request = mock_open.call_args.args[0]
        self.assertTrue(request.full_url.startswith(API + "?"))
        self.assertNotIn("test-ONLY-private", request.full_url)
        headers = {k.lower(): v for k,v in request.header_items()}
        self.assertEqual(headers["x-api-key"], "test-ONLY-private")

    def test_missing_key_cannot_issue_unauthenticated_request(self):
        _fetch.clear()
        with patch("macro_calendar.urlopen") as mock_open:
            with self.assertRaisesRegex(ValueError, "Chiave FinanceCalendar mancante"):
                _fetch("2026-11-02", "2026-11-09", "")
            mock_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
