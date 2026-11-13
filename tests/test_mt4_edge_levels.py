"""MT4 100-strike bridge: GEX Wall/Flip must follow exact newest source.

Strict offline tests; no external Cboe/Supabase/GitHub requests.
"""
import csv
import gzip
import io
import json
import math
import sys
import types
import unittest
from datetime import date, datetime
from unittest.mock import patch

import mt4_feed

DAY = date(2026, 11, 4)
BASIS = {"basis": 29.0, "reference_date": DAY.isoformat(), "source": "TEST"}
REFERENCE = 7500.0


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 4, 19, 0, tzinfo=tz)


class Store:
    def list(self, prefix):
        return []


def sample_frame(clock, *, official=False):
    ref = REFERENCE
    rows = [{"strike_spx": float(s), "net_gamma": float(s) - ref}
            for s in list(range(7450, 7500)) + list(range(7501, 7551))]
    return {
        "clock": clock, "origin": "UFFICIALE" if official else "STUDIO NON UFFICIALE",
        "spot": ref, "reference_spx": ref,
        "cboe_timestamp_original": "2026-11-04 16:45:11",
        "profiles": {
            "0DTE": {"mt4_rows": rows, "rows": rows[25:75]},
            "Weekly": {"mt4_rows": rows, "rows": rows[25:75]},
        },
    }


def csv_meta(data):
    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    return {row[0]: row[1] for row in rows if len(row) == 2}


def fake_engine(raw, mode):
    return {
        "symbol": "SPX", "mode": "0DTE",
        "expiries": [DAY.isoformat()], "quality_sufficient": True,
        "net": 8_000_000_000, "modeled_gamma_sign": "Positive",
        "call_wall": 7510, "put_wall": 7480, "flip": 7495.5,
    }


class EdgeLevelsTests(unittest.TestCase):
    def test_three_model_levels_from_matching_official_raw(self):
        raw = gzip.compress(json.dumps({
            "timestamp": "2026-11-04 16:45:11",
            "data": {"current_price": REFERENCE},
        }).encode())
        frame = sample_frame("17:00:01", official=True)
        frame["_mt4_original_cboe_bytes"] = raw
        with patch.object(mt4_feed, "datetime", Clock), patch.object(
                mt4_feed, "_official_frames", return_value={"17:00": frame}), patch.object(
                mt4_feed, "_study_frames", return_value={}):
            result = mt4_feed.build_feed(
                Store(), DAY, BASIS, mode="Ufficiale", strike_count=100,
                edge_analyzer=fake_engine)
        metadata = csv_meta(result)
        self.assertEqual(metadata["slot_rome"], "17:00")
        self.assertEqual(metadata["gex_model"], "EDGE_0DTE")
        self.assertEqual(metadata["gex_call_wall_spx"], "7510.0")
        self.assertEqual(metadata["gex_call_wall_es"], "7539.0")
        self.assertEqual(metadata["gex_put_wall_spx"], "7480.0")
        self.assertEqual(metadata["gex_put_wall_es"], "7509.0")
        self.assertEqual(metadata["gex_gamma_flip_es"], "7524.5")
        self.assertEqual(metadata["gex_cboe_timestamp"], metadata["cboe_timestamp"])
        self.assertIn("gex_source_sha256", metadata)

    def test_last_replay_with_verified_sidecar_overrides_older_official(self):
        official = sample_frame("15:50:01", official=True)
        replay = sample_frame("18:30:02")
        expected_edge = {
            "sha256": "a" * 64, "spot_spx": REFERENCE, "quality_sufficient": True,
            "modeled_gamma_sign": "Negative", "net_gex": -2_000_000_000,
            "call_wall_spx": 7540, "put_wall_spx": 7470, "gamma_flip_spx": 7501,
        }
        compact = {"cboe_timestamp_original": replay["cboe_timestamp_original"]}
        sidecar = {"cboe_sha256": "a" * 64}
        clock = datetime.fromisoformat("2026-11-04T18:30:02+01:00")
        module = types.ModuleType("edge_replay_data")
        module.read_pair = lambda store, week, day, slot: (
            compact, sidecar, expected_edge, clock)
        with patch.dict(sys.modules, {"edge_replay_data": module}), patch.object(
                mt4_feed, "datetime", Clock), patch.object(
                mt4_feed, "_official_frames", return_value={"15:50": official}), patch.object(
                mt4_feed, "_study_frames", return_value={"18:30": replay}):
            result = mt4_feed.build_feed(Store(), DAY, BASIS, mode="Replay", strike_count=100)
        metadata = csv_meta(result)
        self.assertEqual(metadata["slot_rome"], "18:30")
        self.assertEqual(metadata["gex_call_wall_es"], "7569.0")
        self.assertEqual(metadata["gex_put_wall_es"], "7499.0")
        self.assertEqual(metadata["gex_gamma_flip_es"], "7530.0")
        self.assertEqual(metadata["gex_modeled_sign"], "Negative")

    def test_sidecar_rejected_for_wrong_slot_or_invalid_quality(self):
        frame = sample_frame("18:30:02")
        sample = {
            "sha256": "a" * 64, "spot_spx": REFERENCE,
            "quality_sufficient": True,
            "modeled_gamma_sign": "Positive", "net_gex": 4,
            "call_wall_spx": 7540, "put_wall_spx": 7480, "gamma_flip_spx": 7500,
        }
        mod = types.ModuleType("edge_replay_data")
        mod.read_pair = lambda *args: (
            {"cboe_timestamp_original": frame["cboe_timestamp_original"]},
            {"cboe_sha256": "a" * 64},
            sample, datetime.fromisoformat("2026-11-04T18:31:02+01:00"))
        with patch.dict(sys.modules, {"edge_replay_data": mod}):
            self.assertIsNone(mt4_feed.verified_edge_from_saved_replay(
                Store(), "2026-W45", DAY, "18:30", frame))
            mod.read_pair = lambda *args: (
                {"cboe_timestamp_original": frame["cboe_timestamp_original"]},
                {"cboe_sha256": "a" * 64},
                {**sample, "quality_sufficient": False},
                datetime.fromisoformat("2026-11-04T18:30:02+01:00"))
            self.assertIsNone(mt4_feed.verified_edge_from_saved_replay(
                Store(), "2026-W45", DAY, "18:30", frame))

    def test_absent_gamma_flip_is_nd_not_fabricated(self):
        values = mt4_feed.edge_levels_metadata({
            "call_wall": 7520, "put_wall": 7475, "flip": None})
        self.assertEqual(set(values),
                         {"gex_call_wall_spx", "gex_put_wall_spx"})


if __name__ == "__main__":
    unittest.main()
