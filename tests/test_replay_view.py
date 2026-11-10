"""Replay: fascia visiva 3%/10%, zoom stabile, core 50 immutato."""
import unittest
from copy import deepcopy
from datetime import date

from replay_view import (
    plotted_rows, price_zoom_bounds, coverage_note,
    full_pool_from_archived_cboe,
)

REF = 7500.0


def make_profile():
    core = [
        {"strike_spx": float(k), "net_gamma": float((k - REF) / 20)}
        for k in range(7375, 7630, 5) if k != REF
    ]
    assert len(core) == 50
    all_strikes = [
        {"strike_spx": float(k),
         "net_gamma": float((k - REF) / 20)}
        for k in range(6900, 8105, 5) if k != REF
    ]
    return {
        "rows": core, "visual_rows": all_strikes[70:210],
        "visual_pool_rows": all_strikes,
        "visual_pool_truncated": False,
    }


class ReplayBandTests(unittest.TestCase):
    def test_three_percent_default_and_wider_five_and_ten(self):
        prof = make_profile()
        original = deepcopy(prof["rows"])
        count3 = plotted_rows(prof, REF, 7540, 3, True)
        count5 = plotted_rows(prof, REF, 7540, 5, True)
        count10 = plotted_rows(prof, REF, 7540, 10, True)
        self.assertGreater(len(count3), 50)
        self.assertGreater(len(count5), len(count3))
        self.assertGreater(len(count10), len(count5))
        self.assertEqual(prof["rows"], original)
        self.assertTrue(all(
            REF*0.97 <= float(r["strike_spx"]) <= REF*1.03 for r in count3
        ))

    def test_automatic_extension_uses_only_real_strikes_near_spot(self):
        prof = make_profile()
        without_auto = plotted_rows(prof, REF, 7950, 3, False)
        with_auto = plotted_rows(prof, REF, 7950, 3, True)
        self.assertNotIn(7950.0, {r["strike_spx"] for r in without_auto})
        self.assertIn(7950.0, {r["strike_spx"] for r in with_auto})
        all_strikes = {r["strike_spx"] for r in prof["visual_pool_rows"]}
        self.assertTrue({r["strike_spx"] for r in with_auto} <= all_strikes)

    def test_legacy_archive_does_not_invent_unarchived_levels(self):
        profile = make_profile()
        profile.pop("visual_pool_rows")
        old = {r["strike_spx"] for r in profile["visual_rows"]}
        rows = plotted_rows(profile, REF, 7950, 10, True)
        self.assertTrue({r["strike_spx"] for r in rows} <= old)
        note = coverage_note(profile, REF, 10, True, 7950)
        self.assertEqual(note["origin"], "profilo storico limitato")

    def test_corrupt_pool_is_rejected_and_falls_back(self):
        profile = make_profile()
        profile["visual_pool_rows"][0]["net_gamma"] = 999999.0
        # Corrupted row outside the original core is not an integrity breach;
        # corrupt an actual core strike instead.
        profile["visual_pool_rows"] = [
            dict(r, net_gamma=999999.) if r["strike_spx"] == 7475.
            else r for r in profile["visual_pool_rows"]
        ]
        rows = plotted_rows(profile, REF, 7550, 10, True)
        legacy = {r["strike_spx"] for r in profile["visual_rows"]}
        self.assertTrue({r["strike_spx"] for r in rows} <= legacy)

    def test_original_mode_always_preserves_all_fifty(self):
        profile = make_profile()
        rows = plotted_rows(profile, REF, 8000, 0.5, True, extended=False)
        self.assertEqual(rows, profile["rows"])

    def test_auto_zoom_tracks_prices_not_full_strike_pool(self):
        spots = [7500, 7560, 7525]
        levels = [6900, 7000, 8100]
        focused = price_zoom_bounds(
            spots, [7480], [7585], levels, REF, complete=False)
        full = price_zoom_bounds(
            spots, [7480], [7585], levels, REF, complete=True)
        self.assertLess(focused[1] - focused[0], 200)
        self.assertLessEqual(full[0], 6900)
        self.assertGreaterEqual(full[1], 8100)
        self.assertLess(focused[0], 7480)
        self.assertGreater(focused[1], 7585)

    def test_archive_full_raw_can_supply_more_than_140_authentic_strikes(self):
        raw = {"data": {"options": []}}
        expiry = date(2026, 11, 9)
        for strike in range(6900, 8105, 5):
            if strike == REF:
                continue
            raw["data"]["options"].append({
                "option": f"SPXW261109C{strike*1000:08d}",
                "gamma": 0.02, "open_interest": 10, "volume": 0,
            })
        base = [{"strike_spx": float(k), "net_gamma": 0.2}
                for k in range(7375, 7630, 5) if k != REF]
        full = full_pool_from_archived_cboe(raw, expiry, REF, base, 7540)
        self.assertGreater(len(full["visual_pool_rows"]), 140)
        self.assertEqual(len(full["visual_pool_rows"]), 240)
        self.assertEqual([r for r in base if r["strike_spx"] == 7475.][0],
                         [r for r in full["visual_pool_rows"]
                          if r["strike_spx"] == 7475.][0])
        prof = {"rows": base, "visual_rows": base, **full}
        rows = plotted_rows(prof, REF, 7540, 5, True)
        self.assertGreater(len(rows), 140)


if __name__ == "__main__":
    unittest.main()
