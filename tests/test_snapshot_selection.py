"""Regression for auto-follow and manual historical SPX snapshots (no secrets)."""
import ast
import json
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo


class FakeStore:
    def __init__(self, week):
        self.week = week
        self.snapshots = []
        self.logs = {}
        self.reads = []

    def add(self, name, event_id, hhmm, status="NUOVO"):
        if name not in self.snapshots:
            self.snapshots.append(name)
        self.logs[event_id] = {
            "status": status,
            "file": f"{self.week}/snapshots/{name}",
            "download_rome": f"2026-11-04T{hhmm}:00+01:00",
            "scheduled_slot": "manuale",
        }

    def list(self, prefix):
        if prefix.endswith("/snapshots/"):
            return [{"name": name} for name in self.snapshots]
        if prefix.endswith("/logs/"):
            return [{"name": name} for name in self.logs]
        return []

    def download(self, path):
        self.reads.append(path)
        return json.dumps(self.logs[path.rsplit("/", 1)[-1]]).encode()


def load_functions(fake_st):
    """Compile actual production functions without importing chart dependencies."""
    path = Path(__file__).resolve().parents[1] / "gamma_section.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    expected = {
        "_snapshot_log_index", "_latest_downloaded_snapshot",
        "_sync_snapshot_selection", "_snapshot_choice_changed",
    }
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in expected:
            node.decorator_list = []
            selected.append(node)
    assert {node.name for node in selected} == expected
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    namespace = {
        "st": fake_st, "datetime": datetime, "json": json, "ZoneInfo": ZoneInfo,
    }
    exec(compile(module, str(path), "exec"), namespace)
    return namespace


class SnapshotSelectionTests(unittest.TestCase):
    def setUp(self):
        self.week = "2026-W45"
        self.st = SimpleNamespace(session_state={})
        self.fn = load_functions(self.st)
        self.store = FakeStore(self.week)
        # CBOE filename order intentionally differs from acquisition order.
        self.morning = "SPX_CBOE_20261104_145959.json.gz"
        self.afternoon = "SPX_CBOE_20261104_144921.json.gz"
        self.evening = "SPX_CBOE_20261104_190000.json.gz"
        self.store.add(self.morning, "log1", "07:30")
        self.store.add(self.afternoon, "log2", "15:50")

    def select(self):
        return self.fn["_sync_snapshot_selection"](
            self.store, self.week,
            self.store.list(f"{self.week}/snapshots/"),
            self.store.list(f"{self.week}/logs/"),
        )

    def test_default_follows_log_time_not_filename(self):
        names, latest = self.select()
        self.assertEqual(latest, self.afternoon)
        self.assertEqual(
            self.st.session_state[f"serafini_selected_snapshot_{self.week}"],
            self.afternoon,
        )
        self.assertEqual(names[0], self.morning)
        self.assertTrue(all("/logs/" in p for p in self.store.reads))

    def test_historical_choice_persists_until_reenabled(self):
        _, latest = self.select()
        self.st.session_state[f"serafini_selected_snapshot_{self.week}"] = self.morning
        self.fn["_snapshot_choice_changed"](self.week, latest)
        self.assertFalse(self.st.session_state[f"serafini_follow_latest_{self.week}"])
        self.store.add(self.evening, "log3", "20:30")
        self.select()
        self.assertEqual(
            self.st.session_state[f"serafini_selected_snapshot_{self.week}"],
            self.morning,
        )
        self.st.session_state[f"serafini_follow_latest_{self.week}"] = True
        self.select()
        self.assertEqual(
            self.st.session_state[f"serafini_selected_snapshot_{self.week}"],
            self.evening,
        )

    def test_no_scheduled_snapshot_or_edge_refresh(self):
        root = Path(__file__).resolve().parents[1]
        for filename in ("app.py", "gamma_section.py", "edge_triptych.py"):
            with self.subTest(filename=filename):
                source = (root / filename).read_text(encoding="utf-8")
                self.assertNotIn("watch_new_snapshot", source)
                self.assertNotIn("run_every=", source)

    def test_refresh_latest_restores_auto_mode(self):
        self.select()
        self.st.session_state[f"serafini_selected_snapshot_{self.week}"] = self.morning
        self.st.session_state[f"serafini_follow_latest_{self.week}"] = False
        self.st.session_state[f"spx_refresh_latest_{self.week}"] = True
        self.select()
        self.assertTrue(self.st.session_state[f"serafini_follow_latest_{self.week}"])
        self.assertEqual(
            self.st.session_state[f"serafini_selected_snapshot_{self.week}"],
            self.afternoon,
        )


if __name__ == "__main__":
    unittest.main()
