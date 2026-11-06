"""Regression: common text sizing must not affect gamma or 4 export paths."""
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pine_export import make_pine
from pine_compare import make_pine_comparison
from pine_news_overlay import append_news

def profile(expiry="2026-11-06", magnitude=100):
    rows = [{"strike_spx": 7200.0 + i * 5, "net_gamma": (1 if i % 2 else -1) * (magnitude + i)}
            for i in range(50)]
    return {"expiry": expiry, "rows": rows}

class TextSizeRegression(unittest.TestCase):
    def check_common(self, script):
        self.assertIn('labelSizeChoice = input.string("Normale", "Dimensione scritte Pine"', script)
        self.assertIn('options=["Piccola", "Normale", "Grande", "Molto grande"]', script)
        self.assertIn('tooltip="Regola la dimensione di intestazioni, avvisi e news. Default Normale."', script)
        self.assertIn('labelTextSize = switch labelSizeChoice', script)
        self.assertIn('size=labelTextSize', script)
        self.assertIn("var array<float> odteValues", script)
        self.assertIn("Gamma/barra", script)

    def test_0730(self):
        self.check_common(make_pine(profile(), profile("2026-11-13"), 25, "2026-11-06", "2026-11-06 06:30:00", "Mattina 07:30"))

    def test_three_comparisons(self):
        for old,new in [("07:30","13:30"),("07:30","15:50"),("15:50","20:30")]:
            with self.subTest(slot=new):
                script = make_pine_comparison(profile(), profile(), profile("2026-11-13"), 25,
                         "2026-11-06", old, new, "2026-11-06 06:30:00")
                self.check_common(script)
                self.assertIn("showPrevious = input.bool(true", script)

    def test_emergency_current_snapshot_uses_normal_default(self):
        # Emergency uses the same common Serafini exporter.
        script = make_pine(
            profile(), profile("2026-11-13"), 25, "2026-11-06",
            "2026-11-06 06:30:00", "Download manuale 07:30",
            current_only=True)
        self.check_common(script)
        self.assertIn("Serafini SPX NET GAMMA", script)

    def test_news_same_size(self):
        script = make_pine(profile(), profile("2026-11-13"), 25, "2026-11-06", "2026-11-06 06:30:00", "Mattina 07:30")
        events = [{"day": date(2026,11,6), "clock":"15:30","kind":"NFP","priority":"ALTA"}]
        result = append_news(script, events, captured_at=datetime(2026,11,6,7,30,tzinfo=ZoneInfo("Europe/Rome")))
        self.assertIn("showMacroNews", result)
        self.assertIn('size=labelTextSize', result)
        self.assertNotIn("size=size.normal)", result)

if __name__ == "__main__":
    unittest.main()
