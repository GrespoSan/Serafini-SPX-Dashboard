"""Slot SPX: migrazione 13:30 -> 14:46 SENZA look-ahead o backfill.

Dati mock: non accede a Cboe, Supabase, cron-job o account broker.
"""
import unittest
from datetime import date
from unittest.mock import patch

from acquisition_schedule import (
    CUTOFF_1446, FIRST_1330_OFFICIAL, LAST_1330_OFFICIAL,
    official_slots_for_day, expected_replay_slots, official_export_slots,
    raw_study_1330_for_day,
)
from pine_sources import load_export_profiles


def fake_frame(day, origin="UFFICIALE"):
    from gamma_replay import weekly_expiry
    rows = [{"strike_spx": float(7300 + i * 5),
             "net_gamma": float(i-25)} for i in range(50)]
    return {
        "origin": origin,
        "reference_spx": 7500.,
        "download_rome": f"{day.isoformat()}T14:46:30+01:00",
        "cboe_timestamp_original": f"{day.isoformat()} 13:46:00",
        "profiles": {
            "0DTE": {"expiry": day.isoformat(),
                     "rows": rows, "visual_rows": rows},
            "Weekly": {"expiry": weekly_expiry(day).isoformat(),
                       "rows": rows, "visual_rows": rows},
        },
    }


class SlotMigrationTests(unittest.TestCase):
    def test_official_1330_single_historical_day_only(self):
        self.assertEqual(FIRST_1330_OFFICIAL, date(2026,11,11))
        self.assertEqual(LAST_1330_OFFICIAL, date(2026,11,11))
        self.assertEqual(CUTOFF_1446, date(2026,11,12))
        before = official_slots_for_day(date(2026,11,10))
        historical = official_slots_for_day(date(2026,11,11))
        new = official_slots_for_day(date(2026,11,12))
        self.assertNotIn("13:30", before)
        self.assertNotIn("14:46", historical)
        self.assertEqual(historical["13:30"], "30 13 * * 1-5")
        self.assertNotIn("13:30", new)
        self.assertEqual(new["14:46"], "46 14 * * 1-5")
        for slot in ("07:30", "15:50", "17:00", "20:30"):
            self.assertEqual(before[slot], historical[slot])
            self.assertEqual(historical[slot], new[slot])

    def test_replay_keeps_1330_and_adds_1446_only_post_cutover(self):
        before = expected_replay_slots(date(2026,11,10))
        after = expected_replay_slots(date(2026,11,12))
        self.assertIn("13:30", before)
        self.assertIn("13:30", after)
        self.assertNotIn("14:46", before)
        self.assertIn("14:46", after)
        self.assertTrue(raw_study_1330_for_day(date(2026,11,10)))
        self.assertFalse(raw_study_1330_for_day(date(2026,11,11)))

    def test_five_pines_switch_only_middle_day_slot(self):
        old = official_export_slots(date(2026,11,11))
        new = official_export_slots(date(2026,11,12))
        self.assertEqual(len(old), 5)
        self.assertEqual(len(new), 5)
        self.assertEqual(old[1], "13:30")
        self.assertEqual(new[1], "14:46")
        self.assertEqual(old[0], new[0])
        self.assertEqual(old[2:], new[2:])

    def test_pine_loader_does_not_mix_retired_slot(self):
        day = date(2026,11,12)
        frame = fake_frame(day)
        with patch("pine_sources._study_frames", return_value={"13:30": frame}), \
             patch("pine_sources._official_frames", return_value={
                 "07:30": frame, "13:30": frame, "14:46": frame,
                 "15:50": frame, "17:00": frame, "20:30": frame,
             }):
            results = load_export_profiles(None, "2026-W46", day, [])
        self.assertEqual(set(results), set(official_export_slots(day)))
        self.assertNotIn("13:30", results)
        self.assertEqual(results["14:46"]["origin"], "UFFICIALE")

    def test_pine_loader_keeps_11_nov_historical_official(self):
        day = date(2026,11,11)
        frame = fake_frame(day)
        with patch("pine_sources._study_frames", return_value={"13:30": frame}), \
             patch("pine_sources._official_frames", return_value={
                 "07:30": frame, "13:30": frame, "14:46": frame,
                 "15:50": frame, "17:00": frame, "20:30": frame,
             }):
            results = load_export_profiles(None, "2026-W46", day, [])
        self.assertEqual(set(results), set(official_export_slots(day)))
        self.assertIn("13:30", results)
        self.assertNotIn("14:46", results)

    def test_ai_report_source_labels_are_date_aware(self):
        from ai_report import _acquisition_label
        r = {"acquisition_slot": "30 13 * * 1-5",
             "download_utc": "2026-11-11T12:30:10+00:00"}
        self.assertIn("ufficiale", _acquisition_label(r))
        r["download_utc"] = "2026-11-10T12:30:10+00:00"
        self.assertIn("studio storico", _acquisition_label(r))
        r["acquisition_slot"] = "46 14 * * 1-5"
        r["download_utc"] = "2026-11-12T13:46:10+00:00"
        self.assertIn("14:46", _acquisition_label(r))


if __name__ == "__main__":
    unittest.main()
