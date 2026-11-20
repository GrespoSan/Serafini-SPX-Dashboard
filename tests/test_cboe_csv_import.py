"""Cboe manual CSV: strict, provenance-preserving, no invented source fields."""
import csv
import hashlib
import io
import json
import unittest
import gzip

from cboe_csv_import import csv_to_snapshot, csv_coverage_warning, InvalidCboeCSV
from net_gamma import profile
from offline_snapshot import load_local_snapshot
from volatility_levels import weekly_expiry
from datetime import date

DATE = date(2026, 11, 18)
FIELDS = [
    "Expiration Date", "Calls", "Last Sale", "Net", "Bid", "Ask",
    "Volume", "IV", "Delta", "Gamma", "Open Interest", "Strike",
    "Puts", "Last Sale", "Net", "Bid", "Ask", "Volume", "IV",
    "Delta", "Gamma", "Open Interest"
]


def sample_csv(title="S&P 500 INDEX (^SPX)",
               date_label="November 18, 2026, 9:15 PM GMT+1",
               have_change=True, all_expiries=True):
    out = io.StringIO(newline="")
    wr = csv.writer(out)
    wr.writerow([title, "Last: 7,377.50"] +
                (["Change: -76.06"] if have_change else []))
    wr.writerow(["Date: " + date_label, "Bid: 7,377.4", "Ask: 7,377.8"])
    wr.writerow(FIELDS)
    expiries = [DATE, weekly_expiry(DATE)] if all_expiries else [DATE]
    for exp in expiries:
        for strike in range(7150, 7700, 5):
            ref = exp.strftime("%y%m%d")
            call = f"SPXW{ref}C{strike * 1000:08d}"
            put = f"SPXW{ref}P{strike * 1000:08d}"
            wr.writerow([
                f"Wed Nov 18 2026", call, "1", "0", "1", "2",
                "7", "0.2", "0.5", "0.0012", "27", str(strike),
                put, "1", "0", "1", "2", "5", "0.2",
                "-0.5", "0.0011", "25",
            ])
    return out.getvalue().encode("utf-8")


class TestCboeCSVImport(unittest.TestCase):
    def test_parse_november_2026_dashboard_and_provenance(self):
        raw = sample_csv()
        parsed = csv_to_snapshot(raw)
        self.assertEqual(parsed["symbol"], "_SPX")
        self.assertEqual(parsed["timestamp"], "2026-11-18 20:15:00")
        self.assertAlmostEqual(parsed["data"]["prev_day_close"], 7453.56)
        self.assertEqual(parsed["data"]["current_price"], 7377.50)
        self.assertEqual(len(parsed["data"]["options"]), 440)
        origin = parsed["_import_provenance"]
        self.assertEqual(origin["original_csv_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(origin["source"], "CBOE_CSV_MANUAL_USER_EXPORT")
        self.assertTrue(origin["not_original_cboe_json"])
        self.assertEqual(origin["contracts"], 440)

        local = load_local_snapshot("spx_manual.csv", raw)
        self.assertIsNotNone(local.import_provenance)
        snapshot = json.loads(gzip.decompress(local.download(local.object_path)))
        self.assertEqual(snapshot, parsed)
        for expiry in (DATE, weekly_expiry(DATE)):
            result = profile(snapshot, expiry, parsed["data"]["prev_day_close"])
            self.assertEqual(len(result["rows"]), 50)
            self.assertEqual(result["contracts"], 220)

    def test_friday_premarket_header_last_is_previous_close(self):
        # Cboe 20/11/2026 ore 09:04 Roma = 03:04 New York.
        # 7175.83 is the VERIFIED 19/11 close, NOT 7323.63 (18/11).
        raw = list(csv.reader(io.StringIO(sample_csv().decode("utf-8"))))
        raw[0] = ["S&P 500 INDEX (^SPX)", "Last: 7,175.83",
                  "Change: -147.80"]
        raw[1][0] = "Date: November 20, 2026, 9:04 AM GMT+1"
        out = io.StringIO(newline="")
        csv.writer(out).writerows(raw)
        result = csv_to_snapshot(out.getvalue().encode())
        self.assertEqual(result["timestamp"], "2026-11-20 08:04:00")
        self.assertEqual(result["data"]["current_price"], 7175.83)
        self.assertEqual(result["data"]["prev_day_close"], 7175.83)
        provenance = result["_import_provenance"]
        self.assertTrue(provenance["reference_is_preopen_last"])
        self.assertEqual(provenance["reference_market_day_ny"], "2026-11-20")
        self.assertIn("pre-apertura", provenance["previous_close_method"])

    def test_limited_near_money_chain_reports_missing_strikes(self):
        # Reconstruct an exporter that gives many expiries but only a few
        # strikes per expiry. NEVER synthesize the absent weekly levels.
        source = list(csv.reader(io.StringIO(sample_csv().decode("utf-8"))))
        source[0] = ["S&P 500 INDEX (^SPX)", "Last: 7,175.83",
                     "Change: -147.80"]
        source[1][0] = "Date: November 20, 2026, 9:04 AM GMT+1"
        original = source[3:]
        target0 = []
        targetW = []
        for strike in (7170, 7175, 7180, 7185):
            row = original[0][:]
            row[0] = "Fri Nov 20 2026"
            row[1] = f"SPXW261120C{strike*1000:08d}"
            row[11] = str(strike)
            row[12] = f"SPXW261120P{strike*1000:08d}"
            target0.append(row)
        for strike in (7165, 7170, 7175, 7180, 7185, 7190):
            row = original[0][:]
            row[0] = "Fri Nov 27 2026"
            row[1] = f"SPXW261127C{strike*1000:08d}"
            row[11] = str(strike)
            row[12] = f"SPXW261127P{strike*1000:08d}"
            targetW.append(row)
        # Keep other expiries to meet minimum file size/rows.
        everything = source[:3] + original[:110] + target0 + targetW
        buf = io.StringIO(newline="")
        csv.writer(buf).writerows(everything)
        result = csv_to_snapshot(buf.getvalue().encode())
        coverage = result["_import_provenance"]["coverage_by_expiry"]
        self.assertEqual((coverage["0DTE"]["below"], coverage["0DTE"]["above"]), (2,2))
        self.assertEqual((coverage["Weekly"]["below"], coverage["Weekly"]["above"]), (3,3))
        warning = csv_coverage_warning(result["_import_provenance"])
        self.assertIn("Options Range = All", warning)
        self.assertIn("0DTE 2026-11-20: 2/25 sotto, 2/25 sopra", warning)
        self.assertIn("Weekly 2026-11-27: 3/25 sotto, 3/25 sopra", warning)

    def test_snapshot_is_local_only(self):
        local = load_local_snapshot("download.csv", sample_csv())
        self.assertEqual(local.list("2026-W47/logs/"), [])
        self.assertEqual(len(local.list("2026-W47/snapshots/")), 1)
        with self.assertRaises(FileNotFoundError):
            local.download("2026-W47/snapshots/other.json.gz")

    def test_wrong_ticker_and_missing_change_rejected(self):
        with self.assertRaisesRegex(InvalidCboeCSV, "non SPX"):
            csv_to_snapshot(sample_csv(title="NASDAQ-100 INDEX (^NDX)"))
        with self.assertRaisesRegex(InvalidCboeCSV, "Last e Change"):
            csv_to_snapshot(sample_csv(have_change=False))

    def test_ambiguous_time_never_assumed_utc(self):
        with self.assertRaisesRegex(InvalidCboeCSV, "fuso"):
            csv_to_snapshot(sample_csv(date_label="November 18, 2026, 9:15 PM"))

    def test_et_offset_and_partial_export(self):
        data = csv_to_snapshot(sample_csv(
            date_label="November 18, 2026, 3:15 PM ET", all_expiries=False))
        self.assertEqual(data["timestamp"], "2026-11-18 20:15:00")
        self.assertEqual(len(data["data"]["options"]), 220)
        missing_weekly = profile(
            data, weekly_expiry(DATE), data["data"]["prev_day_close"])
        self.assertEqual(missing_weekly["contracts"], 0)
        self.assertEqual(missing_weekly["rows"], [])

    def test_missing_gamma_on_open_interest_is_not_zero_filled(self):
        src = sample_csv().decode("utf-8")
        # Replace the first call Gamma only, maintaining CSV 22-column structure.
        data = list(csv.reader(io.StringIO(src)))
        data[3][9] = ""
        buf = io.StringIO(newline="")
        wr = csv.writer(buf)
        wr.writerows(data)
        with self.assertRaisesRegex(InvalidCboeCSV, "Gamma"):
            csv_to_snapshot(buf.getvalue().encode())

    def test_duplicate_contract_rejected(self):
        data = list(csv.reader(io.StringIO(sample_csv().decode("utf-8"))))
        data.append(data[3][:])
        buf = io.StringIO(newline="")
        csv.writer(buf).writerows(data)
        with self.assertRaisesRegex(InvalidCboeCSV, "duplicato"):
            csv_to_snapshot(buf.getvalue().encode())


if __name__ == "__main__":
    unittest.main()
