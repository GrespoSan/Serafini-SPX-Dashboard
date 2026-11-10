"""Tests for ES forward outcome lab (synthetic 5m bars)."""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from research_gamma_es_outcomes import forward_outcomes

ROME = ZoneInfo("Europe/Rome")


class ForwardOutcomeTests(unittest.TestCase):
    def setUp(self):
        start = datetime(2026, 11, 10, 15, 0, tzinfo=ROME)
        self.frame = pd.DataFrame([
            {"bar_close": start + timedelta(minutes=5*i), "close": 7500.0 + i}
            for i in range(20)
        ])

    def test_embargo_and_horizons(self):
        result = forward_outcomes(self.frame, "2026-11-10T15:00:00+01:00")
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["reference_close"], 7504.0)
        self.assertEqual(result["es_15m_pts"], 3.0)
        self.assertEqual(result["es_30m_pts"], 6.0)
        self.assertEqual(result["es_60m_pts"], 12.0)

    def test_missing_target_is_not_interpolated(self):
        frame = self.frame.loc[self.frame["bar_close"] !=
                               datetime(2026, 11, 10, 15, 35, tzinfo=ROME)]
        result = forward_outcomes(frame, "2026-11-10T15:00:00+01:00")
        self.assertIsNone(result["es_15m_pts"])

    def test_missing_reference_is_nd(self):
        result = forward_outcomes(self.frame,
                                  "2026-11-10T16:00:00+01:00")
        self.assertEqual(result["status"], "N/D")

    def test_timezone_required(self):
        with self.assertRaises(ValueError):
            forward_outcomes(self.frame, "2026-11-10T15:00:00")


if __name__ == "__main__":
    unittest.main()
