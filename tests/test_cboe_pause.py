"""SPX direct/manual Cboe requests are paused, local import remains."""
import unittest
from unittest.mock import patch

from cboe_acquisition_pause import CBOE_ACQUISITIONS_PAUSED, PAUSE_MESSAGE
from emergency_pine import download_current_snapshot
from manual_acquisition import dispatch_snapshot


class SuspensionTests(unittest.TestCase):
    def test_direct_and_github_dispatch_never_use_network(self):
        self.assertTrue(CBOE_ACQUISITIONS_PAUSED)
        with patch("emergency_pine.urlopen") as cboe, \
             patch("manual_acquisition.urlopen") as github:
            with self.assertRaisesRegex(RuntimeError, "sospese"):
                download_current_snapshot()
            with self.assertRaisesRegex(RuntimeError, "sospese"):
                dispatch_snapshot("dummy", "GrespoSan/Serafini-SPX-Cloud")
            cboe.assert_not_called()
            github.assert_not_called()

    def test_local_snapshot_import_remains_supported(self):
        from pathlib import Path
        source = (Path(__file__).resolve().parents[1] / "emergency_pine.py").read_text()
        self.assertIn('if st.button("Usa file importato"', source)
        self.assertIn("disabled=CBOE_ACQUISITIONS_PAUSED", source)


if __name__ == "__main__":
    unittest.main()
