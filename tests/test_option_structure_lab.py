"""Option structure study: hypothetical strike overrides never rewrite Cboe Gamma."""
import unittest
from copy import deepcopy

import numpy as np

from option_structure import build_structure, make_study_levels
from payoff_charts import intrinsic_payoff


NAMES = ("C1", "C2", "C3", "P1", "P2", "P3")
BASE = {"C1": 7485, "C2": 7490, "C3": 7500,
        "P1": 7375, "P2": 7350, "P3": 7300}
GAMMA = {"C1": 11.3, "C2": 15.1, "C3": 24.9,
         "P1": -5.2, "P2": -9.9, "P3": -5.0}


def choices():
    return {
        key: {"strike_spx": BASE[key], "net_gamma": GAMMA[key]}
        for key in NAMES
    }


def profile():
    return {
        "rows": [{"strike_spx": BASE[key], "net_gamma": GAMMA[key]}
                 for key in NAMES]
    }


class StructureStudyTests(unittest.TestCase):
    def test_changed_strike_does_not_inherit_another_strike_gamma(self):
        original = choices()
        original_copy = deepcopy(original)
        changed = dict(BASE, C1=7482.5)
        study = make_study_levels(original, changed, profile())
        self.assertEqual(original, original_copy)
        self.assertEqual(study["C1"]["strike_spx"], 7482.5)
        self.assertIsNone(study["C1"]["net_gamma"])
        self.assertEqual(study["C2"]["net_gamma"], GAMMA["C2"])
        rows, widths = build_structure(study, offset=24)
        self.assertEqual(rows[0]["Prezzo ES"], 7506.5)
        self.assertEqual(rows[0]["NET GAMMA"], "N/D")
        self.assertEqual(widths["Vertical Call (punti)"], 7.5)

    def test_gamma_is_looked_up_at_exact_real_strike_only(self):
        original = choices()
        extra = profile()
        extra["rows"].append({"strike_spx": 7482.5, "net_gamma": 4.25})
        study = make_study_levels(original, dict(BASE, C1=7482.5), extra)
        self.assertEqual(study["C1"]["net_gamma"], 4.25)

    def test_payoff_changes_with_hypothetical_levels_without_premiums(self):
        x = np.asarray([7480., 7492., 7520.])
        original = intrinsic_payoff(BASE, "combined", x)
        changed_strikes = dict(BASE, C2=7495)
        study = make_study_levels(choices(), changed_strikes, profile())
        current = intrinsic_payoff(
            {key: study[key]["strike_spx"] for key in NAMES}, "combined", x
        )
        self.assertNotEqual(original.tolist(), current.tolist())
        np.testing.assert_allclose(
            current,
            intrinsic_payoff(
                {key: study[key]["strike_spx"] for key in NAMES}, "call", x
            ) + intrinsic_payoff(
                {key: study[key]["strike_spx"] for key in NAMES}, "put", x
            ) + intrinsic_payoff(
                {key: study[key]["strike_spx"] for key in NAMES}, "strangle", x
            )
        )

    def test_invalid_or_missing_inputs_are_rejected(self):
        for change in (
            {"C1": 7495}, {"P2": 7380}, {"C1": -1},
            {"C1": float("nan")}, {"P3": float("inf")},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    make_study_levels(choices(), dict(BASE, **change), profile())
        with self.assertRaisesRegex(ValueError, "sei"):
            make_study_levels(choices(), {"C1": 7485}, profile())

    def test_none_gamma_for_off_grid_manual_level_is_not_modified_on_rebuild(self):
        study = make_study_levels(choices(), dict(BASE, P2=7342.5), profile())
        rows1, _ = build_structure(study, 24)
        rows2, _ = build_structure(study, 24)
        p2 = next(row for row in rows1 if row["Livello"] == "P2")
        self.assertEqual(p2["NET GAMMA"], "N/D")
        self.assertEqual(rows1, rows2)

if __name__ == "__main__":
    unittest.main()
