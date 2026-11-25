import csv
import io
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock
from pathlib import Path
from test_manual_csv_net_gamma import csv_fixture
from manual_csv.cboe_csv_import import csv_to_snapshot
from manual_csv.mt4 import build_feed, publish, rows100, SOURCE
from manual_csv.net_gamma import profile
from datetime import date

class ManualMT4Tests(unittest.TestCase):
    def setUp(self):
        self.doc=csv_to_snapshot(csv_fixture())
        self.now=datetime(2026,11,23,10,0,tzinfo=timezone.utc)
        self.basis={"reference_date":"2026-11-20"}
        self.sha=self.doc["_import_provenance"]["original_csv_sha256"]

    def build(self, saved=None, source=None, now=None):
        return build_feed(self.doc,6000,16,self.basis,source,saved,"",now or self.now)

    def test_manual_morning_and_100_real_strikes(self):
        lines=list(csv.reader(io.StringIO(self.build().decode())))
        meta=dict(r for r in lines if len(r)==2)
        self.assertEqual(meta["snapshot_source"],SOURCE)
        self.assertEqual(meta["snapshot_sha256"],self.sha)
        self.assertEqual(meta["download_clock_rome"],"10:47:00")
        self.assertEqual(meta["mode"],"Manuale")
        self.assertEqual(meta["serafini_net_gamma_core_strikes"],"100")
        core=sum(row["net_gamma"] for expiry in (date(2026,11,23),date(2026,11,27)) for row in profile(self.doc,expiry,6000)["rows"])
        self.assertAlmostEqual(float(meta["serafini_net_gamma_core_25x2"]),core)
        self.assertNotIn("gex_model",meta)
        for label,expiry in (("0DTE",date(2026,11,23)),("Weekly",date(2026,11,27))):
            rows=[r for r in lines if r[0]==label]
            self.assertEqual(len(rows),100)
            vals={float(r[2]):float(r[4]) for r in rows}
            self.assertEqual(len(vals),100)
            for row in profile(self.doc,expiry,6000)["rows"]:
                self.assertAlmostEqual(vals[row["strike_spx"]],row["net_gamma"])

    def test_same_csv_gex_only(self):
        source={"sha256":self.sha,"symbol":"SPX","oi_mode":"csv"}
        report={"sha256":self.sha,"symbol":"SPX","source":SOURCE,"quality_certified":False,
            "timestamp_utc":"2026-11-23T09:47:00+00:00","spot_info":{"method":"SPXW_0DTE_PUT_CALL_PARITY_R0_Q0"},"oi_info":{"requested_mode":"csv"},
            "reports":{"0DTE":{"status":"DIAGNOSTICO","expiry":"2026-11-23",
                "net_gex":-2e9,"regime":"negative","call_wall":6100.,"put_wall":5900.,"flip":None},
                "Weekly":{"status":"INSUFFICIENTE"}}}
        saved=((self.sha,"SPX","auto","csv"),report)
        body=self.build(saved,source).decode()
        self.assertIn("gex_call_wall_es,6116.000000",body)
        self.assertIn("gex_modeled_sign,Negative",body)
        self.assertNotIn("gex_gamma_flip_es",body)
        report["reports"]["0DTE"]["regime"]="neutral"
        self.assertIn("gex_modeled_sign,Zero",self.build(saved,source).decode())
        report["spot_info"]={"method":"CSV_LAST"}
        self.assertNotIn("gex_model",self.build(saved,source).decode())
        report["sha256"]="a"*64
        self.assertNotIn("gex_model",self.build(saved,source).decode())

    def test_stale_future_and_unauthorized(self):
        for now in (datetime(2026,11,24,10,tzinfo=timezone.utc),
                    datetime(2026,11,23,9,tzinfo=timezone.utc)):
            with self.assertRaises(ValueError):
                self.build(now=now)
        with self.assertRaises(PermissionError):
            publish(Mock(),self.build(),"friend")

    def test_layout_retired_sections(self):
        text=Path("app.py").read_text()
        for label in ("GEX EDGE — CONFRONTO","REPORT GIORNALIERO AI","Replay Curva teorica GEX Edge + ES"):
            self.assertNotIn(label,text)
        self.assertIn("REPLAY E CONFRONTO",text)
        self.assertIn("render_manual_csv_net_gamma(mt4_store=store if role == 'admin' else None)",text)
