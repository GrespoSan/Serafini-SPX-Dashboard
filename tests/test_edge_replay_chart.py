"""GEX EDGE triptych: actual Plotly chart with Europe/Rome time."""
import unittest
import pandas as pd
from edge_triptych import _plot


class EdgeReplayChartTests(unittest.TestCase):
    def test_wall_and_flip_lines_stand_out_on_es_candles(self):
        # Reuses a real Plotly figure: all 3 panels retain the SAME levels.
        candles = pd.DataFrame({
            "bar_close": pd.DatetimeIndex(["2026-11-11T09:00:00+00:00"]),
            "open": [7545.], "high": [7555.],
            "low": [7542.], "close": [7550.],
        })
        edge = {
            "rows": [
                {"strike_spx": 7509., "call_gex": 100e6,
                 "put_gex": -120e6, "net_gex": -20e6},
                {"strike_spx": 7549., "call_gex": 200e6,
                 "put_gex": -80e6, "net_gex": 120e6}
            ],
            "put_wall_spx": 7509.0, "gamma_flip_spx": 7544.67,
            "call_wall_spx": 7549.0
        }
        serafini_rows = [
            {"strike_spx": 7509.0, "net_gamma": -20.0},
            {"strike_spx": 7549.0, "net_gamma": 45.0}
        ]
        # Real triptych: both GEX and Serafini panels have their own axes.
        fig = _plot(edge, serafini_rows, candles, 24.0, [7440., 7630.])
        shapes = list(fig.layout.shapes)
        self.assertEqual(len(shapes), 9)
        first = [s for s in shapes if s.xref == "x domain"]
        second = [s for s in shapes if s.xref == "x2 domain"]
        third = [s for s in shapes if s.xref == "x3 domain"]
        self.assertEqual(len(first), 3)
        self.assertEqual(len(second), 3)
        self.assertEqual(len(third), 3)
        expected = {7533.0: ("#ff5365", "dot"),
                    7568.67: ("#ffcc4d", "dash"),
                    7573.0: ("#31e38b", "dot")}
        for shape in first:
            color, dash = expected[round(shape.y0, 2)]
            self.assertEqual(shape.line.color, color)
            self.assertEqual(shape.line.dash, dash)
            self.assertEqual(shape.line.width, 3)
            self.assertEqual(shape.opacity, 0.98)
        for shape in second + third:
            color, dash = expected[round(shape.y0, 2)]
            self.assertEqual(shape.line.color, color)
            self.assertEqual(shape.line.dash, dash)
            self.assertEqual(shape.line.width, 1.2)
            self.assertEqual(shape.opacity, 0.6)
        self.assertEqual(fig.layout.yaxis.range, (7440., 7630.))

    def test_candle_timestamp_display_has_correct_rome_clock(self):
        candles = pd.DataFrame({
            "bar_close": pd.DatetimeIndex([
                pd.Timestamp("2026-11-09T15:30:00+00:00"),
                pd.Timestamp("2026-11-09T15:35:00+00:00"),
            ]),
            "open": [7490., 7493.], "high": [7495., 7496.],
            "low": [7488., 7491.], "close": [7493., 7494.],
        })
        candles.attrs["candle_source"] = "Massive Futures ESZ6"
        edge = {
            "rows": [
                {"strike_spx": 7460., "call_gex": 100e6,
                 "put_gex": -80e6, "net_gex": 20e6},
                {"strike_spx": 7490., "call_gex": 40e6,
                 "put_gex": -160e6, "net_gex": -120e6}
            ],
            "call_wall_spx": 7490., "put_wall_spx": 7460.,
            "gamma_flip_spx": 7475.,
        }
        serafini_rows = [
            {"strike_spx": 7460., "net_gamma": -20.},
            {"strike_spx": 7490., "net_gamma": 30.}
        ]
        fig = _plot(edge, serafini_rows, candles, 24., [7400., 7550.],
                    reading_clock="16:31:12")
        self.assertEqual(tuple(fig.data[0].x), (
            "2026-11-09T16:30:00", "2026-11-09T16:35:00"))
        self.assertEqual(fig.data[0].name, "Massive Futures ESZ6")
        self.assertEqual(fig.layout.xaxis.tickformat, "%H:%M")
        self.assertEqual(len(fig.data), 6)
        self.assertEqual(fig.layout.yaxis.range, (7400., 7550.))
        self.assertGreater(len(fig.to_plotly_json()["data"]), 0)


if __name__ == "__main__":
    unittest.main()
