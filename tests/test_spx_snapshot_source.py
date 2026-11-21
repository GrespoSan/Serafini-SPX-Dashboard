"""Source identity tests without Streamlit, database or network."""
import gzip
import json
import unittest
from datetime import date

from spx_snapshot_source import accepted, parse_name, same_source_day_prefix, visible_source
from net_gamma import read_snapshot, profile


class SourceSelectionTests(unittest.TestCase):
    cboe="SPX_CBOE_20261120_155925.json.gz"
    alpaca="SPX_ALPACA_20261123_151700.json.gz"

    def test_identifies_new_and_old_sources(self):
        self.assertTrue(accepted(self.cboe))
        self.assertTrue(accepted(self.alpaca))
        self.assertEqual(parse_name(self.alpaca)[1],date(2026,11,23))
        self.assertEqual(same_source_day_prefix(self.alpaca),"SPX_ALPACA_20261123_")
        self.assertEqual(same_source_day_prefix(self.cboe),"SPX_CBOE_20261120_")

    def test_invalid_source_names_not_accepted(self):
        self.assertFalse(accepted("SPX_EDGE_20261123_151700.json.gz"))
        self.assertFalse(accepted("SPX_ALPACA_20261132_151700.json.gz"))
        self.assertFalse(accepted("../SPX_CBOE_20261120_155925.json.gz"))

    @staticmethod
    def _sample(source=None):
        options=[]
        for k in [round(100-.01*n,2) for n in range(1,26)] + [
                   round(100+.01*n,2) for n in range(1,26)]:
            for side in ("C","P"):
                options.append({"option":f"SPXW261123{side}{round(k*1000):08d}",
                                "gamma":.01,"open_interest":20,"volume":5})
        doc={"symbol":"_SPX","timestamp":"2026-11-23 15:17:00",
             "data":{"prev_day_close":100.0,"current_price":100.01,"options":options}}
        if source is not None:
            doc["source_provenance"]=source
        return doc

    def test_source_provenance_accepts_model_only_when_explicit(self):
        good={"source":"ALPACA_INDICATIVE_OCC_OI",
              "kind":"DERIVED_ALPACA_INDICATIVE_NOT_CBOE"}
        doc=self._sample(good)
        data=read_snapshot(gzip.compress(json.dumps(doc).encode()))
        self.assertEqual(visible_source(data),"ALPACA_INDICATIVE_DERIVED")
        rows=profile(data,date(2026,11,23),100)
        self.assertEqual(len(rows["rows"]),50)
        self.assertTrue(all(abs(x["net_gamma"])<1e-10 for x in rows["rows"]))
        doc["source_provenance"]["kind"]="PSEUDO_CBOE_ORIGINAL"
        with self.assertRaises(ValueError):
            read_snapshot(gzip.compress(json.dumps(doc).encode()))

    def test_source_aware_replay_accepts_only_verified_alpaca(self):
        from spx_snapshot_source import replay_source_kind
        core={"schema":"serafini_replay_v1", "rome_date":"2026-11-23",
              "kind":"STUDIO_INTRADAY_ALPACA_DERIVED",
              "source":"ALPACA_INDICATIVE_OCC_OI",
              "source_provenance":{"source":"ALPACA_INDICATIVE_OCC_OI",
                                   "kind":"DERIVED_ALPACA_INDICATIVE_NOT_CBOE"}}
        self.assertEqual(replay_source_kind(core),"ALPACA_INDICATIVE_DERIVED")
        core["source_provenance"]["source"]="CBOE_ORIGINAL"
        self.assertIsNone(replay_source_kind(core))
        original={"schema":"serafini_replay_v1",
                  "kind":"STUDIO_INTRADAY_NON_UFFICIALE"}
        self.assertEqual(replay_source_kind(original),"CBOE_ORIGINAL")
        original["source"]="ALPACA_INDICATIVE_OCC_OI"
        self.assertIsNone(replay_source_kind(original))

    def test_legacy_cboe_without_provenance_remains_compatible(self):
        doc=read_snapshot(gzip.compress(json.dumps(self._sample()).encode()))
        self.assertEqual(visible_source(doc),"CBOE_ORIGINAL_OR_MANUAL")


if __name__=="__main__":
    unittest.main()
