"""Intent-aware profit-floor guard in the spot DCA engine.

A non-urgent limit SELL must not execute below the average cost (a stale or
miscomputed take-profit reference would otherwise realise a loss). Urgent MARKET
exits (STOP, trailing) are exempt and must always run — the "STOP/trailing cannot
be blocked" invariant from the provider-unification plan. Buys are unaffected.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

KRAKEN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kraken")
ROOT = os.path.dirname(KRAKEN_DIR)
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from strategies import spot_dca as strat  # noqa: E402
from providers.strategy_executor import PairPrecision  # noqa: E402


def _make_strategy(**overrides):
    client = MagicMock()
    client.pair_precision.return_value = PairPrecision(2, 6, 0.01, "TEST")
    defaults = dict(
        currency="USD", entry_amount=100.0, entry_discount_pct=0.2, dca_amount=50.0,
        dca_drop_pct=2.0, check_minutes=2.0, takeprofit_pct=1.9, max_budget=1000.0,
        max_dca_buys=10, enable_takeprofit=True, order_ttl_min=10.0, stop_loss_pct=0.0,
        adopt_cost=0.0, adopt_qty=0.0, reentry_drop_pct=2.2, reentry_tolerance_pct=0.05,
        reentry_adaptive=False, reentry_sl_bounce_pct=1.5, tp_tranches=[],
    )
    defaults.update(overrides)
    params = strat.StratParams(**defaults)
    s = strat.Strategy(client, "TESTPAIR_GUARD", params, dry_run=True,
                       initial_state=strat._new_state())
    # avg = cost / qty = 1000 / 10 = 100.0
    s.s["cost"] = 1000.0
    s.s["qty"] = 10.0
    return s


class ProfitFloorGuardTest(unittest.TestCase):
    def test_avg_is_the_reference(self):
        self.assertEqual(_make_strategy()._avg(), 100.0)

    def test_limit_sell_below_avg_is_refused(self):
        self.assertFalse(_make_strategy()._place("sell", 1.0, 95.0, kind="TP"))

    def test_limit_sell_above_avg_is_allowed(self):
        self.assertTrue(_make_strategy()._place("sell", 1.0, 105.0, kind="TP"))

    def test_stop_below_avg_is_exempt(self):
        # A protective STOP must always run, even at a loss.
        self.assertTrue(_make_strategy()._place("sell", 1.0, 90.0, kind="STOP", market=True))

    def test_market_trailing_exit_below_avg_is_exempt(self):
        # An urgent MARKET trailing exit must not be blocked by the floor.
        self.assertTrue(_make_strategy()._place("sell", 1.0, 90.0, kind="TP", market=True))

    def test_buy_below_avg_is_unaffected(self):
        self.assertTrue(_make_strategy()._place("buy", 1.0, 90.0, kind="DCA"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
