"""Pine source and unavailable GEX must never look like original Cboe/Edge."""
import unittest
from datetime import date

from pine_export import make_pine
from pine_compare import make_pine_comparison
from pine_gex_sources import levels_for_export


def data():
    rows=[{"strike_spx":float(100 + x),"net_gamma":float(x*0.01)}
          for x in range(-25,26) if x!=0]
    return ({"expiry":"2026-11-23","rows":rows},
            {"expiry":"2026-11-27","rows":rows})


class PineDerivedSourceTest(unittest.TestCase):
    def test_alpaca_pine_source_is_explicit(self):
        odte,weekly=data()
        code=make_pine(odte,weekly,40,"2026-11-23","",
                       "16:16 Chicago",data_source="ALPACA_INDICATIVE_OCC_OI")
        self.assertIn("ALPACA INDICATIVE",code)
        self.assertIn("Gamma BSM modellato",code)
        self.assertIn("snapshot Cboe",make_pine(odte,weekly,40,"2026-11-23",""))
        self.assertNotIn("// Esportazione STATICA da uno snapshot Cboe",code)

    def test_selected_pine_works_without_morning_and_uses_actual_clock(self):
        odte, weekly = data()
        for group in (odte, weekly):
            core = group["rows"]
            group["display_rows"] = sorted(core + [
                {"strike_spx":float(k), "net_gamma":0.01} for k in range(126,276)
            ],key=lambda x:x["strike_spx"])
        script = make_pine(odte,weekly,16,"2026-11-23","",
                           "16:16 Roma",data_source="ALPACA_INDICATIVE_OCC_OI",
                           capture_clock="16:16")
        self.assertIn("0DTE 16:16",script)
        self.assertIn("Weekly 16:16",script)
        self.assertNotIn("Mostra 0DTE precedente",script)
        self.assertIn("MAX 200 per profilo",script)

    def test_comparison_keeps_same_provenance(self):
        odte,weekly=data()
        script=make_pine_comparison(
            odte,odte,weekly,40,"2026-11-23",
            "07:30","16:16","",
            data_source="ALPACA_INDICATIVE_OCC_OI")
        self.assertIn("ALPACA INDICATIVE",script)
        self.assertIn("Mostra 0DTE precedente",script)

    def test_reject_unknown_source_and_emergency_cboe_timestamp_assumption(self):
        odte,weekly=data()
        with self.assertRaises(ValueError):
            make_pine(odte,weekly,40,"2026-11-23","",
                      data_source="FAKE_EDGE")
        with self.assertRaises(ValueError):
            make_pine(odte,weekly,40,"2026-11-23","",
                      data_source="ALPACA_INDICATIVE_OCC_OI",current_only=True)

    def test_alpaca_pine_cannot_steal_old_edge_gex_levels(self):
        fake_frame={"origin":"SLOT UFFICIALE · ALPACA INDICATIVE / GAMMA MODELLATO",
                    "snapshot_path":"2026-W48/snapshots/SPX_ALPACA_20261123_160000.json.gz",
                    "clock":"16:16:01","spot":100,
                    "cboe_timestamp_original":"2026-11-23 16:16:01"}
        self.assertIsNone(levels_for_export(None,"2026-W48",date(2026,11,23),
                                             "16:16",fake_frame,[]))


if __name__=="__main__":
    unittest.main()
