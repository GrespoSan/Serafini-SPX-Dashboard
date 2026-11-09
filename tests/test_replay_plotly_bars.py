"""Regressione del ValueError Streamlit Replay su barre con strike mancanti."""
import unittest

import plotly.graph_objects as go

from replay_profiles import gamma_bar_trace


class ReplayPlotlyBarTests(unittest.TestCase):
    def test_extended_replay_with_missing_strikes_builds_valid_plotly_frame(self):
        strikes = [float(7300 + 5 * i) for i in range(80)]
        prices = [strike + 24. for strike in strikes]
        first = {strike: float(i - 25) for i, strike in enumerate(strikes[:50])}
        next_frame = {strike: float(i - 25) for i, strike in enumerate(strikes)}
        for label, values in (("07:30", first), ("16:46", next_frame)):
            with self.subTest(slot=label):
                trace = gamma_bar_trace(label, strikes, prices, values)
                fig = go.Figure(data=[trace])
                encoded = fig.to_plotly_json()
                self.assertEqual(len(trace.x), 80)
                self.assertEqual(len(trace.marker.color), 80)
                self.assertNotIn("#00000000", encoded["data"][0]["marker"]["color"])
                if label == "07:30":
                    self.assertIsNone(trace.x[70])
                    self.assertEqual(trace.marker.color[70], "rgba(0,0,0,0)")
                else:
                    self.assertEqual(trace.x[70], next_frame[strikes[70]])

    def test_positive_negative_and_true_zero_are_distinct(self):
        trace = gamma_bar_trace("14:31", [1., 2., 3., 4.],
                                [11., 12., 13., 14.],
                                {1.: 5.25, 2.: -3., 3.: 0.})
        self.assertEqual(list(trace.x), [5.25, -3., 0., None])
        self.assertEqual(trace.marker.color[0], "#22c55e")
        self.assertEqual(trace.marker.color[1], "#ef4444")
        self.assertEqual(trace.marker.color[2], "#22c55e")
        self.assertEqual(trace.marker.color[3], "rgba(0,0,0,0)")

    def test_large_extended_140_strike_profile_still_serializes(self):
        strikes = [float(7200 + 5 * i) for i in range(140)]
        gamma = {s: float(i - 70) for i, s in enumerate(strikes[::2])}
        bar = gamma_bar_trace("16:46", strikes, strikes, gamma)
        fig = go.Figure(data=[bar])
        obj = fig.to_plotly_json()
        self.assertEqual(len(obj["data"][0]["marker"]["color"]), 140)
        self.assertIsNone(bar.x[1])
        self.assertEqual(sum(v is None for v in bar.x), 70)


if __name__ == "__main__":
    unittest.main()
