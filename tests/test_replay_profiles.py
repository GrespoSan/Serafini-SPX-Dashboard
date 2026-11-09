"""Replay: estensione grafica Cboe archiviata senza contaminare il 25+25."""
import unittest

from replay_profiles import choose_rows, union_strikes


def frame(with_extension=True):
    core = [{"strike_spx": float(7300 + 5*i), "net_gamma": float(i - 25)}
            for i in range(50)]
    extra = list(core) + [
        {"strike_spx": float(7550 + 5*i), "net_gamma": float(20 + i)}
        for i in range(30)
    ]
    return {"rows": core, "visual_rows": extra if with_extension else []}


class ReplayProfileTests(unittest.TestCase):
    def test_extended_mode_recovers_levels_above_original_50(self):
        item = frame()
        self.assertEqual(len(choose_rows(item, True)), 80)
        self.assertEqual(max(r["strike_spx"] for r in choose_rows(item, False)), 7545.)
        self.assertEqual(max(r["strike_spx"] for r in choose_rows(item, True)), 7695.)
        self.assertEqual(len(item["rows"]), 50)

    def test_older_compact_without_extension_falls_back_to_real_50(self):
        self.assertEqual(len(choose_rows(frame(False), True)), 50)

    def test_inconsistent_extension_cannot_overwrite_core_gamma(self):
        item = frame()
        item["visual_rows"][0] = {"strike_spx": 7300.0, "net_gamma": 9999.}
        self.assertEqual(choose_rows(item, True), item["rows"])

    def test_disjoint_or_duplicate_strikes_reject_extension(self):
        item = frame()
        item["visual_rows"][0] = {"strike_spx": 6995., "net_gamma": 50.}
        self.assertEqual(choose_rows(item, True), item["rows"])
        item = frame()
        item["visual_rows"].append(dict(item["visual_rows"][-1]))
        self.assertEqual(choose_rows(item, True), item["rows"])

    def test_more_than_140_cannot_pass_archive_visual_limit(self):
        item = frame()
        item["visual_rows"] += [
            {"strike_spx": float(7700+i*5), "net_gamma": 1.}
            for i in range(70)
        ]
        self.assertEqual(len(choose_rows(item, True)), 50)

    def test_union_does_not_invent_levels_between_snapshot_grids(self):
        old = frame(False)
        newest = frame()
        combined = union_strikes([old, newest], extended=True)
        self.assertEqual(len(combined), 80)
        self.assertNotIn(7547.5, combined)


if __name__ == "__main__":
    unittest.main()
