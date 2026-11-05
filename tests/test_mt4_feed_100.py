"""MT4-only 100-strike feed and legacy 50-strike compatibility (offline)."""
import csv
import gzip
import io
import json
import tempfile
from pathlib import Path
import unittest
from datetime import date, datetime
from unittest.mock import patch

import mt4_feed


DAY = date(2026, 11, 4)
REFERENCE = 7650.0
BASIS = {"basis": 29.0, "reference_date": "2026-11-03", "source": "TEST"}


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 4, 17, 0, tzinfo=tz)


class Store:
    def list(self, prefix):
        return []


def make_profile(extended=True):
    below = list(range(7600, 7650))
    above = list(range(7651, 7701))
    all_prices = below + above
    core_prices = below[25:] + above[:25]
    def rows(values):
        return [{"strike_spx": float(p), "net_gamma": float(p-7650)} for p in values]
    return {"rows": rows(core_prices), "mt4_rows": rows(all_prices) if extended else []}


def frame(clock, extended=True, origin="STUDIO NON UFFICIALE"):
    return {
        "clock": clock, "origin": origin, "reference_spx": REFERENCE,
        "spot": REFERENCE,
        "cboe_timestamp_original": "2026-11-04 16:45:11",
        "profiles": {"0DTE": make_profile(extended), "Weekly": make_profile(extended)},
    }


def csv_rows(data):
    rows = list(csv.reader(io.StringIO(data.decode("utf-8"))))
    meta = {r[0]:r[1] for r in rows if len(r)==2}
    profiles = {"0DTE": [], "Weekly": []}
    for r in rows:
        if len(r)==5 and r[0] in profiles:
            profiles[r[0]].append(r)
    return meta, profiles


class MT4FeedTest(unittest.TestCase):
    def build(self, mode="Replay", strikes=100, study_extended=True, official_extended=True):
        official = {"15:50": frame("15:50:02", official_extended, "UFFICIALE")}
        study = {"16:46": frame("16:46:02", study_extended)}
        with patch.object(mt4_feed, "datetime", Clock), patch.object(
                mt4_feed, "_official_frames", return_value=official), patch.object(
                mt4_feed, "_study_frames", return_value=study):
            return mt4_feed.build_feed(Store(), DAY, BASIS, mode=mode, strike_count=strikes)

    def test_100_strikes_per_profile_and_basis(self):
        meta, rows = csv_rows(self.build())
        self.assertEqual(meta["schema"], "serafini_mt4_v4")
        self.assertEqual(meta["profile_strikes"], "100")
        self.assertEqual(meta["slot_rome"], "16:46")
        for kind in ("0DTE", "Weekly"):
            self.assertEqual(len(rows[kind]), 100)
            self.assertEqual(len({float(r[2]) for r in rows[kind]}), 100)
            for r in rows[kind]:
                self.assertAlmostEqual(float(r[3])-float(r[2]), 29.0)
                self.assertAlmostEqual(float(r[4]),float(r[2])-REFERENCE)

    def test_legacy_feed_is_unchanged_50(self):
        meta, rows = csv_rows(self.build(strikes=50))
        self.assertEqual(meta["schema"], "serafini_mt4_v3")
        self.assertEqual(len(rows["0DTE"]), 50)
        self.assertEqual(len(rows["Weekly"]), 50)

    def test_historical_replay_falls_back_to_real_official_100(self):
        meta, rows = csv_rows(self.build(study_extended=False))
        self.assertEqual(meta["slot_rome"], "15:50")
        self.assertEqual(meta["origin"], "UFFICIALE")
        self.assertEqual(len(rows["0DTE"]), 100)

    def test_no_fabricated_100_strikes(self):
        with self.assertRaisesRegex(ValueError, "Nessun frame"):
            self.build(study_extended=False, official_extended=False)

    def test_official_100_is_independent(self):
        meta, rows = csv_rows(self.build(mode="Ufficiale"))
        self.assertEqual(meta["slot_rome"], "15:50")
        self.assertEqual(meta["mode"], "Ufficiale")
        self.assertEqual(len(rows["Weekly"]), 100)


    def test_verified_artifact_adds_edge_sign_to_only_100_strike_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = json.dumps({
                "timestamp": "2026-11-04 16:45:11",
                "data": {"current_price": REFERENCE}}).encode()
            Path(folder, "SPX_CBOE_20261104_164511.json.gz").write_bytes(
                gzip.compress(raw))
            def edge(raw_bytes, mode):
                self.assertEqual(raw_bytes[:2], bytes((31, 139)))
                self.assertEqual(mode, "0DTE")
                return {"symbol":"SPX", "mode":"0DTE",
                        "expiries":[DAY.isoformat()],
                        "quality_sufficient":True, "net":2_770_000_000.0,
                        "modeled_gamma_sign":"Positive"}
            with patch.object(mt4_feed,"datetime",Clock), patch.object(
                mt4_feed,"_official_frames",return_value={
                    "15:50":frame("15:50:02",True,"UFFICIALE")}), patch.object(
                mt4_feed,"_study_frames",return_value={
                    "16:46":frame("16:46:02",True)}):
                data=mt4_feed.build_feed(
                    Store(),DAY,BASIS,mode="Replay",strike_count=100,
                    edge_artifacts=folder,edge_analyzer=edge)
                legacy=mt4_feed.build_feed(
                    Store(),DAY,BASIS,mode="Replay",strike_count=50,
                    edge_artifacts=folder,edge_analyzer=edge)
        meta,profiles=csv_rows(data)
        self.assertEqual(meta["gex_model"],"EDGE_0DTE")
        self.assertEqual(meta["gex_modeled_sign"],"Positive")
        self.assertEqual(meta["gex_cboe_timestamp"],meta["cboe_timestamp"])
        self.assertEqual(meta["gex_expiry"],DAY.isoformat())
        self.assertEqual(len(profiles["0DTE"]),100)
        self.assertEqual(len(profiles["Weekly"]),100)
        old_meta,_=csv_rows(legacy)
        self.assertNotIn("gex_modeled_sign",old_meta)
        self.assertEqual(old_meta["schema"],"serafini_mt4_v3")

    def test_official_100_gex_from_already_downloaded_original_without_trigger_artifact(self):
        """A later Replay workflow must not delete the official 0DTE Edge sign."""
        payload={"timestamp":"2026-11-04 16:45:11",
                 "data":{"current_price": REFERENCE}}
        already_read=gzip.compress(json.dumps(payload).encode())
        official_frame=frame("15:50:02",True,"UFFICIALE")
        official_frame["_mt4_original_cboe_bytes"]=already_read

        def model(raw,mode):
            self.assertEqual(raw,already_read)
            self.assertEqual(mode,"0DTE")
            return {"symbol":"SPX","mode":"0DTE",
                    "expiries":[DAY.isoformat()],
                    "quality_sufficient":True,
                    "net":-16_758_700_000.0,
                    "modeled_gamma_sign":"Negative"}

        with patch.object(mt4_feed,"datetime",Clock), patch.object(
            mt4_feed,"_official_frames",
            return_value={"15:50":official_frame}) as official_mock, patch.object(
            mt4_feed,"_study_frames", return_value={"16:46":frame("16:46:02",True)}):
            result=mt4_feed.build_feed(
                Store(),DAY,BASIS,mode="Ufficiale",strike_count=100,
                edge_artifacts="/unavailable/nonmatching_artifact",edge_analyzer=model)
            old=mt4_feed.build_feed(
                Store(),DAY,BASIS,mode="Ufficiale",strike_count=50,
                edge_artifacts="/unavailable/nonmatching_artifact",edge_analyzer=model)

        meta,rows=csv_rows(result)
        self.assertEqual(meta["schema"],"serafini_mt4_v4")
        self.assertEqual(meta["gex_model"],"EDGE_0DTE")
        self.assertEqual(meta["gex_modeled_sign"],"Negative")
        self.assertEqual(meta["gex_cboe_timestamp"],meta["cboe_timestamp"])
        self.assertEqual(len(rows["0DTE"]),100)
        self.assertEqual(len(rows["Weekly"]),100)
        previous,_=csv_rows(old)
        self.assertNotIn("gex_modeled_sign",previous)
        self.assertEqual(previous["schema"],"serafini_mt4_v3")
        self.assertEqual(
            official_mock.call_args_list[0].kwargs["include_mt4_raw"],True)
        self.assertEqual(
            official_mock.call_args_list[1].kwargs["include_mt4_raw"],False)

    def test_official_raw_must_match_frame_even_when_already_downloaded(self):
        bad=frame("15:50:02",True,"UFFICIALE")
        bad["_mt4_original_cboe_bytes"]=gzip.compress(json.dumps(
            {"timestamp":"2026-11-04 12:05:00",
             "data":{"current_price": REFERENCE}}).encode())
        called=[]
        def should_not_run(*args):
            called.append(1)
            raise AssertionError("Must not analyze unmatching raw")
        self.assertIsNone(mt4_feed.verified_edge_from_artifact(
            bad,DAY,artifact_dir=None,analyzer=should_not_run))
        self.assertFalse(called)

    def test_wrong_raw_timestamp_or_model_is_nonblocking_and_nd(self):
        with tempfile.TemporaryDirectory() as folder:
            data={"timestamp":"2026-11-04 15:30:00",
                  "data":{"current_price":REFERENCE}}
            file=Path(folder,"SPX_CBOE_20261104_153000.json.gz")
            file.write_bytes(gzip.compress(json.dumps(data).encode()))
            frame_obj=frame("16:46:02",True)
            self.assertIsNone(mt4_feed.verified_edge_from_artifact(
                frame_obj,DAY,folder,analyzer=lambda *_: (_ for _ in ()).throw(
                    AssertionError("Mismatched timestamp must never analyze"))))
            data["timestamp"]=frame_obj["cboe_timestamp_original"]
            file.write_bytes(gzip.compress(json.dumps(data).encode()))
            base={"symbol":"SPX","mode":"0DTE",
                  "expiries":[DAY.isoformat()],
                  "quality_sufficient":True,"net":-2_000_000_000.0,
                  "modeled_gamma_sign":"Negative"}
            self.assertEqual(mt4_feed.verified_edge_from_artifact(
                frame_obj,DAY,folder,analyzer=lambda *_: base
            )["gex_modeled_sign"],"Negative")
            for wrong in (
                dict(base, modeled_gamma_sign="Positive"),
                dict(base, quality_sufficient=False),
                dict(base, expiries=["2026-11-05"]),
                dict(base, mode="Weekly"),
                dict(base, net=float("nan")),
            ):
                self.assertIsNone(mt4_feed.verified_edge_from_artifact(
                    frame_obj,DAY,folder,analyzer=lambda *args: wrong))


if __name__ == "__main__":
    unittest.main()
