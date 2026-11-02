"""SPX Massive day cache regression tests; only synthetic data, no HTTP."""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo
import ast
import os
import unittest

import requests
from spx_massive_day_cache import fetch_json, purge_expired


class DummyResponse:
    def __init__(self, payload=None, status=200):
        self.payload = payload
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code), response=self)
    def json(self):
        return self.payload


class SPXMassiveCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {"SPX_MASSIVE_CACHE_DIR":self.temp.name})
        env.start()
        self.addCleanup(env.stop)
        self.url = "https://api.massive.com/futures/v1/aggs/ESZ6"
        self.params = {
            "resolution":"1session", "window_start.gte":"2026-08-01",
            "window_start.lte":"2026-10-30", "sort":"window_start.asc",
            "limit":250}
        self.payload = {"status":"OK", "results":[
            {"ticker":"ESZ6","session_end_date":"2026-10-30",
             "open":7780.,"high":7800.,"low":7700.,"close":7720.}]}

    def test_one_download_per_day_shared_disk_cache(self):
        called = []
        def requester(url,**kwargs):
            called.append(url)
            return DummyResponse(self.payload)
        with patch("spx_massive_day_cache.requests.get", side_effect=requester):
            first=fetch_json(self.url, self.params, "sample")
            second=fetch_json(self.url, self.params, "sample")
        self.assertEqual(len(called),1)
        self.assertEqual(first, second)
        self.assertEqual(first["results"][0]["close"],7720.)
        dbs=list(Path(self.temp.name).glob("massive-es-*.sqlite3"))
        self.assertEqual(len(dbs),1)
        self.assertNotIn(b"sample",dbs[0].read_bytes())

    def test_cache_key_separates_contract_and_credential(self):
        called=[]
        def getter(url,**kwargs):
            called.append(url)
            return DummyResponse(self.payload)
        with patch("spx_massive_day_cache.requests.get",side_effect=getter):
            fetch_json(self.url,self.params,"key1")
            fetch_json(self.url,self.params,"key2")
            fetch_json(self.url.replace("ESZ6","ESH7"),self.params,"key1")
        self.assertEqual(len(called),3)

    def test_failure_429_partial_and_empty_never_enter_cache(self):
        called=[]
        def getter(url,**kwargs):
            called.append(url)
            if len(called)==1: return DummyResponse(status=429)
            if len(called)==2: return DummyResponse({"status":"OK",
                "results":[{"ticker":"ESZ6"}],"next_url":"more"})
            if len(called)==3: return DummyResponse({"status":"OK","results":[]})
            return DummyResponse(self.payload)
        with patch("spx_massive_day_cache.requests.get",side_effect=getter):
            with self.assertRaises(requests.HTTPError):
                fetch_json(self.url,self.params,"sample")
            self.assertIn("next_url",fetch_json(self.url,self.params,"sample"))
            self.assertEqual(fetch_json(self.url,self.params,"sample")["results"],[])
            self.assertEqual(fetch_json(self.url,self.params,"sample"),self.payload)
            self.assertEqual(fetch_json(self.url,self.params,"sample"),self.payload)
        self.assertEqual(len(called),4)

    def test_midnight_purges_only_temp_files(self):
        previous=Path(self.temp.name)/"massive-es-20261102.sqlite3"
        current=Path(self.temp.name)/"massive-es-20261103.sqlite3"
        cboe=Path(self.temp.name)/"SPX_CBOE_20261102.json.gz.enc"
        for path in (previous,current,cboe): path.write_bytes(b"abc")
        purge_expired(datetime(2026,11,3,0,1,tzinfo=ZoneInfo("Europe/Rome")))
        self.assertFalse(previous.exists())
        self.assertTrue(current.exists())
        self.assertTrue(cboe.exists())

    def test_rome_rollover_signal_for_cache_clear(self):
        import spx_massive_day_cache as mod
        start=datetime(2026,11,2,23,59,tzinfo=ZoneInfo("Europe/Rome"))
        after=datetime(2026,11,3,0,1,tzinfo=ZoneInfo("Europe/Rome"))
        with patch.object(mod,"_LAST_DAY",None):
            self.assertFalse(purge_expired(start))
            self.assertTrue(purge_expired(after))
            self.assertFalse(purge_expired(after))

    def test_mock_getters_bypass_persistent_cache(self):
        calls=[]
        def getter(url,**kwargs):
            calls.append(url)
            return DummyResponse(self.payload)
        fetch_json(self.url,self.params,"sample",get=getter)
        fetch_json(self.url,self.params,"sample",get=getter)
        self.assertEqual(len(calls),2)
        self.assertEqual(list(Path(self.temp.name).glob("massive-es-*.sqlite3")),[])

    def test_automatic_context_preserves_snapshot_listing_and_replay(self):
        app=(Path(__file__).resolve().parents[1]/"app.py").read_text()
        root=ast.parse(app)
        calls=[node for node in ast.walk(root) if isinstance(node,ast.Call)
               and isinstance(node.func,ast.Name)
               and node.func.id=="render_vix_context"]
        self.assertEqual(len(calls),1)
        self.assertNotIn("spx_context_enabled", app)
        self.assertNotIn("if context_enabled:", app)
        self.assertLess(app.index("snaps = store.list("),
                        app.index("context_data = render_vix_context"))
        self.assertIn("render_snapshot_selector(store, week, snaps, logs)",app)
        self.assertIn("show_edge_triptych(week, store, logs)",app)
        self.assertIn("render_gamma_replay(store, week, logs",app)
        self.assertIn("render_emergency_pine()",app)
        self.assertIn("_fetch_massive_es.clear()",app)


if __name__=="__main__":
    unittest.main()
