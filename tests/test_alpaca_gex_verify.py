"""Offline independent Alpaca GEX source-SHA authentication regression."""
import gzip
import hashlib
import json
import unittest

from alpaca_gex_verify import verify_pair, GEX_NAME


WEEK="2026-W48"
NAME="GEX_ALPACA_20261123_154000_official_16_16_*_*_1-5.json"
REPLAY_NAME="GEX_ALPACA_20261123_154000_replay_16-16.json"
CLOCK="2026-11-23T16:40:00+01:00"
UTC="2026-11-23T15:40:00+00:00"


def report(expiry,digest):
    rows=[{"strike_spx":round(100+n*.1,2),
           "call_gex":1e6,"put_gex":-1e6,"net_gex":0.0}
          for n in range(-25,26) if n!=0]
    return {
        "schema":"serafini_alpaca_gex_v1",
        "source":"ALPACA_INDICATIVE_OCC_OI",
        "mode":"0DTE" if expiry=="2026-11-23" else "Weekly",
        "expiries":[expiry],"spot_spx":100.0,
        "call_gex":50e6,"put_gex":-50e6,
        "net_gex":0.0,"gross_gex":100e6,
        "quality_sufficient":False,
        "source_snapshot_sha256":digest,
        "captured_utc":UTC,
        "rows":rows,
        "curve":[{"spot_spx":90+n*.1,"net_gex":0.0} for n in range(201)],
        "call_wall_spx":99.9,"put_wall_spx":100.1,"gamma_flip_spx":None,
    }


def fixtures(kind="official"):
    snap={
        "symbol":"_SPX",
        "source_provenance":{
           "source":"ALPACA_INDICATIVE_OCC_OI",
           "kind":"DERIVED_ALPACA_INDICATIVE_NOT_CBOE",
           "download_utc":UTC},
        "data":{"options":[],"current_price":100,"prev_day_close":100},
    }
    blob=gzip.compress(json.dumps(snap).encode(),mtime=0)
    digest=hashlib.sha256(blob).hexdigest()
    rawpath=f"{WEEK}/snapshots/SPX_ALPACA_20261123_154000.json.gz"
    compactpath=f"replay/{WEEK}/2026-11-23_16-16.json"
    related=rawpath if kind=="official" else compactpath
    name=NAME if kind=="official" else REPLAY_NAME
    pair={
        "schema":"serafini_alpaca_gex_pair_v1",
        "symbol":"SPX","source":"ALPACA_INDICATIVE_OCC_OI",
        "source_snapshot_sha256":digest,
        "source_snapshot_filename":"SPX_ALPACA_20261123_154000.json.gz",
        "captured_utc":UTC,
        "download_rome":CLOCK,
        "kind":kind,"related_serafini_path":related,
        "reports":{"0DTE":report("2026-11-23",digest),
                   "Weekly":report("2026-11-27",digest)},
    }
    compact={"schema":"serafini_replay_v1",
             "kind":"STUDIO_INTRADAY_ALPACA_DERIVED",
             "source":"ALPACA_INDICATIVE_OCC_OI",
             "source_snapshot_sha256":digest,
             "download_rome":CLOCK,
             "source_provenance":{"kind":"DERIVED_ALPACA_INDICATIVE_NOT_CBOE"}}
    return name,pair,{rawpath:blob,compactpath:json.dumps(compact).encode(),
                       f"gex-alpaca/{WEEK}/{name}":json.dumps(pair).encode()}


class FakeStore:
    def __init__(self,files):
        self.files=files
    def download(self,path):
        return self.files[path]


class GexSourceTests(unittest.TestCase):
    def test_official_gex_cannot_drift_from_snapshot(self):
        name,pair,files=fixtures("official")
        verified,stamp=verify_pair(FakeStore(files),WEEK,name)
        self.assertEqual(verified["source_snapshot_sha256"],pair["source_snapshot_sha256"])
        self.assertEqual(stamp.isoformat(),CLOCK)
        path=pair["related_serafini_path"]
        files[path]=gzip.compress(b"other chain")
        with self.assertRaises(ValueError):
            verify_pair(FakeStore(files),WEEK,name)

    def test_replay_gex_requires_matching_compact_raw_digest(self):
        name,pair,files=fixtures("replay")
        verified,_=verify_pair(FakeStore(files),WEEK,name)
        self.assertEqual(verified["kind"],"replay")
        path=pair["related_serafini_path"]
        compact=json.loads(files[path])
        compact["source_snapshot_sha256"]="0"*64
        files[path]=json.dumps(compact).encode()
        with self.assertRaises(ValueError):
            verify_pair(FakeStore(files),WEEK,name)

    def test_wrong_model_report_is_rejected(self):
        name,pair,files=fixtures()
        pair["reports"]["0DTE"]["quality_sufficient"]=True
        files[f"gex-alpaca/{WEEK}/{name}"]=json.dumps(pair).encode()
        with self.assertRaises(ValueError):
            verify_pair(FakeStore(files),WEEK,name)

    def test_wrong_gex_time_is_rejected(self):
        name,pair,files=fixtures()
        pair["reports"]["Weekly"]["captured_utc"]="2026-11-22T10:00:00+00:00"
        files[f"gex-alpaca/{WEEK}/{name}"]=json.dumps(pair).encode()
        with self.assertRaises(ValueError):
            verify_pair(FakeStore(files),WEEK,name)

    def test_non_gex_legacy_or_external_names_rejected(self):
        self.assertIsNone(GEX_NAME.fullmatch("SPX_CBOE_20261123_154000.json.gz"))
        self.assertIsNone(GEX_NAME.fullmatch("../GEX_ALPACA_20261123_154000_replay_16-16.json"))


if __name__=="__main__":
    unittest.main()
