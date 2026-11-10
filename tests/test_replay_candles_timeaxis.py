"""Regressione Replay: asse orario locale coerente durante slider e Play.

Test completamente locali: non richiede Yahoo, Cboe, Streamlit Cloud o Supabase.
"""
import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go

from replay_candles import combined_graph, _frame_download_rome, _plot_rome_clock

ROME = ZoneInfo("Europe/Rome")
DAY = date(2026, 11, 9)


def _frame(slot, download):
    return (slot, {
        "clock": download[11:19], "download_rome": download,
        "reference_spx": 7450.0, "spot": 7430.0,
        "profiles": {"0DTE": {"rows": [{"strike_spx": 7400, "net_gamma": 3}]}}
    })


def _gamma_graph(available):
    fig = go.Figure(data=[
        go.Bar(x=[3], y=[7400], orientation="h"),
        go.Scatter(x=[0], y=[7400], mode="text", text=["7400"])
    ])
    fig.update_layout(title={"text": "Replay gamma"},
                      xaxis={"range": [-5, 5]})
    fig.frames = [
        go.Frame(name=slot,
                 data=[go.Bar(x=[3], y=[7400], orientation="h"),
                       go.Scatter(x=[0], y=[7400], mode="text", text=["7400"])],
                 traces=[0, 1],
                 layout=go.Layout(title={"text": f"Slot {slot}"},
                                  xaxis={"range": [-5, 5]}))
        for slot, _ in available
    ]
    return fig


class ReplayTimeAxisTests(unittest.TestCase):
    def test_rome_clock_explicit_no_timezone_in_display(self):
        self.assertEqual(
            _plot_rome_clock(datetime(2026, 11, 9, 6, 30, tzinfo=timezone.utc)),
            "2026-11-09T07:30:00")

    def test_dst_mismatch_is_converted_using_zoneinfo_not_constant_shift(self):
        self.assertEqual(
            _plot_rome_clock(datetime(2026, 3, 27, 13, 30, tzinfo=timezone.utc)),
            "2026-03-27T14:30:00")
        self.assertEqual(
            _plot_rome_clock(datetime(2026, 3, 30, 13, 30, tzinfo=timezone.utc)),
            "2026-03-30T15:30:00")

    def test_only_verifiable_download_time_allowed(self):
        with self.assertRaises(ValueError):
            _frame_download_rome(DAY, {"download_rome": "2026-11-09T07:30:00"})
        with self.assertRaises(ValueError):
            _frame_download_rome(DAY, {"download_rome": "2026-11-08T07:30:00+01:00"})
        self.assertEqual(
            _frame_download_rome(
                DAY, {"download_rome": "2026-11-09T06:30:00+00:00"}
            ).hour, 7)

    def test_frames_do_not_shift_chart_time_or_show_future_candles(self):
        bar_times = pd.to_datetime([
            "2026-11-09T00:05:00+01:00",
            "2026-11-09T07:30:00+01:00",
            "2026-11-09T14:30:00+01:00",
            "2026-11-09T14:35:00+01:00"
        ]).tz_convert(ROME)
        data = pd.DataFrame({
            "bar_close": bar_times,
            "open": [7420., 7422., 7430., 7440.],
            "high": [7423., 7425., 7434., 7442.],
            "low": [7419., 7421., 7428., 7439.],
            "close": [7422., 7424., 7431., 7441.]
        })
        available = [
            _frame("07:30", "2026-11-09T07:30:39+01:00"),
            _frame("14:31", "2026-11-09T14:31:10+01:00")
        ]
        with patch("replay_candles.load_es_candles", return_value=data):
            fig = combined_graph(_gamma_graph(available), available,
                                 DAY, "0DTE", 20., [-5, 5], [7380, 7490])
        start, end = fig.layout.xaxis.range
        self.assertEqual(start, "2026-11-09T00:00:00")
        self.assertEqual(end, "2026-11-09T18:35:00")
        self.assertEqual(fig.layout.xaxis.tickformat, "%H:%M")
        self.assertEqual(fig.layout.xaxis.dtick, 10800000)
        self.assertEqual(list(fig.data[0].x),
                         ["2026-11-09T00:05:00", "2026-11-09T07:30:00"])
        self.assertEqual(list(fig.frames[-1].data[0].x),
                         ["2026-11-09T00:05:00", "2026-11-09T07:30:00",
                          "2026-11-09T14:30:00"])
        for frame in fig.frames:
            self.assertEqual(tuple(frame.layout.xaxis.range), (start, end))
            self.assertFalse(frame.layout.xaxis.autorange)

if __name__ == "__main__":
    unittest.main()
