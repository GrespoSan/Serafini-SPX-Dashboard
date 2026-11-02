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

    def test_optional_visual_extension_cannot_block_valid_50(self):
        with patch("emergency_pine.visual_profile",
                   side_effect=ValueError("Estensione non utilizzabile")):
            profiles, _, _ = calculate_current_profiles(fixture(), TODAY)
        for kind in ("0DTE", "Weekly"):
            with self.subTest(kind=kind):
                self.assertEqual(profiles[kind]["display_rows"], profiles[kind]["rows"])
                self.assertEqual(len(profiles[kind]["rows"]), 50)

    def test_single_current_pine_has_no_historical_slot_labels(self):
        snap = fixture()
        code, profiles, _, _ = generate_current_pine(
            snap, TODAY, 40.0, snap["timestamp"], "Download manuale Roma")
        self.assertIn('indicator("Serafini SPX NET GAMMA | 2026-11-02 13:40 Roma"', code)
        self.assertNotIn('indicator("Serafini SPX NET GAMMA | 2026-11-02 | Download manuale Roma"', code)
        self.assertIn('renderProfile(odteLevels, odteValues, bar_index + offsetOdte, "0DTE 13:40 Roma"', code)
        self.assertIn('renderProfile(weeklyLevels, weeklyValues, bar_index + weeklyCenter, "Weekly 13:40 Roma"', code)
        self.assertIn("fuso non dichiarato", code)
        self.assertIn("assumendo UTC", code)
        self.assertIn("Timestamp originale CBOE", code)
        # I toggle "Mostra 0DTE corrente" restano invariati:
        # controlliamo soltanto le intestazioni disegnate sul grafico.
        self.assertNotIn('"0DTE corrente", actualScale', code)
        self.assertNotIn('"Weekly corrente", actualScale', code)
        self.assertIn("Differenziale ES-SPX GIA' APPLICATO agli strike: 40.0000", code)
        self.assertNotIn("07:30", code)
        self.assertNotIn("13:30", code)
        self.assertNotIn("15:50", code)
        self.assertNotIn("20:30", code)
        # Old public official exporter stays unchanged by the optional flag.
        official = make_pine(profiles["0DTE"], profiles["Weekly"], 40.0,
                             TODAY.isoformat(), snap["timestamp"],
                             "Mattina 07:30")
        self.assertIn('indicator("Serafini SPX NET GAMMA | 2026-11-02 | Mattina 07:30"', official)
        self.assertIn("0DTE 07:30", official)
        self.assertIn("Weekly 07:30", official)

    def test_label_tracks_actual_source_stamp_not_download_clock(self):
        snap = fixture()
        snap["timestamp"] = "2026-11-02 09:17:00"
        pine, _, _, _ = generate_current_pine(
            snap, TODAY, 40.0, snap["timestamp"],
            "Download manuale 2026-11-02 12:45 Roma")
        self.assertIn('indicator("Serafini SPX NET GAMMA | 2026-11-02 10:17 Roma"', pine)
        self.assertNotIn('indicator("Serafini SPX NET GAMMA | 2026-11-02 | Download manuale', pine)
        self.assertIn('"0DTE 10:17 Roma"', pine)
        self.assertIn('"Weekly 10:17 Roma"', pine)
        self.assertNotIn('"0DTE 12:45 Roma"', pine)
        self.assertIn("Download manuale 2026-11-02 12:45 Roma", pine)

    def test_cboe_clock_handles_italian_dst_transition(self):
        # Prima del 25 ottobre: CEST UTC+2; dopo: CET UTC+1.
        # L'orario del future USA non influenza la conversione UTC-Roma.
        cases = [
            (date(2026, 10, 23), "2026-10-23 14:50:00", "16:50"),
            (date(2026, 10, 30), "2026-10-30 14:50:00", "15:50"),
            (date(2026, 11, 2), "2026-11-02 14:49:21", "15:49"),
            (date(2026, 11, 2), "2026-11-02 08:28:04", "09:28"),
        ]
        for day, stamp, expected_clock in cases:
            with self.subTest(day=day, stamp=stamp):
                snapshot = fixture(day)
                snapshot["timestamp"] = stamp
                pine, _, _, _ = generate_current_pine(
                    snapshot, day, 40, stamp, "Importazione locale - ora download ignota")
                self.assertIn(
                    f'indicator("Serafini SPX NET GAMMA | {day} {expected_clock} Roma"',
                    pine,
                )
                self.assertIn(f'"0DTE {expected_clock} Roma"', pine)
                self.assertIn(f'"Weekly {expected_clock} Roma"', pine)
                self.assertNotIn(
                    f'indicator("Serafini SPX NET GAMMA | {day} | Importazione locale"',
                    pine,
                )

    def test_malformed_cboe_stamp_never_fabricates_label(self):
        snap = fixture()
        with self.assertRaisesRegex(ValueError, "Ora CBOE non valida"):
            generate_current_pine(snap, TODAY, 40.0, "2026-11-02 xx:yy:zz", "Manuale")

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
        self.assertLess(app.index("with st.expander('ANALISI NET GAMMA'"),
                        app.index("PINE DI EMERGENZA"))
        self.assertLess(app.index("with st.expander('REPLAY E CONFRONTO'"),
                        app.index("PINE DI EMERGENZA"))
        self.assertLess(app.index("with st.expander('REPORT GIORNALIERO AI'"),
                        app.index("PINE DI EMERGENZA"))
        self.assertNotIn("from storage import", emg)
        self.assertNotIn("Supabase", emg.replace("senza Supabase", "").replace(
            "Nessun Supabase", ""))  # no backend calls
        self.assertNotIn("gamma_replay", emg)
        self.assertNotIn("make_pine_comparison", emg)


if __name__ == "__main__":
    unittest.main()
