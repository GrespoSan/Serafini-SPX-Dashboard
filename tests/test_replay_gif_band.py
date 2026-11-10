"""Replay GIF: same archive-only band and fixed prices as interactive graph."""
import unittest
from datetime import date
from io import BytesIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
from PIL import Image

from replay_gif import generate_gif

ROME = ZoneInfo("Europe/Rome")


def item(slot, time_value):
    rows = [
        {"strike_spx": float(k), "net_gamma": (k-7500)/50.0}
        for k in range(7375, 7630, 5) if k != 7500
    ]
    pool = [
        {"strike_spx": float(k), "net_gamma": (k-7500)/50.0}
        for k in range(7150, 7855, 5) if k != 7500
    ]
    return (slot, {
        "clock": slot + ":00",
        "download_rome": f"2026-11-09T{time_value}+01:00",
        "reference_spx": 7500.0, "spot": 7535.0,
        "profiles": {"0DTE": {
            "rows": rows, "visual_rows": pool[:100],
            "visual_pool_rows": pool,
        }},
    })


class ReplayGifBandTests(unittest.TestCase):
    def test_gif_generates_two_frames_from_same_real_archive_with_5pct(self):
        candles = pd.DataFrame({
            "bar_close": pd.DatetimeIndex([
                pd.Timestamp("2026-11-09T07:30:00+01:00"),
                pd.Timestamp("2026-11-09T14:30:00+01:00"),
            ]).tz_convert(ROME),
            "open": [7521., 7530.], "high": [7524., 7550.],
            "low": [7515., 7527.], "close": [7523., 7545.],
        })
        candles.attrs["candle_source"] = "Yahoo ES=F · 5m verificato"
        available = [
            item("07:30", "07:30:39"), item("14:31", "14:31:16")
        ]
        with patch("replay_gif.load_es_candles", return_value=candles) as loader:
            payload, count = generate_gif(
                available, date(2026,11,9), "0DTE", 24.,
                show_extended=True, band_pct=5., auto_extend=True,
                zoom_complete=False,
            )
        self.assertEqual(count, 2)
        self.assertTrue(payload.startswith(b"GIF89a"))
        self.assertLess(len(payload), 4_000_000)
        loader.assert_called_once()
        with Image.open(BytesIO(payload)) as gif:
            self.assertEqual(gif.n_frames, 2)
            self.assertEqual(gif.size, (1440, 810))

    def test_full_band_zoom_can_export_without_inventing_levels(self):
        candles = pd.DataFrame({
            "bar_close": pd.DatetimeIndex([
                pd.Timestamp("2026-11-09T07:30:00+01:00"),
                pd.Timestamp("2026-11-09T14:30:00+01:00"),
            ]).tz_convert(ROME),
            "open": [7521., 7530.], "high": [7524., 7550.],
            "low": [7515., 7527.], "close": [7523., 7545.],
        })
        with patch("replay_gif.load_es_candles", return_value=candles):
            payload, count = generate_gif(
                [item("07:30", "07:30:39"), item("14:31", "14:31:16")],
                date(2026,11,9), "0DTE", 24.,
                show_extended=True, band_pct=10., auto_extend=True,
                zoom_complete=True,
            )
        self.assertEqual(count, 2)
        self.assertTrue(payload.startswith(b"GIF89a"))


if __name__ == "__main__":
    unittest.main()
