"""Trading212 replay rulează motorul live și păstrează contabilitatea parțială."""

import importlib.util
import os
import sys
import unittest
from unittest.mock import MagicMock


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T212_DIR = os.path.join(ROOT, "212trading")
sys.path.insert(0, T212_DIR)
sys.path.insert(0, ROOT)

_COLLIDING = ("strategy", "market_data", "notify", "ipo_notify", "replay")
_PRELOADED = {name: sys.modules.pop(name) for name in _COLLIDING if name in sys.modules}
try:
    spec = importlib.util.spec_from_file_location(
        "t212_replay_under_test", os.path.join(T212_DIR, "replay.py"),
    )
    replay = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = replay
    spec.loader.exec_module(replay)
    strategy = replay._strat
finally:
    for name in _COLLIDING:
        sys.modules.pop(name, None)
    sys.modules.update(_PRELOADED)


def _params(**overrides):
    config = {
        "STRAT_CURRENCY": "USD",
        "YAHOO_SYMBOL": "TEST",
        "STRAT_ENTRY": "100",
        "STRAT_DCA": "50",
        "STRAT_ENTRY_DISCOUNT_PCT": "0.2",
        "STRAT_DCA_DROP_PCT": "2",
        "STRAT_TAKEPROFIT_PCT": "3",
        "STRAT_MAX_DCA_BUYS": "3",
        "STRAT_MAX_BUDGET": "500",
        "STRAT_FX_FEE_PCT": "0.15",
        "STRAT_STOP_LOSS_PCT": "20",
    }
    config.update({key: str(value) for key, value in overrides.items()})
    return strategy.StratParams.from_env(config)


class T212ReplayTest(unittest.TestCase):
    def test_order_decided_at_close_fills_only_in_next_bar(self):
        params = _params()
        first = (100.0, 200.0, 1.0, 100.0)
        one = replay.run_replay([first], params, bar_minutes=1440)
        self.assertEqual(one["fills"], 0)
        self.assertEqual(one["open_qty"], 0.0)

        second = (100.0, 101.0, 99.0, 100.0)
        two = replay.run_replay([first, second], params, bar_minutes=1440)
        self.assertEqual(two["fills"], 1)
        self.assertGreater(two["open_qty"], 0.0)
        self.assertAlmostEqual(two["net_pnl"], two["total"])

    def test_partial_sell_reduces_remaining_cost_basis(self):
        params = _params()
        engine = strategy.Strategy(
            MagicMock(), "TEST_US_EQ", params, dry_run=True,
            initial_state=strategy._new_state(), fx_to_usd=1.0,
        )
        engine.s.update({"qty": 2.0, "cost_usd": 200.0, "spent_cash": 200.0})
        strategy.notify = lambda **_kwargs: None
        engine._apply_fill(
            {"side": "SELL", "kind": "TP", "qty": 1.0, "limit": 110.0},
            1.0, 110.0,
        )
        self.assertEqual(engine.s["qty"], 1.0)
        self.assertAlmostEqual(engine.s["cost_usd"], 100.0)
        self.assertAlmostEqual(engine._avg_cost(), 100.0)

    def test_paper_stop_arms_same_rebuy_as_real_path(self):
        params = _params(STRAT_SL_REBUY_ENABLED="true", STRAT_SL_REBUY_BOUNCE_PCT="1.2")
        engine = strategy.Strategy(
            MagicMock(), "TEST_US_EQ", params, dry_run=True,
            initial_state=strategy._new_state(), fx_to_usd=1.0,
        )
        engine.s.update({
            "qty": 1.0, "cost_usd": 100.0, "spent_cash": 100.0,
            "sl_pending": True,
        })
        strategy.notify = lambda **_kwargs: None
        engine._apply_fill(
            {"side": "SELL", "kind": "SL", "qty": 1.0, "limit": 70.0},
            1.0, 70.0,
        )
        self.assertEqual(engine.s["last_sell_price"], 70.0)
        self.assertEqual(engine.s["sl_rebuy"], {"low": 70.0, "sell_price": 70.0})

    def test_trend_gate_refuses_wrong_cadence(self):
        params = _params(STRAT_DCA_TREND_GATE_PCT="0.1")
        with self.assertRaisesRegex(ValueError, "5 minute"):
            replay.run_replay([(100, 101, 99, 100)], params, bar_minutes=1440)


if __name__ == "__main__":
    unittest.main()
