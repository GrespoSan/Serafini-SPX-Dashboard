"""Offline CBOE SPX upload tests: no real network or Supabase required."""
import gzip
import io
import json
import unittest
from zipfile import ZipFile, ZIP_DEFLATED

from offline_snapshot import InvalidLocalSnapshot, load_local_snapshot

FILENAME = "SPX_CBOE_20261102_082804.json.gz"


def raw_chain(gamma=0.2):
    return json.dumps({
        "symbol": "_SPX",
        "timestamp": "2026-11-02 08:28:04",
        "data": {
            "prev_day_close": 7580.0698,
            "current_price": 7580.0698,
            "options": [{
                "option": f"SPXW261102C{int(7300+i*5)*1000:08d}",
                "gamma": gamma,
                "open_interest": 2,
                "volume": 1,
            } for i in range(120)],
        },
    }).encode("utf-8")


def sample_zip(compressed):
    buf = io.BytesIO()
    with ZipFile(buf, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(FILENAME, compressed)
    return buf.getvalue()


class OfflineSnapshotTests(unittest.TestCase):
    def test_github_zip_reads_without_supabase(self):
        raw = raw_chain()
        compressed = gzip.compress(raw, mtime=0)
        store = load_local_snapshot("spx-daily-sample.zip", sample_zip(compressed))
        self.assertEqual(store.week, "2026-W45")
        self.assertTrue(store.filename.startswith("SPX_CBOE_20261102_082804_"))
        self.assertEqual(store.list("2026-W45/logs/"), [])
        self.assertEqual(len(store.list("2026-W45/snapshots/")), 1)
        self.assertEqual(gzip.decompress(store.download(store.object_path)), raw)

    def test_original_gzip_and_json_supported(self):
        raw = raw_chain()
        for name, payload in ((FILENAME, gzip.compress(raw, mtime=0)),
                              ("_SPX.json", raw)):
            with self.subTest(name=name):
                store = load_local_snapshot(name, payload)
                self.assertEqual(gzip.decompress(store.download(store.object_path)), raw)

    def test_wrong_symbol_rejected(self):
        payload = json.loads(raw_chain())
        payload["symbol"] = "NDX"
        with self.assertRaises(InvalidLocalSnapshot):
            load_local_snapshot("chain.json", json.dumps(payload).encode())

    def test_zero_gamma_rejected(self):
        with self.assertRaises(InvalidLocalSnapshot):
            load_local_snapshot("chain.json", raw_chain(gamma=0.0))

    def test_invalid_zip_content_rejected(self):
        with self.assertRaises(InvalidLocalSnapshot):
            load_local_snapshot("not-cboe.zip", sample_zip(b"not gzip"))

    def test_offline_storage_is_read_only(self):
        store = load_local_snapshot("chain.json", raw_chain())
        with self.assertRaises(FileNotFoundError):
            store.download("2026-W45/snapshots/other.json.gz")


if __name__ == "__main__":
    unittest.main()
