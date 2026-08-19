"""Caracterizare pentru overlay-ul de trend Kraken.

Overlay-ul este experimental și implicit oprit, dar trebuie să păstreze aceleași
invariante financiare ca motorul range: ordinul nu este poziție până la fill,
stop-loss-ul are prioritate și paper-live citește barele OHLC reale.
"""
import importlib.util
import os
import sys
import unittest
from unittest.mock import MagicMock, patch


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KRAKEN_DIR = os.path.join(ROOT, "kraken")
sys.path.insert(0, KRAKEN_DIR)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

_COLLIDING_MODULES = ("market_data", "notify")
_PRELOADED_MODULES = {
    name: sys.modules.pop(name) for name in _COLLIDING_MODULES if name in sys.modules
}
try:
    _SPEC = importlib.util.spec_from_file_location(
        "kraken_strategy_overlay_under_test", os.path.join(KRAKEN_DIR, "strategy.py")
    )
    strat = importlib.util.module_from_spec(_SPEC)
    sys.modules[_SPEC.name] = strat
    _SPEC.loader.exec_module(strat)
finally:
    for _name in _COLLIDING_MODULES:
        sys.modules.pop(_name, None)
    sys.modules.update(_PRELOADED_MODULES)


def _make_strategy(*, replay_mode=False, **overrides):
    client = MagicMock()
    client.pair_info.return_value = None
    defaults = dict(
        currency="USD", entry_amount=100.0, entry_discount_pct=0.2,
        dca_amount=50.0, dca_drop_pct=2.0, check_minutes=2.0,
        takeprofit_pct=5.0, max_budget=1000.0, max_dca_buys=10,
        enable_takeprofit=True, order_ttl_min=10.0, stop_loss_pct=12.5,
        adopt_cost=0.0, adopt_qty=0.0, reentry_drop_pct=0.0,
        reentry_tolerance_pct=0.0, reentry_adaptive=False,
        reentry_sl_bounce_pct=1.5, tp_tranches=[], trend_overlay=True,
        trend_sma_n=2, trend_interval=240, trend_confirm_bars=1,
        trend_topup=400.0, trend_trail_pct=5.0, trend_exit_break=False,
    )
    defaults.update(overrides)
    params = strat.StratParams(**defaults)
    strategy = strat.Strategy(
        client, "TESTPAIR_OVERLAY", params, dry_run=True,
        initial_state=strat._new_state(), replay_mode=replay_mode,
    )
    strategy._save = lambda: None
    return strategy


class TrendOverlayTest(unittest.TestCase):
    def test_topup_enters_trend_mode_only_after_fill(self):
        s = _make_strategy(replay_mode=True)
        s._shadow_prices.extend([(1.0, 100.0), (2.0, 101.0)])

        s.step(102.0)

        order = s._find_open("buy")
        self.assertIsNotNone(order)
        self.assertEqual(order["kind"], "TREND_ENTRY")
        self.assertFalse(s.s["trend_mode"], "un ordin neexecutat nu este încă poziție de trend")

        s._remove(order)
        with patch.object(strat, "notify"):
            s._apply_fill(order, order["vol"], order["price"], fee=0.0)
        self.assertTrue(s.s["trend_mode"])
        self.assertEqual(s.s["trend_peak"], order["price"])

    def test_unfilled_topup_is_cancelled_if_signal_disappears(self):
        s = _make_strategy(replay_mode=True)
        closes = [[100.0, 101.0, 102.0]]
        with patch.object(s, "_trend_closes", side_effect=lambda: closes[0]):
            s.step(102.0)
            self.assertEqual(s._find_open("buy")["kind"], "TREND_ENTRY")

            closes[0] = [102.0, 101.0, 100.0]
            s.step(100.0)

        order = s._find_open("buy")
        self.assertIsNotNone(order, "după anularea top-up-ului revine la logica range")
        self.assertEqual(order["kind"], "ENTRY")
        self.assertFalse(s.s["trend_mode"])

    def test_stop_loss_has_priority_over_overlay(self):
        s = _make_strategy(replay_mode=True, stop_loss_pct=10.0, trend_trail_pct=50.0)
        s.s.update({
            "qty": 1.0, "cost": 100.0, "spent": 100.0,
            "entry_price": 100.0, "last_buy_price": 100.0,
            "trend_mode": True, "trend_peak": 100.0,
        })
        s._shadow_prices.extend([(1.0, 100.0), (2.0, 101.0)])

        s.step(80.0)

        sell = s._find_open("sell")
        self.assertIsNotNone(sell)
        self.assertEqual(sell["kind"], "STOP")

    def test_paper_live_uses_client_ohlc_not_tick_shadow(self):
        s = _make_strategy(replay_mode=False)
        s.client.ohlc_closes.return_value = [10.0, 11.0, 12.0]
        s._shadow_prices.extend([(1.0, 99.0), (2.0, 98.0)])

        self.assertEqual(s._trend_closes(), [10.0, 11.0, 12.0])
        s.client.ohlc_closes.assert_called_once_with("TESTPAIR_OVERLAY", 240)

    def test_step_uses_injected_replay_timestamp(self):
        s = _make_strategy(replay_mode=True, trend_overlay=False)
        s.step(100.0, timestamp=14_400.0)
        self.assertEqual(s._shadow_prices[-1], (14_400.0, 100.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
