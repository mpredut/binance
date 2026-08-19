"""kraken/strat_rules.py — reguli pure partajate live<->backtest. Verifica formulele
+ ca are_close e IDENTIC cu botcore.are_close (dovada ca refactorul din strategy.py
NU schimba comportamentul LIVE)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kraken"))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import strat_rules as sr


class StratRulesTest(unittest.TestCase):
    def test_entry_and_tp(self):
        self.assertAlmostEqual(sr.entry_price(100.0, 0.2), 99.8)
        self.assertAlmostEqual(sr.tp_price(100.0, 3.0), 103.0)

    def test_hit_stop(self):
        self.assertTrue(sr.hit_stop(100.0, 87.0, 12.5))    # -13% >= 12.5
        self.assertFalse(sr.hit_stop(100.0, 90.0, 12.5))   # -10% < 12.5
        self.assertFalse(sr.hit_stop(100.0, 50.0, 0.0))    # sl dezactivat
        self.assertFalse(sr.hit_stop(0.0, 50.0, 12.5))     # avg lipsa

    def test_reentry_stop_bounce(self):
        # min 50.0, bounce 1.5% -> prag 50.75; sub prag = blocat
        self.assertTrue(sr.reentry_stop_blocked(50.0, 50.0, 1.5, 0.0))
        self.assertFalse(sr.reentry_stop_blocked(50.8, 50.0, 1.5, 0.0))  # peste prag -> intra

    def test_reentry_drop(self):
        # vandut 100, drop 2% -> prag 98; peste prag = blocat
        self.assertTrue(sr.reentry_drop_blocked(99.0, 100.0, 2.0, 0.0))
        self.assertFalse(sr.reentry_drop_blocked(97.0, 100.0, 2.0, 0.0))  # sub prag -> intra
        self.assertFalse(sr.reentry_drop_blocked(99.0, 100.0, 0.0, 0.0))  # drop 0 -> fara bariera
        self.assertFalse(sr.reentry_drop_blocked(99.0, 0.0, 2.0, 0.0))    # last_sell lipsa

    def test_dca_price_hit(self):
        # last_buy 100, drop 2% -> prag 98
        self.assertTrue(sr.dca_price_hit(98.0, 100.0, 2.0, 0.0))    # exact la prag
        self.assertTrue(sr.dca_price_hit(97.0, 100.0, 2.0, 0.0))    # sub prag
        self.assertFalse(sr.dca_price_hit(99.0, 100.0, 2.0, 0.0))   # peste prag, fara tol
        self.assertTrue(sr.dca_price_hit(98.04, 100.0, 2.0, 0.05))  # in toleranta 0.05%

    def test_are_close_identic_cu_botcore(self):
        import botcore
        for a, b, tol in [(50.0, 50.75, 0.05), (65.93, 65.91, 0.05), (100.0, 98.0, 0.0),
                          (99.0, 100.0, 2.0), (0.0, 0.0, 0.05), (58.42, 58.47, 0.05)]:
            self.assertEqual(sr.are_close(a, b, tol), botcore.are_close(a, b, tol),
                             f"divergenta la are_close({a},{b},{tol})")


if __name__ == "__main__":
    unittest.main(verbosity=2)
