"""GEX Edge Replay: no mixing dates, slots, snapshots, or model versions."""
import json
import unittest
from copy import deepcopy
from datetime import date

from edge_replay_data import list_edge_slots, validate_pair, read_pair, same_model

DAY = date(2026, 11, 9)
SHA = "a" * 64


def compact():
    return {
        "schema": "serafini_replay_v1",
        "rome_date": DAY.isoformat(),
        "slot_rome": "16:31",
        "download_rome": "2026-11-09T16:31:12+01:00",
        "cboe_timestamp_original": "2026-11-09 15:30:50",
        "cboe_sha256": SHA,
        "reference_spx": 7450,
        "spot_spx": 7500,
        "profiles": {"0DTE": {"rows": [
            {"strike_spx": 7480, "net_gamma": 2.5}
        ]}},
    }


def sidecar():
    return {
        "schema": "serafini_edge_replay_v1",
        "source_kind": "STUDIO_INTRADAY_NON_UFFICIALE",
        "rome_date": DAY.isoformat(),
        "slot_rome": "16:31",
        "download_rome": "2026-11-09T16:31:12+01:00",
        "cboe_timestamp_original": "2026-11-09 15:30:50",
        "cboe_sha256": SHA,
        "reference_spx": 7450,
        "spot_spx": 7500,
        "edge_engine_commit": "0f32354e63dba8650a6510219fa00eb5520f9b77",
        "status": "VALIDATO",
        "edge": {
            "schema": "edge-gex-context-v1",
            "symbol": "SPX", "mode": "0DTE", "expiries": [DAY.isoformat()],
            "sha256": SHA, "spot_spx": 7500.0, "quality_pct": 99.5,
            "quality_sufficient": True, "model_version": "pinned-model",
            "call_gex": 10e9, "put_gex": -12e9, "net_gex": -2e9,
            "gross_gex": 22e9,
            "rows": [{"strike_spx": 7490, "call_gex": 5, "put_gex": -8, "net_gex": -3}],
            "curve": [],
        },
    }


class FakeStore:
    def __init__(self, files):
        self.files = files

    def download(self, name):
        return self.files[name]

    def list(self, path):
        return [{"name": name.split("/")[-1]} for name in self.files if name.startswith(path)]


class ReplayEdgeDataTests(unittest.TestCase):
    def test_exact_match_is_accepted(self):
        report, clock = validate_pair(compact(), sidecar(), DAY, "16:31")
        self.assertEqual(report["net_gex"], -2e9)
        self.assertEqual(clock.strftime("%H:%M:%S"), "16:31:12")

    def test_sha_is_mandatory_and_must_match_both_reports(self):
        variants = [
            (lambda c, s: c.update(cboe_sha256="b"*64)),
            (lambda c, s: s.update(cboe_sha256="b"*64)),
            (lambda c, s: s["edge"].update(sha256="b"*64)),
            (lambda c, s: c.pop("cboe_sha256")),
        ]
        for change in variants:
            c, s = compact(), sidecar()
            change(c, s)
            with self.assertRaises(ValueError):
                validate_pair(c, s, DAY, "16:31")

    def test_expiry_download_slot_spot_are_strict(self):
        modifications = [
            lambda c, s: s["edge"].update(expiries=["2026-11-10"]),
            lambda c, s: s.update(download_rome="2026-11-09T16:46:12+01:00"),
            lambda c, s: c.update(slot_rome="16:46"),
            lambda c, s: s.update(spot_spx=7400),
            lambda c, s: s["edge"].update(spot_spx=7400),
        ]
        for change in modifications:
            c, s = compact(), sidecar()
            change(c, s)
            with self.assertRaises(ValueError):
                validate_pair(c, s, DAY, "16:31")

    def test_quality_fail_closed_without_silent_gex_validation(self):
        c, s = compact(), sidecar()
        s["edge"]["quality_sufficient"] = False
        with self.assertRaisesRegex(ValueError, "Qualità"):
            validate_pair(c, s, DAY, "16:31")
        s["status"] = "QUALITA_INSUFFICIENTE"
        report, _ = validate_pair(c, s, DAY, "16:31")
        self.assertIs(report["quality_sufficient"], False)

    def test_select_only_available_slots_from_same_day(self):
        week = "2026-W46"
        c, s = compact(), sidecar()
        store = FakeStore({
            "replay-edge/2026-W46/2026-11-09_16-31.json": json.dumps(s).encode(),
            "replay-edge/2026-W46/2026-11-09_14-31.json": b"{}",
            "replay-edge/2026-W46/2026-11-10_14-31.json": b"{}",
            "replay/2026-W46/2026-11-09_16-31.json": json.dumps(c).encode(),
        })
        self.assertEqual(
            [s for s, _ in list_edge_slots(store, week, DAY)],
            ["14:31", "16:31"],
        )
        got_compact, got_sidecar, report, clock = read_pair(
            store, week, DAY, "16:31")
        self.assertEqual(got_compact["cboe_sha256"], SHA)
        self.assertEqual(got_sidecar["edge"]["net_gex"], report["net_gex"])
        self.assertEqual(clock.hour, 16)

    def test_compare_requires_same_model_and_validated_quality(self):
        a = sidecar()
        b = deepcopy(a)
        self.assertTrue(same_model(a, b))
        b["edge"]["model_version"] = "another-model"
        self.assertFalse(same_model(a, b))
        b = deepcopy(a)
        b["edge"]["quality_sufficient"] = False
        self.assertFalse(same_model(a, b))


if __name__ == "__main__":
    unittest.main()
