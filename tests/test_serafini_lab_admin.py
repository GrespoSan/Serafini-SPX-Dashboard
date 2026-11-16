import base64
import io
import json
import unittest
from datetime import date
from urllib.error import HTTPError

from serafini_lab_admin import (
    fetch_private_report, report_path, require_admin, _validate_report,
)


class Response:
    def __init__(self, payload):
        self.data = io.BytesIO(payload)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, max_bytes):
        return self.data.read(max_bytes)


class SerafiniLabAdminTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 11, 16)
        self.report = {
            "schema": "serafini_lab_daily_v1", "date": self.day.isoformat(),
            "status": "DIAGNOSTIC_ONLY", "score": {
                "status": "HISTORICAL_SCORE_NOT_EDGE", "scored": 40,
                "models": {key: {"mae_gamma": .001, "weighted_mae_gamma": .002}
                           for key in ("H0_carry", "H1_color_only", "H2_nonlinear_decay")}}}

    def test_no_io_for_friend(self):
        def never(*args, **kwargs):
            raise AssertionError("No network call for friend")
        with self.assertRaises(PermissionError):
            fetch_private_report(self.day, "secret", "friend", opener=never)
        with self.assertRaises(PermissionError):
            require_admin(None)

    def test_absent_token_is_explicit(self):
        result, status = fetch_private_report(self.day, "", "admin")
        self.assertIsNone(result)
        self.assertEqual(status, "NON_COLLEGATO")

    def test_server_private_data_shape(self):
        payload = {"type": "file", "encoding": "base64",
                   "content": base64.b64encode(json.dumps(self.report).encode()).decode()}
        calls=[]
        def opener(request, timeout):
            calls.append(request)
            return Response(json.dumps(payload).encode())
        result, status = fetch_private_report(self.day, "my-server-only-token", "admin", opener)
        self.assertEqual(status, "DISPONIBILE")
        self.assertEqual(result["score"]["scored"], 40)
        self.assertIn("/Serafini_LAB_20261116.json?ref=main", calls[0].full_url)
        self.assertNotIn("my-server-only-token", calls[0].full_url)
        self.assertIn("Bearer my-server-only-token", calls[0].get_header("Authorization"))

    def test_incorrect_day_rejected(self):
        with self.assertRaises(ValueError):
            _validate_report(dict(self.report, date="2026-11-15"), self.day)

    def test_404_means_absent_not_success(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 404, "Not Found", None, None)
        result, status = fetch_private_report(self.day, "secret", "admin", opener)
        self.assertIsNone(result)
        self.assertEqual(status, "NESSUN_REPORT")

    def test_fixed_report_path(self):
        self.assertEqual(report_path(self.day),
                         "research/higher_greeks_reports/daily/2026/Serafini_LAB_20261116.json")


if __name__ == "__main__":
    unittest.main()
