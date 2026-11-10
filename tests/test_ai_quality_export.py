"""AI export v10: Replay volume alerts + Cboe provenance, no false UTC."""
import csv
import io
import unittest
from copy import deepcopy
from datetime import date

from ai_quality_export import (
    merge_volume_alerts, attach_freshness_audit, snapshot_freshness,
    volume_alerts_csv, freshness_csv,
)


def report_fixture():
    return {
        "volume_integrity": {
            "status": "NESSUNA ALLERTA DA SOGLIA", "alert_count": 0,
            "alerts": [], "possible_us_open_reset_count": 0,
            "possible_us_open_resets": [],
        },
        "market_context": {
            "previous_daily_range": {
                "source": "Massive Futures REST · ESZ6 · 1session"
            },
        },
        "snapshots": [
            {
                "file": "2026-W46/snapshots/SPX_CBOE_20261110_025842.json.gz",
                "download_utc": "2026-11-10T06:30:45+00:00",
                "cboe_timestamp_original": "2026-11-10 02:58:42",
            },
            {
                "file": "2026-W46/snapshots/SPX_CBOE_20261110_144922.json.gz",
                "download_utc": "2026-11-10T14:50:11+00:00",
                "cboe_timestamp_original": "2026-11-10 14:49:22",
            },
        ],
        "intraday_replay": {
            "status": "DISPONIBILE",
            "timeline": [
                {
                    "slot_rome": "14:31",
                    "source": "STUDIO_INTRADAY_NON_UFFICIALE",
                    "download_rome": "2026-11-10T14:31:30+01:00",
                    "cboe_timestamp_original": "2026-11-10 13:30:45",
                },
                {
                    "slot_rome": "15:50",
                    "source": "UFFICIALE",
                    "download_rome": "2026-11-10T15:50:11+01:00",
                    "cboe_timestamp_original": "2026-11-10 14:49:22",
                },
            ],
            "transitions": [
                {
                    "from_slot": "14:31", "to_slot": "15:50",
                    "profiles": {
                        "0DTE": {
                            "volume_audit": {
                                "alert": True,
                                "status": "ALLERTA COMPARABILITA VOLUME",
                                "volume_initial": 228557.0,
                                "volume_final": 53179.0,
                                "volume_change_pct": -76.73,
                                "oi_change_pct": 0.0,
                            }
                        },
                        "Weekly": {
                            "volume_audit": {
                                "alert": True,
                                "status": "ALLERTA COMPARABILITA VOLUME",
                                "volume_initial": 65366.0,
                                "volume_final": 1588.0,
                                "volume_change_pct": -97.57,
                                "oi_change_pct": 0.0,
                            }
                        },
                    },
                },
                {
                    "from_slot": "15:50", "to_slot": "16:01",
                    "profiles": {
                        "0DTE": {"volume_audit": {
                            "alert": False, "status": "NESSUNA ALLERTA DA SOGLIA"
                        }},
                        "Weekly": {"volume_audit": {
                            "status": "VOLUME/OI NON DISPONIBILI NEL COMPACT",
                            "alert": False,
                        }},
                    },
                },
            ],
        },
        "warnings": [],
    }


class AiQualityTests(unittest.TestCase):
    def test_10_november_replay_alerts_aggregated_not_silenced(self):
        rep = report_fixture()
        before = deepcopy(rep["intraday_replay"]["transitions"])
        summary = merge_volume_alerts(rep)
        self.assertEqual(summary["alert_count"], 2)
        self.assertEqual(summary["official_alert_count"], 0)
        self.assertEqual(summary["replay_alert_count"], 2)
        self.assertEqual(summary["status"], "ALLERTA")
        self.assertEqual(summary["replay_comparisons_unavailable"], 1)
        self.assertEqual([a["profile"] for a in summary["alerts"]],
                         ["0DTE", "Weekly"])
        self.assertTrue(all(a["scope"] == "REPLAY" for a in summary["alerts"]))
        self.assertEqual(rep["intraday_replay"]["transitions"], before)
        self.assertTrue(any("ALLERTA VOLUME REPLAY" in x for x in rep["warnings"]))

    def test_official_and_replay_alerts_remain_distinct(self):
        report = report_fixture()
        report["volume_integrity"]["alerts"].append({
            "initial_file": "A", "final_file": "B", "profile": "0DTE",
            "audit": {"alert": True, "volume_initial": 200,
                      "volume_final": 20, "volume_change_pct": -90.,
                      "oi_change_pct": 0.},
        })
        res = merge_volume_alerts(report)
        self.assertEqual((res["official_alert_count"], res["replay_alert_count"],
                          res["alert_count"]), (1, 2, 3))
        self.assertEqual([a["scope"] for a in res["alerts"]],
                         ["UFFICIALE", "REPLAY", "REPLAY"])

    def test_naive_cboe_timestamp_never_assumed_utc(self):
        info = snapshot_freshness("2026-11-10T06:30:45+00:00",
                                  "2026-11-10 02:58:42")
        self.assertEqual(info["status"], "FUSO_CBOE_NON_DICHIARATO")
        self.assertIsNone(info["cboe_age_minutes"])
        self.assertIsNone(info["cboe_timestamp_rome_verified"])
        self.assertEqual(info["download_rome"],
                         "2026-11-10T07:30:45+01:00")

    def test_timezone_explicit_is_safely_calculated_in_minutes(self):
        info = snapshot_freshness("2026-11-10T15:50:00+01:00",
                                  "2026-11-10T14:49:00+00:00")
        self.assertEqual(info["status"], "ETA_VERIFICABILE")
        self.assertEqual(info["cboe_age_minutes"], 1.0)
        self.assertEqual(info["cboe_timestamp_rome_verified"],
                         "2026-11-10T15:49:00+01:00")

    def test_repeated_cboe_timestamp_flagged_without_assumed_age(self):
        info = snapshot_freshness("2026-11-10T13:30:00+01:00",
                                  "2026-11-10 10:00:00",
                                  previous_cboe="2026-11-10 10:00:00")
        self.assertTrue(info["cboe_timestamp_repeated_from_previous"])
        self.assertIsNone(info["cboe_age_minutes"])

    def test_freshness_counts_and_source_are_explicit(self):
        r = report_fixture()
        summary = attach_freshness_audit(r)
        self.assertEqual(summary["official"]["records"], 2)
        self.assertEqual(summary["official"]["timezone_undeclared"], 2)
        self.assertEqual(summary["replay_timeline"]["timezone_undeclared"], 2)
        self.assertEqual(summary["cboe_timezone_assumption"], "NESSUNA")
        self.assertIn("Massive", summary["futures_daily_source"])
        self.assertEqual(r["snapshots"][0]["freshness"]["download_rome"],
                         "2026-11-10T07:30:45+01:00")

    def test_csvs_preserve_scopes_and_timestamp_raw(self):
        r = report_fixture()
        merge_volume_alerts(r)
        attach_freshness_audit(r)
        volume = list(csv.DictReader(io.StringIO(
            volume_alerts_csv(r).decode("utf-8-sig"))))
        self.assertEqual(len(volume), 2)
        self.assertEqual(volume[0]["da_slot"], "14:31")
        self.assertEqual(volume[1]["profilo"], "Weekly")
        fresh = list(csv.DictReader(io.StringIO(
            freshness_csv(r).decode("utf-8-sig"))))
        self.assertEqual(len(fresh), 4)
        self.assertEqual(fresh[0]["timestamp_cboe_originale"],
                         "2026-11-10 02:58:42")
        self.assertEqual(fresh[0]["eta_minuti_se_verificabile"], "")
        self.assertEqual(fresh[0]["stato"], "FUSO_CBOE_NON_DICHIARATO")

    def test_unavailable_replay_is_not_silent_proof_of_no_anomalies(self):
        r = report_fixture()
        r["intraday_replay"] = {"status": "NON DISPONIBILE"}
        summary = merge_volume_alerts(r)
        self.assertEqual(summary["alert_count"], 0)
        self.assertIn("Copertura Replay non disponibile", summary["scope_note"])


if __name__ == "__main__":
    unittest.main()
