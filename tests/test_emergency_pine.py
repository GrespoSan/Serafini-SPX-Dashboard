"""SPX Emergency Pine: tests on synthetic CBOE snapshots, no real APIs.

Guards the single-snapshot/current-only design and Serafini parity.
"""
from datetime import date
import gzip
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from emergency_pine import (
    CBOE_URL, calculate_current_profiles, download_current_snapshot,
    generate_current_pine, use_uploaded_snapshot,
)
from net_gamma import profile
from offline_snapshot import load_local_snapshot
from pine_export import make_pine
from volatility_levels import weekly_expiry

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 2)


def fixture(day=TODAY):
    expiries = (day, weekly_expiry(day))
    entries = []
    for expiry in expiries:
        stamp = expiry.strftime("%y%m%d")
        for strike in range(7200, 7750, 5):
            for kind, gamma, oi, vol in (
                ("C", 0.20, 15, 3), ("P", 0.08, 11, 2)
            ):
                entries.append({
                    "option": f"SPXW{stamp}{kind}{strike * 1000:08d}",
                    "gamma": gamma, "open_interest": oi, "volume": vol,
                })
    return {
        "symbol": "_SPX", "timestamp": "2026-11-02 12:40:00",
        "data": {"prev_day_close": 7480.0, "current_price": 7491.0,
                 "options": entries},
    }


class CBOEResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit):
        self.limit = limit
        return self.data


class EmergencyPineTests(unittest.TestCase):
    def test_serafini_25_plus_25_and_visual_extension_parity(self):
        snap = fixture()
        profiles, reference, spot = calculate_current_profiles(snap, TODAY)
        self.assertEqual((reference, spot), (7480, 7491))
        for kind, expiry in (("0DTE", TODAY), ("Weekly", weekly_expiry(TODAY))):
            with self.subTest(kind=kind):
                direct = profile(snap, expiry, reference)
                self.assertEqual(profiles[kind]["rows"], direct["rows"])
                self.assertEqual(len(direct["rows"]), 50)
                self.assertGreaterEqual(len(profiles[kind]["display_rows"]), 50)
                self.assertLessEqual(len(profiles[kind]["display_rows"]), 140)

    def test_single_current_pine_has_no_historical_slot_labels(self):
        snap = fixture()
        code, profiles, _, _ = generate_current_pine(
            snap, TODAY, 40.0, snap["timestamp"], "Download manuale Roma")
        self.assertIn("0DTE corrente", code)
        self.assertIn("Weekly corrente", code)
        self.assertIn("Differenziale ES-SPX GIA' APPLICATO agli strike: 40.0000", code)
        self.assertNotIn("07:30", code)
        self.assertNotIn("13:30", code)
        self.assertNotIn("15:50", code)
        self.assertNotIn("20:30", code)
        # Old public official exporter stays unchanged by the optional flag.
        official = make_pine(profiles["0DTE"], profiles["Weekly"], 40.0,
                             TODAY.isoformat(), snap["timestamp"],
                             "Mattina 07:30")
        self.assertIn("0DTE 07:30", official)
        self.assertIn("Weekly 07:30", official)

    def test_no_basis_never_silently_becomes_zero(self):
        with self.assertRaises((TypeError, ValueError)):
            generate_current_pine(fixture(), TODAY, None, "", "")
        with self.assertRaises(ValueError):
            generate_current_pine(fixture(), TODAY, float("nan"), "", "")

    def test_invalid_weekly_cannot_generate_pine(self):
        snap = fixture()
        snap["data"]["options"] = [
            row for row in snap["data"]["options"]
            if "261106" not in row["option"]
        ]
        with self.assertRaises(ValueError):
            generate_current_pine(snap, TODAY, 30, snap["timestamp"], "Manuale")

    def test_download_direct_to_cboe_and_no_supabase(self):
        raw = json.dumps(fixture()).encode()
        with patch("emergency_pine.urlopen", return_value=CBOEResponse(raw)) as fake:
            record = download_current_snapshot()
        self.assertEqual(fake.call_args.args[0].full_url, CBOE_URL)
        self.assertEqual(record["source"], "Download manuale CBOE")
        self.assertEqual(json.loads(gzip.decompress(record["content"])), fixture())
        self.assertNotIn("SUPABASE_", str(record))

    def test_zip_json_can_be_imported_without_store_credentials(self):
        raw = json.dumps(fixture()).encode()
        record = use_uploaded_snapshot("_SPX.json", raw)
        self.assertIsNone(record["download_rome"])
        store = load_local_snapshot(record["name"], record["content"])
        self.assertEqual(store.timestamp, fixture()["timestamp"])

    def test_app_layout_and_no_legacy_cross_calls(self):
        app = (ROOT / "app.py").read_text()
        emg = (ROOT / "emergency_pine.py").read_text()
        self.assertIn("PINE DI EMERGENZA — NET GAMMA CORRENTE", app)
        self.assertIn("render_emergency_pine()", app)
        self.assertLess(app.index("context_slot = st.empty()"),
                        app.index("PINE DI EMERGENZA"))
        self.assertLess(app.index("PINE DI EMERGENZA"),
                        app.index("with st.expander('ANALISI NET GAMMA'"))
        self.assertNotIn("from storage import", emg)
        self.assertNotIn("Supabase", emg.replace("senza Supabase", "").replace(
            "Nessun Supabase", ""))  # no backend calls
        self.assertNotIn("gamma_replay", emg)
        self.assertNotIn("make_pine_comparison", emg)


if __name__ == "__main__":
    unittest.main()
