"""Independent 0DTE Edge Call/Put reconciliation; no network or Cboe files."""
import ast
from dataclasses import dataclass
from pathlib import Path
import unittest

from edge_gex_exposure import (
    full_chain_gex_components, format_gex_billions,
)

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Contract:
    type: str
    gex: float | None
    valid: bool = True


class EdgeExposureSummaryTests(unittest.TestCase):
    def test_full_chain_call_put_matches_original_engine_net_gross(self):
        # A real snapshot may have thousands of contracts; do NOT restrict
        # this calculation to displayed strike bars in +/-10% of spot.
        options = [
            Contract("call", 11.7e9),
            Contract("put", -17.43e9),
            Contract("call", 25e9, valid=False),
        ]
        values = full_chain_gex_components(options, -5.73e9, 29.13e9)
        self.assertAlmostEqual(values["call_gex"], 11.7e9, delta=.01)
        self.assertAlmostEqual(values["put_gex"], -17.43e9, delta=.01)
        self.assertEqual(values["modeled_gamma_sign"], "Negative")
        self.assertEqual(format_gex_billions(-5.73e9, signed=True),
                         "-5.73 mld USD")
        self.assertEqual(format_gex_billions(11.7e9, signed=True),
                         "+11.70 mld USD")
        self.assertEqual(format_gex_billions(-17.43e9, signed=True),
                         "-17.43 mld USD")

    def test_negative_positive_zero_and_neutral_are_not_conflated(self):
        # Regime Edge may use a neutral threshold even when net sign < 0.
        vals = full_chain_gex_components(
            [Contract("call", 10e9), Contract("put", -10.1e9)],
            -.1e9, 20.1e9)
        self.assertEqual(vals["modeled_gamma_sign"], "Negative")
        self.assertEqual(full_chain_gex_components(
            [Contract("call", 2e9), Contract("put", -1e9)],
            1e9, 3e9)["modeled_gamma_sign"], "Positive")
        self.assertEqual(full_chain_gex_components(
            [Contract("call", 2e9), Contract("put", -2e9)],
            0, 4e9)["modeled_gamma_sign"], "Zero")

    def test_reject_incoherent_total_and_invalid_sign(self):
        options = [Contract("call", 10e9), Contract("put", -12e9)]
        with self.assertRaisesRegex(ValueError, "non riconciliati"):
            full_chain_gex_components(options, -1e9, 22e9)
        with self.assertRaisesRegex(ValueError, "non riconciliati"):
            full_chain_gex_components(options, -2e9, 50e9)
        with self.assertRaisesRegex(ValueError, "segno"):
            full_chain_gex_components([Contract("put", 1e9)], 1e9, 1e9)
        with self.assertRaisesRegex(ValueError, "tipologia"):
            full_chain_gex_components([Contract("other", 1e9)], 1e9, 1e9)
        with self.assertRaisesRegex(ValueError, "non finito"):
            full_chain_gex_components([Contract("call", float("nan"))],
                                      0, 0)

    def test_adapter_uses_all_expiry_contracts_not_chart_rows(self):
        adapter = (ROOT / "edge_auto_engine.py").read_text(encoding="utf-8")
        payload = (ROOT / "edge_auto.py").read_text(encoding="utf-8")
        panel = (ROOT / "edge_triptych.py").read_text(encoding="utf-8")
        for filename, body in (
            ("edge_auto_engine.py", adapter),
            ("edge_auto.py", payload),
            ("edge_triptych.py", panel),
            ("edge_gex_exposure.py",
             (ROOT / "edge_gex_exposure.py").read_text(encoding="utf-8")),
        ):
            ast.parse(body, filename=filename)
        self.assertIn(
            "full_chain_gex_components(\n        contracts, levels.net_gex, levels.gross_gex)",
            adapter)
        self.assertIn('"call_gex": components["call_gex"]', adapter)
        self.assertIn('"put_gex": components["put_gex"]', adapter)
        self.assertIn('"modeled_gamma_sign": components', adapter)
        self.assertIn('"call_gex":result["call_gex"]', payload)
        self.assertIn('"put_gex":result["put_gex"]', payload)
        self.assertIn('"modeled_gamma_sign":result["modeled_gamma_sign"]', payload)
        self.assertIn('**GEX EDGE 0DTE — esposizioni modellate**', panel)
        self.assertIn('"Regime Edge"', panel)
        self.assertIn('Call GEX', panel)
        self.assertIn('Put GEX', panel)
        self.assertIn("posizioni osservate", panel)
        self.assertIn("non disponibile nel report", panel)


if __name__ == "__main__":
    unittest.main()
