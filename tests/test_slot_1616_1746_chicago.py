"""Prospective SPX official 09:16/10:46 America/Chicago.

Historical archives, same-minute Replay sidecars and frozen GEX sources
remain independent. Synthetic tests; NO Cboe, Supabase or broker requests.
"""
import unittest
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from acquisition_schedule import (
    official_slots_for_day, official_export_slots, expected_replay_slots,
)
from gamma_replay import merge_replay_sources
from market_map_pine_autogen import slots_for_day as auto_slots


class NewChicagoOfficialSlotsTests(unittest.TestCase):
    def test_date_scoped_official_ids_and_five_pine_exports(self):
        before = date(2026, 11, 13)
        day = date(2026, 11, 16)
        old = official_slots_for_day(before)
        new = official_slots_for_day(day)
        self.assertEqual(old["15:50"], "50 15 * * 1-5")
        self.assertEqual(old["17:00"], "0 17 * * 1-5")
        self.assertNotIn("16:16", old)
        self.assertNotIn("17:46", old)
        self.assertEqual(new["16:16"], "16 16 * * 1-5")
        self.assertEqual(new["17:46"], "46 17 * * 1-5")
        self.assertNotIn("15:50", new)
        self.assertNotIn("17:00", new)
        self.assertEqual(len(new), 5)
        self.assertEqual(official_export_slots(before),
                         ("07:30", "14:46", "15:50", "17:00", "20:30"))
        self.assertEqual(official_export_slots(day),
                         ("07:30", "14:46", "16:16", "17:46", "20:30"))

    def test_real_chicago_times_and_eu_us_dst_mismatch(self):
        chicago = ZoneInfo("America/Chicago")
        rome = ZoneInfo("Europe/Rome")
        for day, first, second in (
            (date(2026,11,16), "16:16", "17:46"),
            (date(2027,3,15), "15:16", "16:46"),
            (date(2027,11,3), "15:16", "16:46"),
        ):
            with self.subTest(day=day):
                self.assertEqual(datetime(day.year,day.month,day.day,9,16,
                                          tzinfo=chicago).astimezone(rome)
                                          .strftime("%H:%M"), first)
                self.assertEqual(datetime(day.year,day.month,day.day,10,46,
                                          tzinfo=chicago).astimezone(rome)
                                          .strftime("%H:%M"), second)

    def test_compact_replay_and_official_same_slot_are_separate(self):
        day = date(2026,11,16)
        study = {"16:16": {"origin": "STUDIO NON UFFICIALE",
                            "clock": "16:16:02", "sha": "study"}}
        official = {"16:16": {"origin": "UFFICIALE",
                               "clock": "16:16:28", "sha": "official"},
                    "17:46": {"origin": "UFFICIALE",
                                "clock": "17:46:07"}}
        frames = merge_replay_sources(study, official, day)
        self.assertEqual(frames["16:16"]["sha"], "official")
        self.assertEqual(frames["16:16 · Replay"]["sha"], "study")
        self.assertIn("17:46", frames)
        self.assertIn("16:16", expected_replay_slots(day))
        self.assertIn("17:46", expected_replay_slots(day))
        self.assertNotIn("17:46", expected_replay_slots(date(2026,11,13)))

    def test_replay_expected_slots_exclude_retired_official_identifiers(self):
        historical = expected_replay_slots(date(2026, 11, 13))
        future = expected_replay_slots(date(2026, 11, 16))
        self.assertIn("15:50", historical)
        self.assertIn("17:00", historical)
        self.assertNotIn("17:46", historical)
        self.assertIn("16:16", future)
        self.assertIn("17:46", future)
        self.assertNotIn("15:50", future)
        self.assertNotIn("17:00", future)

    def test_comparison_default_tracks_chicago_not_constant_1550(self):
        from gamma_section import _afternoon_default_rome
        self.assertEqual(_afternoon_default_rome(
            date(2026, 11, 13)).strftime("%H:%M"), "15:50")
        for day, target in ((date(2026, 11, 16), "16:16"),
                            (date(2027, 3, 15), "15:16"),
                            (date(2027, 11, 3), "15:16")):
            with self.subTest(day=day):
                self.assertEqual(
                    _afternoon_default_rome(day).strftime("%H:%M"), target)
        source = (Path(__file__).resolve().parents[1] /
                  "gamma_section.py").read_text(encoding="utf-8")
        self.assertIn("target_afternoon = _afternoon_default_rome(day)", source)
        self.assertIn("later, target_afternoon.hour, target_afternoon.minute", source)

    def test_market_map_pine_diagnostic_is_date_aware(self):
        source = (Path(__file__).resolve().parents[1] /
                  "market_map_pine_autogen.py").read_text(encoding="utf-8")
        self.assertIn("recognized = official_slots_for_day(day)", source)
        self.assertNotIn("(OFFICIAL | LEGACY_STUDY).items()", source)

    def test_automatic_pine_does_not_use_obsolete_slots(self):
        day = date(2026,11,16)
        self.assertEqual(auto_slots(day),
                         (("07:30",None), ("14:46","07:30"),
                          ("16:16","07:30"), ("17:46","16:16"),
                          ("20:30","17:46")))
        source = (Path(__file__).resolve().parents[1] /
                  "gamma_section.py").read_text(encoding="utf-8")
        self.assertIn("('16:16', '07:30')", source)
        self.assertIn("('17:46', '16:16')", source)
        self.assertIn("09:16 Chicago", source)
        self.assertIn("10:46 Chicago", source)


if __name__ == "__main__":
    unittest.main()
