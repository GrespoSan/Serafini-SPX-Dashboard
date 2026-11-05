"""Trend Daily duplicates only validated Edge 0DTE modeled sign and NET GEX.

All tests synthetic. No Cboe, Massive, Supabase, Yahoo, or upstream GEX call.
"""
import ast
from datetime import date
from pathlib import Path
from unittest.mock import patch
import unittest

import trend_daily_section
from edge_daily_gex import prepare_daily_gex

ROOT=Path(__file__).resolve().parents[1]


def report(sign="Positive", net=2.77e9):
    return {
        "symbol": "SPX", "mode": "0DTE", "expiries": ["2026-11-05"],
        "sha256": "a" * 64, "timestamp": "2026-11-05 13:30:00",
        "modeled_gamma_sign": sign, "net_gex": net,
        "call_gex": 5.00e9, "put_gex": net - 5.00e9,
    }


class FakeColumn:
    def __init__(self):
        self.values = []

    def markdown(self, html, unsafe_allow_html=False):
        self.values.append((html, unsafe_allow_html))


class FakeUI:
    def __init__(self):
        self.labels=[]
        self.messages=[]
        self.columns_rendered=[]

    def subheader(self, value):
        self.labels.append(value)

    def columns(self, n):
        group=[FakeColumn() for _ in range(n)]
        self.columns_rendered.append(group)
        return group

    def caption(self, value):
        self.messages.append(("caption",value))

    def info(self, value):
        self.messages.append(("info",value))


class EdgeDailyDuplicationTests(unittest.TestCase):
    def test_successful_matching_0dte_is_the_same_values(self):
        value=prepare_daily_gex(report(), date(2026,11,5), "15:50")
        self.assertEqual(value["modeled_gamma_sign"],"Positive")
        self.assertEqual(value["net_gex"],2.77e9)
        self.assertEqual(value["expiry"],"2026-11-05")
        self.assertEqual(value["download_clock"],"15:50")
        self.assertEqual(value["sha256"],"a"*64)

    def test_invalid_and_stale_values_cannot_be_replayed(self):
        cases=[
            {**report(), "mode":"Weekly"},
            {**report(), "expiries":["2026-11-04"]},
            {**report(), "symbol":"NDX"},
            {**report(), "sha256":"mismatch"},
            {**report(), "net_gex":-2.77e9},
            {**report(), "put_gex":-1.23e9},
            {**report(), "modeled_gamma_sign":"Unknown"},
            {**report(), "call_gex":float("nan")},
            {**report(), "put_gex":2.0},
        ]
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    prepare_daily_gex(item,date(2026,11,5))

    def test_daily_massive_failure_does_not_hide_successful_edge(self):
        context=prepare_daily_gex(report(),date(2026,11,5),"15:50")
        fake=FakeUI()
        with patch.object(trend_daily_section,"st",fake):
            trend_daily_section.render(
                reference_day=date(2026,11,5), daily_bars=[],
                modeled_gex=context)
        self.assertIn("Trend Daily — contesto SPX / ES",fake.labels)
        self.assertEqual(len(fake.columns_rendered),1)
        content="\n".join(
            html for col in fake.columns_rendered[0]
            for html,_ in col.values)
        self.assertIn("Segno Gamma modellato",content)
        self.assertIn("Positivo allo spot Cboe",content)
        self.assertIn("NET GEX",content)
        self.assertIn("+2.77 mld USD",content)
        self.assertIn("#22b573",content)
        self.assertTrue(any(
            "Trend Daily non disponibile" in v
            for typ,v in fake.messages if typ=="info"))
        self.assertTrue(any(
            "15:50" in v
            for typ,v in fake.messages if typ=="caption"))

    def test_no_edge_report_means_no_metric_and_daily_still_continues(self):
        fake=FakeUI()
        with patch.object(trend_daily_section,"st",fake):
            trend_daily_section.render(
                reference_day=date(2026,11,5), daily_bars=[],
                modeled_gex=None)
        self.assertFalse(fake.columns_rendered)
        self.assertTrue(any(typ=="info" for typ,_ in fake.messages))

    def test_same_source_only_and_nonblocking_current_run(self):
        app=(ROOT/"app.py").read_text(encoding="utf-8")
        edge=(ROOT/"edge_triptych.py").read_text(encoding="utf-8")
        trend=(ROOT/"trend_daily_section.py").read_text(encoding="utf-8")
        for name,src in (("app.py",app),("edge_triptych.py",edge),
                         ("trend_daily_section.py",trend)):
            ast.parse(src,filename=name)
        self.assertIn("trend_edge_gex = {}",app)
        self.assertIn("on_valid_gex=trend_edge_gex.update",app)
        self.assertIn("trend_edge_gex.clear()",app)
        self.assertIn("modeled_gex=trend_edge_gex or None",app)
        self.assertNotIn("st.session_state['trend_edge_gex']",app)
        self.assertLess(edge.index("_matching_serafini(store,week,e"),
                        edge.index("on_valid_gex(duplicate)"))
        self.assertIn("prepare_daily_gex(e, day, reading_clock)",edge)
        self.assertIn('from edge_gex_exposure import format_gex_billions',trend)
        self.assertNotIn("result_from_snapshot",trend)
        self.assertNotIn("store.download(",trend)


if __name__=="__main__":
    unittest.main()
