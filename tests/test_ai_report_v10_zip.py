"""Smoke test full PDF/ZIP export and untouched Serafini buffer logic."""
import io
import json
import unittest
import zipfile
from datetime import date

from ai_report import export_zip, pdf_summary
from ai_quality_export import (
    merge_volume_alerts, attach_freshness_audit,
)


def fixture():
    return {
        "report_date": "2026-11-10",
        "schema_version": "serafini_spx_ai_report_v10",
        "parameters": {
            "reference_spx_previous_close": 7550.0,
            "es_minus_spx": 23.0,
        },
        "market_context": {
            "vix_curve": {"status": "NON DISPONIBILE"},
            "previous_daily_range": {
                "status": "VALIDO", "day": "2026-11-09",
                "ticker": "ESZ6", "source": "Massive Futures REST · 1session",
                "high": 7600.5, "low": 7363.75, "close": 7580.25,
                "range_points": 236.75, "range_pct": 3.123248,
            },
            "asia_session": {"status": "NON DISPONIBILE"},
            "breakout_buffer": {"status": "DETERMINATO",
                                "buffer_es_points": 15,
                                "reason": "Backwardation VX"},
        },
        "snapshots": [{
            "file": "2026-W46/snapshots/SPX_CBOE_20261110_144922.json.gz",
            "status": "VALIDO", "acquisition_slot": "50 15 * * 1-5",
            "download_rome": "10/11/2026 15:50:11",
            "download_utc": "2026-11-10T14:50:11+00:00",
            "cboe_timestamp_original": "2026-11-10 14:49:22",
            "profiles": {
                "0DTE": {"main_concentrations_by_absolute_net": []},
                "Weekly": {"main_concentrations_by_absolute_net": []},
            },
        }],
        "pairwise_changes": [],
        "intraday_replay": {
            "status": "DISPONIBILE",
            "timeline": [],
            "official_count_in_timeline": 0,
            "study_count_in_timeline": 0,
            "missing_slots": [],
            "post_expiry_0dte_slots": [],
            "transitions": [{
                "from_slot": "14:31", "to_slot": "15:50",
                "profiles": {
                    "0DTE": {"volume_audit": {
                        "alert": True,
                        "status": "ALLERTA COMPARABILITA VOLUME",
                        "volume_initial": 228557.0,
                        "volume_final": 53179.0,
                        "volume_change_pct": -76.73,
                        "oi_change_pct": 0.0,
                    }},
                    "Weekly": {"volume_audit": {
                        "alert": True,
                        "status": "ALLERTA COMPARABILITA VOLUME",
                        "volume_initial": 65366.0,
                        "volume_final": 1588.0,
                        "volume_change_pct": -97.57,
                        "oi_change_pct": 0.0,
                    }},
                },
            }],
        },
        "us_session_clock": {},
        "volume_integrity": {
            "status": "NESSUNA ALLERTA DA SOGLIA", "alert_count": 0,
            "alerts": [], "possible_us_open_resets": [],
        },
        "warnings": [],
    }


class AiReportZipTests(unittest.TestCase):
    def test_pdf_and_zip_include_replay_volume_alerts_and_freshness(self):
        report = fixture()
        volume = merge_volume_alerts(report)
        attach_freshness_audit(report)
        self.assertEqual(volume["alert_count"], 2)
        pdf = pdf_summary(report)
        self.assertTrue(pdf.startswith(b"%PDF-"))
        blob = export_zip(report)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            files = set(z.namelist())
            expected = {
                "SPX_AI_2026-11-10.json",
                "SPX_Report_2026-11-10.pdf",
                "SPX_Allerte_Volume_2026-11-10.csv",
                "SPX_Freschezza_Dati_2026-11-10.csv",
                "LEGGIMI.txt",
            }
            self.assertEqual(files, expected)
            self.assertEqual(pdf, z.read("SPX_Report_2026-11-10.pdf"))
            original = json.loads(z.read("SPX_AI_2026-11-10.json"))
            self.assertEqual(original["volume_integrity"]["alert_count"], 2)
            self.assertEqual(original["volume_integrity"]["replay_alert_count"], 2)
            self.assertEqual(original["volume_integrity"]["official_alert_count"], 0)
            self.assertEqual(original["market_context"]["breakout_buffer"]
                             ["buffer_es_points"], 15)
            self.assertIn("Massive", original["market_context"]
                          ["previous_daily_range"]["source"])
            self.assertIn("14:31", z.read("SPX_Allerte_Volume_2026-11-10.csv")
                          .decode("utf-8-sig"))
            self.assertIn("FUSO_CBOE_NON_DICHIARATO",
                          z.read("SPX_Freschezza_Dati_2026-11-10.csv")
                          .decode("utf-8-sig"))

    def test_backwardation_still_produces_15_even_with_green_daily(self):
        from vix_regime import combined_breakout_buffer
        output = combined_breakout_buffer(
            contango_pct=-2.57182981743308,
            daily_range_pct=3.123248, daily_candle_red=False,
        )
        self.assertEqual(output["buffer_es_points"], 15)
        self.assertIn("Backwardation", output["reason"])


if __name__ == "__main__":
    unittest.main()
