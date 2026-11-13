"""No-network regression of unattended Pine generator; never use future-day data."""
import tempfile
import unittest
from unittest.mock import patch
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from market_map_pine_autogen import generate, safe_source, get_news

ROME=ZoneInfo("Europe/Rome")
DAY=date(2026,11,6)
NOW=datetime(2026,11,6,21,0,tzinfo=ROME)

def group(expiry):
    rows=[{"strike_spx":7000 + i*5., "net_gamma":(-1 if i%2 else 1)*(100+i)} for i in range(50)]
    return {"expiry":expiry, "rows":rows, "display_rows":rows}

def src(slot):
    return {"origin":"UFFICIALE", "download_rome":f"2026-11-06T{slot}:00+01:00",
            "cboe_timestamp":"2026-11-06 15:00:00",
            "profiles":{"0DTE":group("2026-11-06"),
                        "Weekly":group("2026-11-13")}}

class PointInTime(unittest.TestCase):
    def test_current_public_calendar_api_verified_point_in_time(self):
        event = {"day": DAY, "clock": "14:30", "kind": "CPI",
                 "provider": "BLS", "source": "https://www.bls.gov/"}
        other = {**event, "day": date(2026, 11, 7)}
        with patch("macro_calendar.load_calendar",
                   return_value=([event, other],
                                 {"healthy": ["BLS", "BEA", "ISM"],
                                  "complete": True})):
            events, status = get_news(DAY)
            self.assertEqual(events, [event])
            self.assertEqual(status, "VERIFIED_AT_GENERATION")

    def test_partial_or_unavailable_calendar_fails_closed(self):
        event = {"day": DAY, "clock": "14:30", "kind": "CPI"}
        with patch("macro_calendar.load_calendar",
                   return_value=([event], {"healthy": ["BLS"],
                                           "complete": False})):
            news, status = get_news(DAY)
            self.assertEqual(news, [event])
            self.assertEqual(status, "PARTIAL_CALENDAR_UNVERIFIED")
            with tempfile.TemporaryDirectory() as tmp:
                result = generate(DAY, NOW, 25,
                                  {"20:30": src("20:30")},
                                  news, status, Path(tmp))
                self.assertEqual(result["produced"], [])
                self.assertEqual(
                    result["skipped"]["20:30"], "WAIT_NEWS_SOURCE_UNVERIFIED")
        with patch("macro_calendar.load_calendar",
                   side_effect=RuntimeError("provider not reachable")):
            self.assertEqual(get_news(DAY),
                             ([], "UNAVAILABLE_RuntimeError"))

    def test_four_slots(self):
        inputs={slot:src(slot) for slot in ("07:30","13:30","15:50","20:30")}
        with tempfile.TemporaryDirectory() as tmp:
            result=generate(DAY,NOW,25,inputs,[],"VERIFIED_AT_GENERATION",Path(tmp))
            self.assertEqual(len(result["produced"]),1)
            self.assertEqual(result["produced"][0]["slot"], "20:30")
            files=list(Path(tmp).glob("*.pine"))
            self.assertEqual(len(files),1)
            self.assertTrue(all('labelSizeChoice = input.string("Normale"' in f.read_text()
                                for f in files))
            self.assertEqual(result["news_status"],"VERIFIED_AT_GENERATION")
    def test_future_frame_not_exported(self):
        inputs={"07:30":src("07:30"),"20:30":src("20:30")}
        early=datetime(2026,11,6,8,0,tzinfo=ROME)
        with tempfile.TemporaryDirectory() as tmp:
            result=generate(DAY,early,25,inputs,[],"VERIFIED_AT_GENERATION",Path(tmp))
            self.assertEqual(len(result["produced"]),1)
            self.assertEqual(result["skipped"]["20:30"],"WAIT_SOURCE_OR_TIMESTAMP")
    def test_missing_source_yields_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            result=generate(DAY,NOW,25,{"07:30":src("07:30")},[],
                            "VERIFIED_AT_GENERATION",Path(tmp))
            self.assertEqual(len(result["produced"]),0)
            self.assertEqual(len(result["skipped"]),4)

if __name__ == "__main__":
    unittest.main()
