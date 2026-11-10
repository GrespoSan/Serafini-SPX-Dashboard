"""Regression tests for isolated Serafini gamma distribution research."""
import unittest

from research_gamma_distribution import compare_profiles


def rows(items):
    return [{"strike_spx": k, "net_gamma": v} for k, v in items]


class GammaDistributionTests(unittest.TestCase):
    def test_relative_versus_absolute(self):
        before = rows([(7500, 10), (7550, 15)])
        after = rows([(7500, 100), (7550, 400)])
        result = compare_profiles(before, after, spot=7480)
        strike = next(r for r in result["strikes"] if r["strike_spx"] == 7500)
        self.assertAlmostEqual(strike["relative_to_max_before_pct"], 100 * 10 / 15)
        self.assertAlmostEqual(strike["relative_to_max_after_pct"], 25)
        self.assertAlmostEqual(strike["relative_to_max_delta_pp"], -41.6666666667)
        self.assertEqual(result["status"], "OK")

    def test_common_strikes_only(self):
        result = compare_profiles(rows([(7500, 10), (7550, 15)]),
                                  rows([(7500, 20), (7600, 100)]))
        self.assertEqual(result["status"], "COPERTURA PARZIALE")
        self.assertEqual(result["common_count"], 1)
        self.assertEqual(result["strikes"][0]["relative_to_max_before_pct"], 100)

    def test_zero_denominator_and_sign_flip(self):
        result = compare_profiles(rows([(7500, 0), (7550, 0)]),
                                  rows([(7500, -3), (7550, 2)]), spot=7490)
        strike = result["strikes"][0]
        self.assertIsNone(strike["relative_to_max_before_pct"])
        self.assertFalse(strike["sign_flip"])
        self.assertIsNone(result["positive_above_change_pct"])

    def test_positive_to_negative(self):
        result = compare_profiles(rows([(7500, 2)]), rows([(7500, -2)]))
        self.assertTrue(result["strikes"][0]["sign_flip"])

    def test_duplicate_strikes_rejected(self):
        with self.assertRaises(ValueError):
            compare_profiles(rows([(7500, 1), (7500, 2)]), rows([(7500, 3)]))

    def test_no_common(self):
        result = compare_profiles(rows([(7500, 1)]), rows([(7600, 1)]))
        self.assertEqual(result["status"], "N/D")


if __name__ == "__main__":
    unittest.main()
