"""Trend Daily: read-only GEX inside fifth dark KPI, no top-level duplicate.

Synthetic data only; no Cboe, Massive, Supabase, Yahoo or remote GEX calls.
"""
import ast
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
import unittest

import trend_daily_section
from edge_daily_gex import (
    prepare_daily_gex, compact_gex_kpi_html, compact_gex_caption,
)

ROOT = Path(__file__).resolve().parents[1]


def report(sign="Positive", net=2.77e9):
    return {
        "symbol": "SPX", "mode": "0DTE", "expiries": ["2026-11-05"],
        "sha256": "a" * 64, "timestamp": "2026-11-05 13:30:00",
        "modeled_gamma_sign": sign, "net_gex": net,
        "call_gex": 5.00e9, "put_gex": net - 5.00e9,
    }


def bars():
    # Five valid closed sessions with alternating colors.
    days = [date(2026, 10, 29) + timedelta(days=i) for i in range(5)]
    return [
        {"day": day, "ticker": "ESZ6", "open": 7700.0 + i,
         "hi": 7735.0 + i, "lo": 7675.0 + i,
         "close": 7710.0 + i if i % 2 == 0 else 7690.0 + i}
        for i, day in enumerate(days)
    ]


class FakeUI:
    def __init__(self):
        self.headings = []
        self.markdowns = []
        self.messages = []

    def subheader(self, value):
        self.headings.append(value)

    def markdown(self, html, unsafe_allow_html=False):
        self.markdowns.append((html, unsafe_allow_html))

    def caption(self, value):
        self.messages.append(("caption", value))

    def info(self, value):
        self.messages.append(("info", value))

    def warning(self, value):
        self.messages.append(("warning", value))

    def columns(self, n):
        raise AssertionError("I due indicatori GEX non devono creare colonne sopra i KPI")


class EdgeDailyKPITests(unittest.TestCase):
    def test_source_values_same_0dte_no_new_calculation(self):
        value = prepare_daily_gex(report(), date(2026, 11, 5), "07:30")
        self.assertEqual(value["modeled_gamma_sign"], "Positive")
        self.assertEqual(value["net_gex"], 2.77e9)
        self.assertEqual(value["expiry"], "2026-11-05")
        self.assertEqual(value["download_clock"], "07:30")
        self.assertEqual(value["sha256"], "a" * 64)

    def test_fifth_kpi_inside_same_dark_card_after_same_color(self):
        context = prepare_daily_gex(report(), date(2026, 11, 5), "07:30")
        fake = FakeUI()
        with patch.object(trend_daily_section, "st", fake):
            trend_daily_section.render(
                reference_day=date(2026, 11, 5), daily_bars=bars(),
                contract="ESZ6", modeled_gex=context)
        self.assertIn("Trend Daily — contesto SPX / ES", fake.headings)
        self.assertEqual(len(fake.markdowns), 1)
        html, unsafe = fake.markdowns[0]
        self.assertTrue(unsafe)
        self.assertIn("spx-trend-card", html)
        self.assertIn("repeat(4,minmax(0,1fr)) minmax(190px,1.08fr)", html)
        self.assertIn('class="spx-trend-label">GEX', html)
        self.assertLess(html.index("Barre stesso colore"), html.index("Segno Gamma modellato"))
        self.assertIn("Positivo</div>", html)
        self.assertIn("NET GEX", html)
        self.assertIn("+2.77 mld USD", html)
        self.assertIn("#22b573", html)
        self.assertNotIn("allo spot Cboe", html)
        self.assertEqual(html.count("Segno Gamma modellato"), 1)
        self.assertEqual(html.count("+2.77 mld USD"), 1)
        self.assertLess(html.index("spx-trend-gex-source"), html.index('class="spx-trend-foot"'))
        self.assertIn("download 07:30 Roma", html)
        self.assertIn("GEX EDGE 0DTE", html)

    def test_impulse_midpoint_replaces_daily_midpoint_kpi_and_sentence_moves_up(self):
        # L'ultima Daily è del 10/11; l'impulso valido è del 09/11.
        # Questo è solo un campione artificiale, non una quotazione.
        days = [date(2026,11,4),date(2026,11,5),date(2026,11,6),
                date(2026,11,9),date(2026,11,10)]
        ohlc = [
            (7400,7440,7370,7420),
            (7420,7450,7390,7400),
            (7400,7430,7375,7410),
            (7440,7580,7430,7560),  # impulso rialzista: midpoint 7505
            (7560,7580,7520,7555),  # tenuta sopra il centro impulso
        ]
        source = [
            {"day": d, "ticker":"ESZ6", "open":v[0], "hi":v[1],
             "lo":v[2], "close":v[3]}
            for d,v in zip(days,ohlc)
        ]
        fake=FakeUI()
        with patch.object(trend_daily_section, "st", fake):
            trend_daily_section.render(reference_day=date(2026,11,11),
                                      daily_bars=source,contract="ESZ6")
        html=fake.markdowns[0][0]
        self.assertIn('class="spx-trend-label">Impulso 09/11/2026',html)
        self.assertIn("7.505,00",html)
        self.assertIn("Rialzista · TENUTA · 1 seduta",html)
        self.assertIn("nessuna candela successiva è scesa sotto il baricentro",html)
        self.assertEqual(html.count("Candela di impulso 09/11/2026"),1)
        self.assertNotIn('class="spx-trend-label">Baricentro Daily</div>',html)
        self.assertIn("Chiusura vs baricentro Daily",html)
        self.assertIn("spx-trend-impulse",html)

    def test_no_edge_gex_keeps_four_daily_cards_without_extra_caption(self):
        fake = FakeUI()
        with patch.object(trend_daily_section, "st", fake):
            trend_daily_section.render(
                reference_day=date(2026, 11, 5), daily_bars=bars(),
                contract="ESZ6", modeled_gex=None)
        self.assertEqual(len(fake.markdowns), 1)
        html = fake.markdowns[0][0]
        self.assertIn("grid-template-columns:repeat(4,minmax(0,1fr))", html)
        self.assertNotIn('class="spx-trend-label">GEX', html)
        self.assertNotIn("Segno Gamma modellato", html)
        self.assertNotIn("GEX EDGE 0DTE", html)

    def test_gex_remains_nonblocking_if_daily_data_missing(self):
        context = prepare_daily_gex(report(), date(2026,11,5), "15:50")
        fake = FakeUI()
        with patch.object(trend_daily_section, "st", fake):
            trend_daily_section.render(
                reference_day=date(2026,11,5), daily_bars=[],
                modeled_gex=context)
        self.assertEqual(len(fake.markdowns), 1)
        self.assertIn("Positivo", fake.markdowns[0][0])
        self.assertIn("+2.77 mld USD", fake.markdowns[0][0])
        self.assertNotIn("allo spot Cboe", fake.markdowns[0][0])
        self.assertTrue(any("15:50" in x for kind,x in fake.messages
                            if kind == "caption"))
        self.assertTrue(any("Trend Daily non disponibile" in x
                            for kind,x in fake.messages if kind == "info"))

    def test_no_cboe_result_means_no_gex_rendering_even_if_daily_unavailable(self):
        fake = FakeUI()
        with patch.object(trend_daily_section, "st", fake):
            trend_daily_section.render(
                reference_day=date(2026,11,5), daily_bars=[],
                modeled_gex=None)
        self.assertFalse(fake.markdowns)
        self.assertTrue(any(kind == "info" for kind,_ in fake.messages))

    def test_positive_negative_zero_and_safe_malformed_values(self):
        for sign, net, expected in (
            ("Positive", 2.77e9, "Positivo"),
            ("Negative", -2.77e9, "Negativo"),
            ("Zero", 0.0, "Zero"),
        ):
            with self.subTest(sign=sign):
                valid = {"modeled_gamma_sign": sign, "net_gex": net,
                         "expiry": "2026-11-05", "download_clock": "07:30"}
                html = compact_gex_kpi_html(valid)
                self.assertIn(expected, html)
                self.assertIn("GEX", html)
                self.assertNotIn("allo spot Cboe", html)
        for value in (float("nan"), float("inf"), None, True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    compact_gex_kpi_html({
                        "modeled_gamma_sign": "Positive", "net_gex": value})
        self.assertIn("fotografia Cboe 2026-11-05",
                      compact_gex_caption({
                          "expiry":"2026-11-05", "download_clock": None}))

    def test_invalid_and_stale_values_rejected_before_display(self):
        cases = [
            {**report(), "mode": "Weekly"},
            {**report(), "expiries": ["2026-11-04"]},
            {**report(), "symbol": "NDX"},
            {**report(), "sha256": "mismatch"},
            {**report(), "net_gex": -2.77e9},
            {**report(), "put_gex": -1.23e9},
            {**report(), "modeled_gamma_sign": "Unknown"},
            {**report(), "call_gex": float("nan")},
            {**report(), "put_gex": 2.0},
        ]
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    prepare_daily_gex(item, date(2026,11,5))

    def test_same_source_only_and_no_new_download(self):
        app = (ROOT/"app.py").read_text(encoding="utf-8")
        edge = (ROOT/"edge_triptych.py").read_text(encoding="utf-8")
        trend = (ROOT/"trend_daily_section.py").read_text(encoding="utf-8")
        display = (ROOT/"edge_daily_gex.py").read_text(encoding="utf-8")
        for name,src in (("app.py",app),("edge_triptych.py",edge),
                         ("trend_daily_section.py",trend),
                         ("edge_daily_gex.py",display)):
            ast.parse(src,filename=name)
        self.assertIn("trend_edge_gex = {}", app)
        self.assertIn("on_valid_gex=trend_edge_gex.update", app)
        self.assertIn("trend_edge_gex.clear()", app)
        self.assertIn("modeled_gex=trend_edge_gex or None", app)
        self.assertLess(edge.index("_matching_serafini(store,week,e"),
                        edge.index("on_valid_gex(duplicate)"))
        self.assertIn("prepare_daily_gex(e, day, reading_clock)", edge)
        self.assertIn("compact_gex_kpi_html(modeled_gex)", trend)
        self.assertIn("compact_gex_caption(modeled_gex)", trend)
        self.assertNotIn("render_daily_gex(st, modeled_gex)", trend)
        self.assertNotIn("from edge_triptych", trend)
        self.assertNotIn("result_from_snapshot", trend)
        self.assertNotIn("store.download(", trend)


if __name__ == "__main__":
    unittest.main()
