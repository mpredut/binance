"""The pure spot-DCA rules, shared between live and backtest. It checks the formulas
plus that are_close is IDENTICAL to botcore.are_close (proof that the refactor in strategy.py
does NOT change the LIVE behaviour)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from strategies import spot_dca_rules as sr


class StratRulesTest(unittest.TestCase):
    def test_entry_and_tp(self):
        self.assertAlmostEqual(sr.entry_price(100.0, 0.2), 99.8)
        self.assertAlmostEqual(sr.tp_price(100.0, 3.0), 103.0)

    def test_hit_stop(self):
        self.assertTrue(sr.hit_stop(100.0, 87.0, 12.5))    # -13% >= 12.5
        self.assertFalse(sr.hit_stop(100.0, 90.0, 12.5))   # -10% < 12.5
        self.assertFalse(sr.hit_stop(100.0, 50.0, 0.0))    # sl dezactivat
        self.assertFalse(sr.hit_stop(0.0, 50.0, 12.5))     # A missing avg.

    def test_reentry_stop_bounce(self):
        # min 50.0, bounce 1.5% -> threshold 50.75; below it = blocked
        self.assertTrue(sr.reentry_stop_blocked(50.0, 50.0, 1.5, 0.0))
        self.assertFalse(sr.reentry_stop_blocked(50.8, 50.0, 1.5, 0.0))  # above the threshold -> it enters

    def test_reentry_drop(self):
        # sold at 100, drop 2% -> threshold 98; above it = blocked
        self.assertTrue(sr.reentry_drop_blocked(99.0, 100.0, 2.0, 0.0))
        self.assertFalse(sr.reentry_drop_blocked(97.0, 100.0, 2.0, 0.0))  # Below the threshold -> it enters.
        self.assertFalse(sr.reentry_drop_blocked(99.0, 100.0, 0.0, 0.0))  # drop 0 -> no barrier.
        self.assertFalse(sr.reentry_drop_blocked(99.0, 0.0, 2.0, 0.0))    # A missing last_sell.

    def test_dca_price_hit(self):
        # last_buy 100, drop 2% -> a threshold of 98
        self.assertTrue(sr.dca_price_hit(98.0, 100.0, 2.0, 0.0))    # Exactly at the threshold.
        self.assertTrue(sr.dca_price_hit(97.0, 100.0, 2.0, 0.0))    # Below the threshold.
        self.assertFalse(sr.dca_price_hit(99.0, 100.0, 2.0, 0.0))   # above the threshold, no tolerance
        self.assertTrue(sr.dca_price_hit(98.04, 100.0, 2.0, 0.05))  # in toleranta 0.05%

    def test_progressive_dca_spacing_is_safe_and_zero_preserves_live(self):
        self.assertEqual(sr.progressive_dca_drop_pct(1.25, 0.0, 9), 1.25)
        self.assertEqual(sr.progressive_dca_drop_pct(1.25, 0.25, 0), 1.25)
        self.assertEqual(sr.progressive_dca_drop_pct(1.25, 0.25, 4), 2.25)
        self.assertEqual(sr.progressive_dca_drop_pct(1.25, -1.0, 4), 1.25)

    def test_are_close_is_identical_to_botcore(self):
        import botcore
        for a, b, tol in [(50.0, 50.75, 0.05), (65.93, 65.91, 0.05), (100.0, 98.0, 0.0),
                          (99.0, 100.0, 2.0), (0.0, 0.0, 0.05), (58.42, 58.47, 0.05)]:
            self.assertEqual(sr.are_close(a, b, tol), botcore.are_close(a, b, tol),
                             f"divergenta la are_close({a},{b},{tol})")


if __name__ == "__main__":
    unittest.main(verbosity=2)
