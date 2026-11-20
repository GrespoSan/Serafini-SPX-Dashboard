"""SPX official 403 warnings must be grounded in actual today's logs."""
import json
import unittest
from datetime import date

from cboe_source_health import blocked_acquisition_today

DAY = date(2026, 11, 20)
WEEK = "2026-W47"


class Dummy:
    def __init__(self, records):
        self.records = records

    def download(self, path):
        return json.dumps(self.records[path.rsplit("/", 1)[-1]]).encode("utf-8")


class Tests(unittest.TestCase):
    def test_last_failed_403_is_reported(self):
        name = "20261120T063046450647Z_mattina_primario.json"
        record = {"rome_date": DAY.isoformat(), "status": "ERRORE",
                  "error": "HTTPError: HTTP Error 403: Forbidden",
                  "scheduled_slot": "30 7 * * 1-5",
                  "download_rome": "2026-11-20T07:30:46+01:00"}
        value = blocked_acquisition_today(WEEK, [name], Dummy({name: record}), DAY)
        self.assertEqual(value["slot"], record["scheduled_slot"])
        self.assertIn("07:30", value["download_rome"])

    def test_success_after_403_clears_warning(self):
        first = "20261120T063046450647Z_mattina_primario.json"
        later = "20261120T064500000001Z_mattina_recupero.json"
        records = {
            first: {"rome_date": DAY.isoformat(), "status": "ERRORE",
                    "error": "HTTPError: HTTP Error 403: Forbidden"},
            later: {"rome_date": DAY.isoformat(), "status": "NUOVO"},
        }
        self.assertIsNone(blocked_acquisition_today(
            WEEK, [first, later], Dummy(records), DAY))

    def test_stale_failure_and_other_errors_not_reclassified(self):
        name = "20261120T063046450647Z_mattina_primario.json"
        records = {name: {"rome_date": DAY.isoformat(), "status": "ERRORE",
                          "error": "Supabase HTTP 403: permission denied"}}
        self.assertIsNone(blocked_acquisition_today(
            WEEK, [name], Dummy(records), DAY))
        self.assertIsNone(blocked_acquisition_today(
            "2026-W46", [name], Dummy(records), DAY))
        self.assertIsNone(blocked_acquisition_today(
            WEEK, [name], Dummy(records), date(2026, 11, 19)))


if __name__ == "__main__":
    unittest.main()
