"""Tests deterministici della logica derivata dal Pine Serafini Metodo90 v2."""
import unittest
from datetime import date, datetime, time, timedelta

import pandas as pd
import plotly.graph_objects as go

from serafini_sigma import CHICAGO, compute_cash_levels, add_sigma_markers


def fake_m15(day, sessions=22):
    d = day - timedelta(days=1)
    dates = []
    while len(dates) < sessions:
        if d.weekday() < 5:
            dates.append(d)
        d -= timedelta(days=1)
    times, values = [], []
    counter = 0
    for session in sorted(dates):
        start = datetime.combine(session, time(8, 30), tzinfo=CHICAGO)
        for offset in range(26):
            times.append(start + timedelta(minutes=15 * offset))
            values.append(7600.0 + (counter % 57) * 0.75)
            counter += 1
        times.append(datetime.combine(session, time(15, 0), tzinfo=CHICAGO))
        values.append(7630.25 + len(times) * 0.01)  # Ref. 15:15
    return pd.DataFrame({"Close": values}, index=pd.DatetimeIndex(times))


class SerafiniSigmaCashTest(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 10, 28)
        self.frame = fake_m15(self.day)

    def test_precise_90_350_previous_close_and_population_sigma(self):
        result = compute_cash_levels(self.frame, self.day)
        self.assertEqual(result["session_chicago"], "2026-10-27")
        self.assertEqual(result["sample90"], 90)
        self.assertEqual(result["sample350"], 350)
        self.assertAlmostEqual(result["reference_close_es"],
                               float(self.frame.loc[
                                   datetime(2026, 10, 27, 15, 0, tzinfo=CHICAGO),
                                   "Close"]))
        cash = self.frame.loc[
            (self.frame.index.hour * 60 + self.frame.index.minute >= 510) &
            (self.frame.index.hour * 60 + self.frame.index.minute < 900),
            "Close",
        ].iloc[-350:]
        self.assertAlmostEqual(result["sigma90"], cash.iloc[-90:].std(ddof=0))
        self.assertAlmostEqual(result["sigma350"], cash.std(ddof=0))
        self.assertAlmostEqual(result["L90"],
                               result["reference_close_es"] + result["sigma90"])
        self.assertAlmostEqual(result["L350"],
                               result["reference_close_es"] + result["sigma350"])
        self.assertAlmostEqual(result["S350"],
                               result["reference_close_es"] - result["sigma350"])

    def test_no_lookahead_into_target_session(self):
        original = compute_cash_levels(self.frame, self.day)
        injected = pd.DataFrame(
            {"Close": [90000.0, 95000.0]},
            index=pd.DatetimeIndex([
                datetime(2026, 10, 28, 8, 30, tzinfo=CHICAGO),
                datetime(2026, 10, 28, 15, 0, tzinfo=CHICAGO)
            ]),
        )
        combined = pd.concat([self.frame, injected]).sort_index()
        self.assertEqual(compute_cash_levels(combined, self.day), original)

    def test_insufficient_or_incomplete_cash_not_guessed(self):
        with self.assertRaisesRegex(ValueError, "Storico cash insufficiente"):
            compute_cash_levels(fake_m15(self.day, sessions=10), self.day)
        dropped = self.frame.drop(
            datetime(2026, 10, 27, 8, 45, tzinfo=CHICAGO)
        )
        with self.assertRaisesRegex(ValueError, "cash M15 incompleta"):
            compute_cash_levels(dropped, self.day)

    def test_three_short_stubs_only_and_clipped_without_expansion(self):
        levels = compute_cash_levels(self.frame, self.day)
        fig = go.Figure()
        shown, clipped = add_sigma_markers(fig, levels, [7000., 8000.])
        self.assertEqual(shown, 3)
        self.assertEqual(clipped, [])
        self.assertEqual(len(fig.layout.shapes), 3)
        self.assertTrue(all(shape.xref == "paper"
                            and shape.yref == "y"
                            and shape.x0 == 0.017 and shape.x1 == 0.067
                            for shape in fig.layout.shapes))
        self.assertEqual(len(fig.layout.annotations), 3)
        other = go.Figure()
        shown, clipped = add_sigma_markers(other, levels, [100., 200.])
        self.assertEqual(shown, 0)
        self.assertEqual(len(clipped), 3)
        self.assertEqual(len(other.layout.shapes), 0)


if __name__ == "__main__":
    unittest.main()
