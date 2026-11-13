"""SPX refresh: both roles, no password prompt, no global cache purge."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from spx_refresh import refresh_current_data


ROOT = Path(__file__).resolve().parents[1]


class SidebarRefreshTests(unittest.TestCase):
    def test_refresh_clears_only_current_data_caches(self):
        modules = {}
        names = {
            "storage": ("_cached_list",),
            "edge_latest_source": ("_latest_verified_replay",),
            "edge_replay_panel": ("_cached_slots", "_latest_es_quote"),
            "replay_prices": ("_yahoo_candles", "_massive_candles"),
            "vix_spot_history": ("_download_vix_history",),
            "macro_calendar": ("load_calendar",),
        }
        clears = []
        for module, attrs in names.items():
            fake = types.ModuleType(module)
            for attr in attrs:
                callback = Mock()
                setattr(fake, attr, types.SimpleNamespace(clear=callback))
                clears.append(callback)
            modules[module] = fake
        with patch.dict(sys.modules, modules):
            refresh_current_data()
        for fn in clears:
            fn.assert_called_once_with()

    def test_button_inside_authenticated_shared_sidebar_not_admin_branch(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        start = source.index("role = st.session_state.role\nwith st.sidebar:")
        end = source.index("st.caption('Snapshot SPX archiviati", start)
        sidebar = source[start:end]
        self.assertIn("st.button('🔄 Aggiorna dati'", sidebar)
        self.assertIn("from spx_refresh import refresh_current_data", sidebar)
        self.assertIn("refresh_current_data()", sidebar)
        self.assertLess(sidebar.index("Aggiorna dati"), sidebar.index("st.button('Esci')"))
        self.assertNotIn("if role == 'admin':", sidebar)
        self.assertNotIn("st.session_state.clear", sidebar)
        self.assertNotIn("st.session_state.pop('role'", sidebar.split("if st.button('Esci')")[0])
        self.assertNotIn("st.rerun()", sidebar.split("if st.button('Esci')")[0])

    def test_refresh_never_clears_login_or_immutable_cboe_cache(self):
        source = (ROOT / "spx_refresh.py").read_text(encoding="utf-8")
        self.assertNotIn("st.session_state", source)
        self.assertNotIn("st.cache_data.clear", source)
        self.assertNotIn("login_limiter", source)
        self.assertNotIn("_cached_download", source)
        self.assertNotIn("_selected_snapshot", source)
        self.assertNotIn("_cached_official_snapshot", source)


if __name__ == "__main__":
    unittest.main()
