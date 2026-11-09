"""Replay NET GAMMA: lo zero deve restare centrale in ogni frame."""

import math
import unittest

from replay_scale import dynamic_gamma_bounds


class ReplayZeroAxisTests(unittest.TestCase):
    def test_zero_stays_centered_for_positive_negative_and_mixed_profiles(self):
        profiles = [
            [0, 20, 60, 5],
            [0, -25, -120, -1],
            [-200, 50, 1, 0],
            [-4, 0, 8, 2],
            [0, 0, 0],
        ]
        for values in profiles:
            with self.subTest(values=values):
                left, right = dynamic_gamma_bounds(values)
                self.assertAlmostEqual(left, -right, places=12)
                self.assertLess(left, 0)
                self.assertGreater(right, 0)
                self.assertGreaterEqual(right, max(abs(x) for x in values))
                position_zero = (0 - left) / (right - left)
                self.assertAlmostEqual(position_zero, 0.5, places=12)

    def test_larger_gamma_changes_scale_but_not_axis_position(self):
        a = dynamic_gamma_bounds([-3, 15])
        b = dynamic_gamma_bounds([-300, 2])
        self.assertNotEqual(a, b)
        self.assertEqual(-a[0], a[1])
        self.assertEqual(-b[0], b[1])

    def test_rejects_empty_or_nonfinite(self):
        for values in ([], [math.nan], [math.inf], [-math.inf, 1]):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    dynamic_gamma_bounds(values)


if __name__ == "__main__":
    unittest.main()
