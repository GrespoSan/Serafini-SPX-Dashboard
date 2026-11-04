"""Deterministic, offline checks of the read-only Edge diagnostics."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import unittest

from edge_diagnostics import net_gross_pct, spot_freshness_label, summarize_exposures

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class C:
    type: str
    expiry: date
    strike: float
    gex: float
    oi: float
    multiplier: float = 100.0
    iv: float = .2
    T: float = .01
    valid: bool = True


def fake_delta(spot, strike, T, rate, iv, kind):
    assert rate == 0.0
    return .4 if kind == "call" else -.6


class DiagnosticsTests(unittest.TestCase):
    def test_net_gross_signed_ratio_and_zero(self):
        self.assertAlmostEqual(net_gross_pct(-5.73e9, 29.13e9),
                               -19.67044284243048, places=6)
        self.assertEqual(net_gross_pct(3, 5), 60.0)
        self.assertIsNone(net_gross_pct(0, 0))
        with self.assertRaises(ValueError):
            net_gross_pct(5, 4)

    def test_spot_freshness_no_false_real_time_claim(self):
        self.assertIn("NON", spot_freshness_label("regular_close"))
        self.assertIn("NON verificabile", spot_freshness_label("payload"))
        self.assertIn("non verificabile", spot_freshness_label(None).lower())

    def test_expiries_dex_and_invalid_contracts(self):
        a=date(2026,11,4)
        b=date(2026,11,5)
        contracts=[
            C("call",a,95,2e9,10),
            C("put",a,105,-3e9,20),
            C("call",b,100,7e9,5),
            C("put",a,110,-99e9,100,valid=False),
        ]
        r=summarize_exposures(contracts, 100, fake_delta)
        self.assertEqual([x["expiry"] for x in r["expiries"]],
                         ["2026-11-04","2026-11-05"])
        first=r["expiries"][0]
        self.assertEqual(first["net_gex"],-1e9)
        self.assertEqual(first["gross_gex"],5e9)
        self.assertEqual(first["net_gross_pct"],-20.)
        self.assertEqual(first["valid_contracts"],2)
        # Delta * OI * multiplier * spot; puts negative by option Delta.
        self.assertAlmostEqual(r["call_dex"],60000)
        self.assertAlmostEqual(r["put_dex"],-120000)
        self.assertAlmostEqual(r["net_dex"],-60000)
        self.assertAlmostEqual(r["gross_dex"],180000)
        self.assertEqual(len(r["dex_rows"]),3)

    def test_invalid_delta_rejected(self):
        contracts=[C("put",date(2026,11,4),100,-1e9,10)]
        with self.assertRaisesRegex(ValueError,"Segno put"):
            summarize_exposures(contracts,100,lambda *args:.5)
        with self.assertRaisesRegex(ValueError,"non finiti"):
            summarize_exposures(contracts,100,lambda *args:float("nan"))

    def test_code_is_isolated_and_all_modules_parse(self):
        for name in ("edge_diagnostics.py","edge_auto_engine.py",
                     "edge_auto.py","edge_triptych.py"):
            body=(ROOT/name).read_text(encoding="utf-8")
            ast.parse(body,filename=name)
        adapter=(ROOT/"edge_auto_engine.py").read_text(encoding="utf-8")
        panel=(ROOT/"edge_triptych.py").read_text(encoding="utf-8")
        self.assertIn('prepare_contracts(raw, "Prossime 4 scadenze")',adapter)
        self.assertIn('summarize_exposures(contracts, spot, bsm.delta)',adapter)
        self.assertIn('"net_gross_pct": net_gross_pct',adapter)
        self.assertIn('DEX EDGE 0DTE — pannello Delta separato',panel)
        self.assertIn('fotografia Cboe',panel)
        self.assertNotIn('from net_gamma import', (ROOT/"edge_diagnostics.py").read_text(encoding="utf-8"))


if __name__=="__main__":
    unittest.main()
