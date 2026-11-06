"""VIX spot previous close KPI: official Cboe, 4-column dark layout, fail open.

No network requests: fixture CSV and mock Cboe source. The VX futures
backwardation and ES Daily buffer formula must be byte-for-byte unchanged.
"""
from contextlib import nullcontext
from datetime import date
from unittest.mock import patch
import unittest

import vix_regime
from vix_spot_history import _parse_close_history


CSV_DATA = """Cboe VIX Index Historical Prices
DATE,OPEN,HIGH,LOW,CLOSE
10/30/2026,18.3,19.2,17.5,19.42
11/03/2026,21.66,21.91,20.29,20.83
11/04/2026,19.20,19.22,16.57,16.61
11/05/2026,19.88,29.75,19.22,28.18
11/06/2026,20.0,20.5,20.0,20.3
"""


def scenario():
    return {
        "vix_curve":{
            "status":"VALIDO","regime":"BACKWARDATION",
            "contango_pct":-9.55,"day":"2026-11-05"},
        "previous_daily_range":{
            "status":"VALIDO","day":"2026-11-05","ticker":"ESZ6",
            "range_points":392.75,"range_pct":5.34,
            "range_trend":"ESPANSIONE","previous_range_pct":3.31,
            "change_pct":-4.93,
            "open":7600.0,"close":7404.50},
        "breakout_buffer":{
            "buffer_es_points":15,"reason":"Backwardation VX"},
    }


class FakeStreamlit:
    def __init__(self):
        self.html=[]
        self.caption_messages=[]
    def markdown(self, value, unsafe_allow_html=False):
        self.html.append((value,unsafe_allow_html))
    def expander(self, *args, **kwargs):
        return nullcontext()
    def caption(self,value):
        self.caption_messages.append(value)


class VixSpotTests(unittest.TestCase):
    def test_cboe_parser_selects_previous_session_and_never_future(self):
        result=_parse_close_history(CSV_DATA,date(2026,11,6),
                                    expected_day="2026-11-05")
        self.assertEqual(result["day"],"2026-11-05")
        self.assertEqual(result["close"],28.18)
        self.assertEqual(result["source"],"Cboe · VIX Index storico · CLOSE")
        result2=_parse_close_history(CSV_DATA,date(2026,11,6))
        self.assertEqual(result2["day"],"2026-11-05")
        result3=_parse_close_history(CSV_DATA,date(2026,11,5))
        self.assertEqual(result3["day"],"2026-11-04")

    def test_quoted_header_and_different_date_encoding(self):
        csv_text='Preamble\n"DATE","OPEN","HIGH","LOW","CLOSE"\n2026-11-05,23,29,21,28.18\n'
        found=_parse_close_history(csv_text.encode("utf-8"),date(2026,11,6))
        self.assertEqual(found["close"],28.18)

    def test_no_invented_previous_close_from_wrong_day(self):
        for expected in ("2026-11-02", "2026-11-06", "no-date"):
            with self.subTest(expected=expected),self.assertRaises(ValueError):
                _parse_close_history(CSV_DATA,date(2026,11,6),
                                     expected_day=expected)
        with self.assertRaises(ValueError):
            _parse_close_history("DATE,CLOSE\n11/05/2026,NaN\n",
                                 date(2026,11,6))
        with self.assertRaises(ValueError):
            _parse_close_history("DATE,CLOSE\n11/05/2026,9999\n",
                                 date(2026,11,6))
        with self.assertRaises(ValueError):
            _parse_close_history(CSV_DATA,date(2026,11,6),
                                 expected_day="2026-10-26")

    def test_fourth_dark_card_after_buffer_same_buffer_values(self):
        fake=FakeStreamlit()
        original=scenario()
        with patch.object(vix_regime,"st",fake),patch.object(
            vix_regime,"volatility_context",return_value=original),patch(
            "vix_spot_history.previous_vix_spot",
            return_value={"status":"VALIDO","day":"2026-11-05",
                          "close":28.18}) as read:
            displayed=vix_regime.render(date(2026,11,6),return_context=True)
        read.assert_called_once_with(date(2026,11,6),
                                     expected_es_day="2026-11-05")
        self.assertIs(displayed,original)
        self.assertEqual(displayed["breakout_buffer"]["buffer_es_points"],15)
        self.assertEqual(displayed["vix_curve"]["contango_pct"],-9.55)
        self.assertEqual(displayed["vix_spot"]["close"],28.18)
        card=next(markup for markup,_ in fake.html if 'class="spx-vol-card"' in markup)
        self.assertIn("grid-template-columns:repeat(4,minmax(0,1fr))",card)
        self.assertEqual(card.count('class="spx-vol-kpi"'),4)
        self.assertLess(card.index("Buffer standard"),
                        card.index("VIX spot · chiusura precedente"))
        self.assertIn("15 punti (alta volatilità)",card)
        self.assertIn("28,18",card)
        self.assertIn("Chiusura 05/11/2026 · Cboe",card)
        self.assertIn("Regime VX precedente",card)
        self.assertTrue(any("non modifica il buffer" in c for c in fake.caption_messages))

    def test_missing_spot_is_nd_nonblocking_and_no_fallback_to_vx(self):
        fake=FakeStreamlit()
        with patch.object(vix_regime,"st",fake),patch.object(
            vix_regime,"volatility_context",return_value=scenario()),patch(
            "vix_spot_history.previous_vix_spot",
            side_effect=TimeoutError("Cboe down")):
            result=vix_regime.render(date(2026,11,6),return_context=True)
        card=next(markup for markup,_ in fake.html if 'class="spx-vol-card"' in markup)
        self.assertIn("VIX spot · chiusura precedente",card)
        self.assertIn('<div class="number">N/D</div>',card)
        self.assertIn("Chiusura Cboe non disponibile",card)
        self.assertEqual(result["vix_spot"]["status"],"NON DISPONIBILE")
        self.assertEqual(result["breakout_buffer"]["buffer_es_points"],15)
        self.assertIn("Backwardation",card)

    def test_vix_spot_close_is_never_used_to_compute_buffer(self):
        import inspect
        code=inspect.getsource(vix_regime.combined_breakout_buffer)
        self.assertNotIn("spot",code.lower())
        self.assertNotIn("vix_spot",inspect.getsource(
            vix_regime.volatility_context))
        self.assertNotIn("Yahoo",inspect.getsource(
            vix_regime.render))
        self.assertIn("previous_vix_spot",inspect.getsource(
            vix_regime.render))


if __name__=="__main__":
    unittest.main()
