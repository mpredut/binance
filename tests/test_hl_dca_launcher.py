import importlib.util
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

SPEC = importlib.util.spec_from_file_location(
    "repo_hl_dca_bot", os.path.join(ROOT, "hyperliquid", "hl_dca_bot.py")
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
state_dir_for = MODULE.state_dir_for


class HLDcaLauncherTest(unittest.TestCase):
    def test_live_state_is_hyperliquid_scoped(self):
        self.assertEqual(state_dir_for(False), os.path.join(ROOT, "hyperliquid"))

    def test_paper_state_is_separate_from_live(self):
        self.assertEqual(
            state_dir_for(True), os.path.join(ROOT, "hyperliquid", ".paper_state")
        )
        self.assertNotEqual(state_dir_for(True), state_dir_for(False))


if __name__ == "__main__":
    unittest.main()
