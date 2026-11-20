"""Maintenance gate: default PAUSED, explicit Streamlit Secret for resume."""
import ast
import unittest
from pathlib import Path

from app_maintenance import (
    DEFAULT_MAINTENANCE_MODE, MAINTENANCE_MESSAGE,
    maintenance_enabled, suspend_if_enabled,
)

ROOT = Path(__file__).resolve().parents[1]


class SecretsUnavailable:
    def get(self, *_args, **_kwargs):
        raise FileNotFoundError("Streamlit secrets missing")


class Stopped(BaseException):
    pass


class FakeStreamlit:
    def __init__(self, secrets):
        self.secrets = secrets
        self.events = []

    def title(self, text):
        self.events.append(("title", text))

    def warning(self, text):
        self.events.append(("warning", text))

    def info(self, text):
        self.events.append(("info", text))

    def caption(self, text):
        self.events.append(("caption", text))

    def stop(self):
        self.events.append(("stop", None))
        raise Stopped()


class MaintenanceTests(unittest.TestCase):
    def test_default_is_paused_without_secret(self):
        self.assertTrue(DEFAULT_MAINTENANCE_MODE)
        self.assertTrue(maintenance_enabled({}))
        self.assertTrue(maintenance_enabled(SecretsUnavailable()))

    def test_toggle_accepts_only_explicit_boolean_or_boolean_string(self):
        for val in (False, "false", " FALSE "):
            with self.subTest(val=val):
                self.assertFalse(maintenance_enabled({"MAINTENANCE_MODE": val}))
        for val in (True, "true", "garbage", "", 0, 1, None):
            with self.subTest(val=val):
                self.assertTrue(maintenance_enabled({"MAINTENANCE_MODE": val}))

    def test_maintenance_stops_before_data_or_login(self):
        fake = FakeStreamlit({})
        with self.assertRaises(Stopped):
            suspend_if_enabled(fake, "Serafini · test")
        self.assertEqual([name for name, _ in fake.events],
                         ["title", "warning", "info", "caption", "stop"])
        self.assertIn("Nessun archivio", MAINTENANCE_MESSAGE)

    def test_resumed_ui_does_not_block(self):
        fake = FakeStreamlit({"MAINTENANCE_MODE": False})
        self.assertIs(suspend_if_enabled(fake, "Serafini · test"), False)
        self.assertEqual(fake.events, [])

    def test_gate_runs_before_any_market_storage_or_cache_work(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        ast.parse(source)
        check = source.index('suspend_if_enabled(st, ')
        for needle in ("from storage import Store", "from markets import MARKETS",
                       "purge_expired()", "st.title(", "st.secrets["):
            if needle in source:
                self.assertLess(check, source.index(needle), needle)


if __name__ == "__main__":
    unittest.main()
