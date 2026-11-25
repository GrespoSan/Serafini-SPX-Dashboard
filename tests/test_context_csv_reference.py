import ast
import unittest
from datetime import date
from pathlib import Path

class ContextTests(unittest.TestCase):
    def test_csv_reference_layout(self):
        s=Path("app.py").read_text()
        ast.parse(s)
        self.assertEqual(s.count('st.expander("ANALISI DEL CONTESTO'),1)
        context=s[s.index("# Source of truth: Serafini-CSV/app.py"):]
        labels=["News e calendario macroeconomico","render_vix_context(trend_day","render_trend_daily(reference_day","QUADRO OPERATIVO — ORE 09:00"]
        self.assertEqual([context.index(x) for x in labels],sorted(context.index(x) for x in labels))
        self.assertIn("modeled_gex=None",context)
        self.assertNotIn("trend_edge_gex",context)
        self.assertNotIn("col_snapshots.metric",context)
        self.assertIn("week = week_choice()",s)
        self.assertIn("asia_panel = render(store, week, snaps, logs",s)
    def test_morning_context_preserves_no_lookahead(self):
        s=Path("gamma_section.py").read_text()
        ast.parse(s)
        self.assertNotIn("st.expander('QUADRO OPERATIVO",s)
        self.assertIn("download_clock > '09:00'",s)
        self.assertIn("_morning_asia_candidates(week, day",s)
        self.assertIn("return asia_panel",s)
    def test_automatic_es_contract_and_imports(self):
        from mercati.quarterly_contract import quarterly_contract
        self.assertEqual(quarterly_contract("ES",date(2026,11,24))["ticker"],"ESZ6")
        self.assertEqual(quarterly_contract("ES",date(2026,12,14))["ticker"],"ESH7")
        import asia_gamma, daily_volatility, macro_calendar, vix_regime, trend_daily_section
        self.assertTrue(callable(asia_gamma.render_auto))
        self.assertTrue(callable(daily_volatility.previous_es_daily_range))

if __name__=="__main__":
    unittest.main()
