"""Offline regression: all SPX Pine layouts expose editable Edge GEX levels.

Checks provenance and N/D behavior; does not call Cboe, Supabase or TradingView.
"""
import hashlib
import json
import sys
import types
import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pine_export import make_pine
from pine_compare import make_pine_comparison
from pine_gex_sources import levels_for_export, _clean_levels

DAY = date(2026, 11, 13)
ROME = ZoneInfo("Europe/Rome")
WEEK = "2026-W46"


def profile(expiry):
    rows = [
        {"strike_spx": 7480 + i, "net_gamma": float(i - 26)}
        for i in range(50)
    ]
    return {"rows": rows, "expiry": expiry}


class FakeStore:
    def __init__(self, files=None):
        self.files = files or {}
    def download(self, name):
        return self.files[name]


class PineGexStyleTests(unittest.TestCase):
    def test_all_five_exports_have_one_switch_three_color_and_width_inputs(self):
        odte = profile(DAY.isoformat())
        weekly = profile("2026-11-20")
        gex = {"call_wall_spx": 7510.0,
               "put_wall_spx": 7480.0,
               "gamma_flip_spx": 7498.25}
        scripts = [
            make_pine(odte, weekly, 23.0, str(DAY), "2026-11-13 06:30:00",
                      "07:30", gex=gex),
            make_pine_comparison(odte, odte, weekly, 23.0, str(DAY),
                                 "07:30", "14:46", gex=gex),
            make_pine_comparison(odte, odte, weekly, 23.0, str(DAY),
                                 "07:30", "15:50", gex=gex),
            make_pine_comparison(odte, odte, weekly, 23.0, str(DAY),
                                 "15:50", "17:00", gex=gex),
            make_pine(odte, weekly, 23.0, str(DAY), "2026-11-13 19:30:00",
                      "Sera 20:30", gex=gex),
        ]
        for code in scripts:
            with self.subTest(script=code[:120]):
                self.assertEqual(code.count("showGexLevels = input.bool"), 1)
                for key in ("gexCallColor", "gexPutColor", "gexFlipColor"):
                    self.assertEqual(code.count(key + " = input.color"), 1)
                for key in ("gexCallWidth", "gexPutWidth", "gexFlipWidth"):
                    self.assertEqual(code.count(key + " = input.int"), 1)
                self.assertIn("style=line.style_dotted, width=gexFlipWidth", code)
                self.assertIn("extend=extend.both, color=gexCallColor", code)
                self.assertIn("float gexCallES = 7533.00000000", code)
                self.assertIn("float gexPutES = 7503.00000000", code)
                self.assertIn("float gexFlipES = 7521.25000000", code)
                self.assertIn("if validTicker and showGexLevels", code)
                self.assertIn("Proietta Top 3 Call / Put 0DTE", code)

    def test_nd_flip_no_invented_level_no_previous_source(self):
        script = make_pine(profile(str(DAY)), profile("2026-11-20"), 20,
                           str(DAY), "", gex={"call_wall_spx": 7520,
                                               "put_wall_spx": 7470})
        self.assertIn("float gexFlipES = na", script)
        self.assertIn("float gexCallES = 7540.00000000", script)
        unavailable = make_pine(profile(str(DAY)), profile("2026-11-20"),
                                20, str(DAY), "", gex=None)
        self.assertIn("float gexCallES = na", unavailable)
        self.assertIn("float gexPutES = na", unavailable)
        self.assertIn("float gexFlipES = na", unavailable)

    def test_source_requires_0dte_coverage_and_exact_sha(self):
        source = {
            "symbol": "SPX", "mode": "0DTE", "expiries": [str(DAY)],
            "quality_sufficient": True, "sha256": "a"*64,
            "spot_spx": 7500.0, "call_wall_spx": 7520,
            "put_wall_spx": 7480, "gamma_flip_spx": None,
        }
        self.assertEqual(_clean_levels(source, DAY, 7500, "a"*64),
                         {"call_wall_spx": 7520., "put_wall_spx": 7480.})
        self.assertIsNone(_clean_levels(source, DAY, 7500, "b"*64))
        self.assertIsNone(_clean_levels({**source, "quality_sufficient": False},
                                        DAY, 7500, "a"*64))
        self.assertIsNone(_clean_levels({**source, "mode": "Weekly"},
                                        DAY, 7500, "a"*64))

    def test_replay_uses_same_verified_sidecar_and_slot(self):
        report = {
            "symbol": "SPX", "mode": "0DTE", "expiries": [str(DAY)],
            "quality_sufficient": True, "sha256": "a"*64,
            "spot_spx": 7500.0, "call_wall_spx": 7520,
            "put_wall_spx": 7480, "gamma_flip_spx": 7502,
        }
        compact = {"spot_spx": 7500.0, "cboe_sha256": "a"*64,
                   "cboe_timestamp_original": "raw-source-time"}
        clock = datetime(2026, 11, 13, 18, 30, 2, tzinfo=ROME)
        fake = types.ModuleType("edge_replay_data")
        fake.read_pair = lambda *args: (compact, {}, report, clock)
        frame = {"origin": "STUDIO NON UFFICIALE", "clock": "18:30:02",
                 "spot": 7500.0, "cboe_timestamp_original": "raw-source-time"}
        with patch.dict(sys.modules, {"edge_replay_data": fake}):
            levels = levels_for_export(FakeStore(), WEEK, DAY, "18:30",
                                       frame, logs=[])
            self.assertEqual(levels["gamma_flip_spx"], 7502)
            self.assertIsNone(levels_for_export(
                FakeStore(), WEEK, DAY, "18:30",
                {**frame, "clock": "18:31:02"}, logs=[]))

    def test_official_levels_must_match_same_raw_profile(self):
        original = b"gzip-cboe-synthetic"
        log = {"scheduled_slot": "30 7 * * 1-5",
               "rome_date": str(DAY), "status": "NUOVO",
               "file": f"{WEEK}/snapshots/official.json.gz"}
        snap = {"timestamp": "source", "data": {"current_price": 7500.0}}
        rows = [{"strike_spx": 7500.0, "net_gamma": 2.0}]
        frame = {"origin": "UFFICIALE", "clock": "07:30:12",
                 "spot": 7500.0, "reference_spx": 7490.0,
                 "cboe_timestamp_original": "source",
                 "profiles": {"0DTE": {"rows": rows}}}
        edge = {"symbol": "SPX", "mode": "0DTE", "expiries": [str(DAY)],
                "sha256": hashlib.sha256(original).hexdigest(),
                "spot_spx": 7500.0, "quality_sufficient": True,
                "call_wall_spx": 7520, "put_wall_spx": 7480,
                "gamma_flip_spx": 7500}
        store = FakeStore({
            f"{WEEK}/logs/a.json": json.dumps(log).encode(),
            log["file"]: original,
        })
        fake_net = types.ModuleType("net_gamma")
        fake_net.read_snapshot = lambda x: snap
        fake_net.profile = lambda *args: {"rows": rows}
        fake_edge = types.ModuleType("edge_auto")
        fake_edge.result_from_snapshot = lambda raw, offset: edge
        with patch.dict(sys.modules, {"net_gamma": fake_net,
                                      "edge_auto": fake_edge}), patch(
            "gamma_replay.official_slots_for_day",
            return_value={"07:30": "30 7 * * 1-5"}), patch(
            "gamma_replay._download_clock",
            return_value=datetime(2026,11,13,7,30,12,tzinfo=ROME)):
            self.assertEqual(levels_for_export(
                store, WEEK, DAY, "07:30", frame, [{"name": "a.json"}]),
                {"call_wall_spx": 7520., "put_wall_spx": 7480.,
                 "gamma_flip_spx": 7500.})
            self.assertIsNone(levels_for_export(
                store, WEEK, DAY, "07:30",
                {**frame, "clock": "07:30:13"}, [{"name": "a.json"}]))


if __name__ == "__main__":
    unittest.main()
