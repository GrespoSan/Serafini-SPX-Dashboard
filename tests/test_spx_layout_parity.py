"""Layout-only checks for Serafini SPX, no external API calls or secrets."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "app.py").read_text(encoding="utf-8")
GAMMA = (ROOT / "gamma_section.py").read_text(encoding="utf-8")
MANUAL = (ROOT / "manual_acquisition.py").read_text(encoding="utf-8")


class SPXLayoutParityTests(unittest.TestCase):
    def test_all_modules_compile(self):
        for file in ("app.py", "gamma_section.py", "manual_acquisition.py",
                     "offline_snapshot.py", "cboe_csv_import.py", "cboe_source_health.py"):
            with self.subTest(file=file):
                ast.parse((ROOT / file).read_text(encoding="utf-8"),
                          filename=file)

    def test_gamma_selector_and_import_in_one_panel(self):
        self.assertEqual(APP.count("with st.expander('ANALISI NET GAMMA'"), 1)
        self.assertIn("expanded=True", APP.split(
            "with st.expander('ANALISI NET GAMMA'", 1)[1][:75])
        self.assertIn("context_slot = st.empty()", APP)
        self.assertIn("with context_slot.container():", APP)
        self.assertIn("expanded=False", APP.split(
            'with st.expander("ANALISI DEL CONTESTO', 1)[1][:90])
        self.assertNotIn("st.file_uploader(", APP)
        self.assertNotIn("Acquisizione manuale / importazione JSON o CSV", APP)
        self.assertLess(APP.index("render_snapshot_selector(store, week, snaps, logs)"),
                        APP.index("render(store, week, snaps, logs, show_heading=False"))

    def test_external_selector_exported(self):
        functions = {n.name for n in ast.parse(GAMMA).body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertTrue({"render", "render_snapshot_selector"} <= functions)
        self.assertIn("selector_external=True", APP)
        self.assertIn("if selector_external:", GAMMA)

    def test_fallback_does_not_stop_on_402(self):
        self.assertIn("storage_unavailable = True", APP)
        self.assertIn("snaps, logs = [], []", APP)
        self.assertIn("if not storage_unavailable:", APP)
        self.assertNotIn("load_local_snapshot(uploaded_snapshot.name", APP)

    def test_context_independent_of_supabase_and_counters_preserved(self):
        # The counters are intentionally still inside ANALISI DEL CONTESTO.
        self.assertIn("col_snapshots.metric('Snapshot distinti', len(snaps))", APP)
        self.assertIn("col_logs.metric('Registri', len(logs))", APP)
        # A quota failure only clears archive metadata; it does not stop VX/Daily.
        self.assertIn("storage_unavailable = True", APP)
        self.assertIn("snaps, logs = [], []", APP)
        self.assertNotIn("Supabase HTTP 402: gli snapshot archiviati", APP)
        self.assertNotIn("Seleziona una settimana con snapshot Cboe validi per analizzare il VIX", APP)
        self.assertNotIn("context_day", APP)
        self.assertIn("trend_day = datetime.now(ZoneInfo('Europe/Rome')).date()", APP)
        self.assertIn("render_vix_context(trend_day, return_context=True)", APP)
        self.assertIn("render_trend_daily(reference_day=trend_day", APP)
        self.assertIn("es_daily_context = previous_es_daily_range(trend_day)", APP)

    def test_manual_button_unique_and_pine_preserved(self):
        self.assertEqual(MANUAL.count("if st.button('Scarica nuovo snapshot Cboe'"), 1)
        self.assertNotIn("render_manual_acquisition(", APP)
        self.assertNotIn("PINE DI EMERGENZA — NET GAMMA CORRENTE", APP)
        self.assertNotIn("🧪 SERAFINI LAB", APP)
        self.assertNotIn("render_research_lab(", APP)
        self.assertNotIn("render_serafini_lab_admin(", APP)
        self.assertNotIn("NON Gamma Cboe/OPRA", GAMMA)
        self.assertIn("(put_col, 'GAMMA PUT (−) · 25+25', -put_summary)", GAMMA)
        self.assertIn("color = '#f05252' if value < 0", GAMMA)
        self.assertIn("Esporta i profili su TradingView — 5 situazioni", GAMMA)
        self.assertNotIn("from v2_market_engine", APP)


if __name__ == "__main__":
    unittest.main()
