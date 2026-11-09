"""GEX EDGE triptych: actual Plotly chart with Europe/Rome time."""
import unittest
import pandas as pd
from edge_triptych import _plot


class EdgeReplayChartTests(unittest.TestCase):
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
