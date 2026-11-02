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
                     "offline_snapshot.py"):
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
            "with st.expander('ANALISI DEL CONTESTO", 1)[1][:90])
        self.assertEqual(APP.count("st.file_uploader("), 1)
        self.assertIn('with st.expander("Acquisizione manuale / importazione JSON"',
                      APP)
        self.assertLess(APP.index("render_snapshot_selector(store, week, snaps, logs)"),
                        APP.index("render(store, week, snaps, logs, show_heading=False"))
        self.assertTrue(APP.index("with st.expander('ANALISI NET GAMMA'") <
                        APP.index('with st.expander("Acquisizione manuale / importazione JSON"'))

    def test_external_selector_exported(self):
        functions = {n.name for n in ast.parse(GAMMA).body
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertTrue({"render", "render_snapshot_selector"} <= functions)
        self.assertIn("selector_external=True", APP)
        self.assertIn("if selector_external:", GAMMA)

    def test_fallback_does_not_stop_on_402(self):
        self.assertIn("storage_unavailable = True", APP)
        self.assertIn("snaps, logs = [], []", APP)
        self.assertIn("if not offline and not storage_unavailable:", APP)
        self.assertIn("load_local_snapshot(uploaded_snapshot.name", APP)
        self.assertIn("Nessun dato inviato a Supabase", APP)

    def test_manual_button_unique_and_pine_preserved(self):
        self.assertEqual(MANUAL.count("if st.button('Scarica nuovo snapshot Cboe'"), 1)
        self.assertEqual(APP.count("render_manual_acquisition("), 1)
        self.assertIn("Esporta i profili su TradingView — 4 situazioni", GAMMA)
        self.assertNotIn("from v2_market_engine", APP)


if __name__ == "__main__":
    unittest.main()
