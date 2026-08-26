import copy
import os
import unittest
from unittest import mock

os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import assetguardian as ag


class MemoryState:
    def __init__(self, value=None):
        self.value = copy.deepcopy(value or {})

    def load(self):
        return copy.deepcopy(self.value)

    def save(self, value):
        self.value = copy.deepcopy(value)


class AssetGuardianTest(unittest.TestCase):
    def setUp(self):
        ag._last_evaluation.clear()
        ag._last_evaluation.update({
            symbol: {"drawdown": None, "pending_tier": False}
            for symbol in ag.TRACKED_SYMBOLS
        })
        ag._local_price_samples.clear()
        ag._local_price_samples.update({symbol: [] for symbol in ag.TRACKED_SYMBOLS})

    def test_symbol_window_normalizes_ms_and_returns_price_min_max(self):
        now = 2_000_000_000
        rows = [
            [(now - 120) * 1000, 90],
            [(now - 60) * 1000, 110],
            [(now - 30) * 1000, 100],
        ]
        with mock.patch.object(ag, "_read_symbol_price_rows", return_value=rows), \
             mock.patch.object(ag.time, "time", return_value=now):
            minimum, maximum = ag._get_symbol_window_extrema("TAOUSDC", 105, 10)
        self.assertEqual(minimum["price"], 90.0)
        self.assertEqual(maximum["price"], 110.0)

    def test_symbol_window_skips_malformed_nonfinite_stale_and_future_rows(self):
        now = 2_000_000_000
        rows = [
            ["bad", 1],
            [(now - 50) * 1000, "NaN"],
            [(now - 40) * 1000, "Infinity"],
            [(now - 700) * 1000, 10],
            [(now + 1) * 1000, 500],
            [(now - 30) * 1000, 100],
            [(now - 20) * 1000, 120],
        ]
        with mock.patch.object(ag, "_read_symbol_price_rows", return_value=rows), \
             mock.patch.object(ag.time, "time", return_value=now):
            minimum, maximum = ag._get_symbol_window_extrema("TAOUSDC", 110, 10)
        self.assertEqual(minimum["price"], 100.0)
        self.assertEqual(maximum["price"], 120.0)

    def test_tao_drawdown_triggers_tao_buy_not_btc(self):
        extrema = (
            {"timestamp": 1, "price": 224.0},
            {"timestamp": 2, "price": 242.0},
        )
        provider = mock.Mock()
        provider.free_balance.return_value = 1000.0
        state = MemoryState()
        with mock.patch.object(ag.api, "get_current_price", return_value=225.0), \
             mock.patch.object(ag, "_get_symbol_window_extrema", return_value=extrema), \
             mock.patch.object(ag, "STATE", state), \
             mock.patch.object(ag.mkt, "provider_by_name", return_value=provider), \
             mock.patch.object(ag, "buy_with_all_cash", return_value=True) as buy, \
             mock.patch.object(ag, "sell_asset") as sell:
            self.assertTrue(ag.evaluate_symbol("TAOUSDC", threshold_percent=100))
        buy.assert_called_once_with(buy_symbol="TAOUSDC", cash_amount=348.25)
        sell.assert_not_called()
        self.assertEqual(
            state.value["symbols"]["TAOUSDC"]["completed_tiers"], [7.0])

    def test_two_asset_replay_routes_only_tao_drawdown_to_tao(self):
        state = MemoryState()
        provider = mock.Mock()
        provider.free_balance.return_value = 1000.0
        prices = {"BTCUSDC": 100.0, "TAOUSDC": 225.0}
        extrema = {
            "BTCUSDC": (
                {"timestamp": 1, "price": 99.0},
                {"timestamp": 2, "price": 101.0},
            ),
            "TAOUSDC": (
                {"timestamp": 1, "price": 224.0},
                {"timestamp": 2, "price": 242.0},
            ),
        }
        with mock.patch.object(
                ag.api, "get_current_price", side_effect=lambda symbol: prices[symbol]), \
             mock.patch.object(
                 ag, "_get_symbol_window_extrema",
                 side_effect=lambda symbol, *_args, **_kwargs: extrema[symbol]), \
             mock.patch.object(ag, "STATE", state), \
             mock.patch.object(ag.mkt, "provider_by_name", return_value=provider), \
             mock.patch.object(ag, "buy_with_all_cash", return_value=True) as buy, \
             mock.patch.object(ag, "sell_asset") as sell:
            self.assertTrue(ag.evaluate_and_maybe_sell_or_buy(
                threshold_percent=100, symbols=("BTCUSDC", "TAOUSDC")))
        buy.assert_called_once_with(buy_symbol="TAOUSDC", cash_amount=348.25)
        sell.assert_not_called()

    def test_tao_growth_sells_only_tao(self):
        extrema = (
            {"timestamp": 1, "price": 100.0},
            {"timestamp": 2, "price": 210.0},
        )
        with mock.patch.object(ag.api, "get_current_price", return_value=205.0), \
             mock.patch.object(ag, "_get_symbol_window_extrema", return_value=extrema), \
             mock.patch.object(ag, "sell_asset", return_value=True) as sell, \
             mock.patch.object(ag, "buy_with_all_cash") as buy:
            self.assertTrue(ag.evaluate_symbol("TAOUSDC", threshold_percent=100))
        sell.assert_called_once_with("TAOUSDC", current_price=205.0)
        buy.assert_not_called()

    def test_completed_tier_is_isolated_per_symbol(self):
        state = MemoryState({
            "version": 2,
            "symbols": {
                "TAOUSDC": {
                    "peak_price": 242, "peak_ts": 2, "initial_cash": 1000,
                    "completed_tiers": [7],
                }
            },
        })
        maximum = {"timestamp": 2, "price": 242}
        with mock.patch.object(ag, "STATE", state):
            tao_tier, _ = ag._campaign_tier("TAOUSDC", 8, maximum, 650)
            btc_tier, _ = ag._campaign_tier("BTCUSDC", 8, maximum, 650)
        self.assertIsNone(tao_tier)
        self.assertEqual(btc_tier, (7.0, 0.35))
        self.assertEqual(
            state.value["symbols"]["TAOUSDC"]["completed_tiers"], [7])

    def test_higher_peak_does_not_repeat_completed_tier_or_replenish_budget(self):
        state = MemoryState({
            "version": 2,
            "symbols": {
                "TAOUSDC": {
                    "peak_price": 242, "peak_ts": 2, "initial_cash": 1000,
                    "completed_tiers": [7],
                }
            },
        })
        with mock.patch.object(ag, "STATE", state):
            tier, campaign = ag._campaign_tier(
                "TAOUSDC", 10.5, {"timestamp": 3, "price": 250}, 500)
        self.assertEqual(tier, (10.0, 0.35))
        self.assertEqual(campaign["completed_tiers"], [7])
        self.assertEqual(campaign["initial_cash"], 1000)
        self.assertEqual(campaign["peak_price"], 250)

    def test_legacy_global_campaign_maps_only_to_btc(self):
        state = MemoryState({
            "peak_value": 100,
            "peak_ts": 2,
            "initial_cash": 1000,
            "completed_tiers": [7],
        })
        with mock.patch.object(ag, "STATE", state):
            root = ag._load_state_root()
            tier, campaign = ag._campaign_tier(
                ag.LEGACY_BUY_SYMBOL_DEFAULT, 8,
                {"timestamp": 3, "price": 100}, 500)
        self.assertIn(ag.LEGACY_BUY_SYMBOL_DEFAULT, root["symbols"])
        self.assertNotIn("TAOUSDC", root["symbols"])
        self.assertIsNone(tier)
        self.assertEqual(campaign["completed_tiers"], [7.0])

    def test_same_tao_tier_is_not_repeated(self):
        state = MemoryState()
        extrema = (
            {"timestamp": 1, "price": 224.0},
            {"timestamp": 2, "price": 242.0},
        )
        provider = mock.Mock()
        provider.free_balance.return_value = 1000.0
        with mock.patch.object(ag.api, "get_current_price", return_value=225.0), \
             mock.patch.object(ag, "_get_symbol_window_extrema", return_value=extrema), \
             mock.patch.object(ag, "STATE", state), \
             mock.patch.object(ag.mkt, "provider_by_name", return_value=provider), \
             mock.patch.object(ag, "buy_with_all_cash", return_value=True) as buy:
            self.assertTrue(ag.evaluate_symbol("TAOUSDC"))
            self.assertFalse(ag.evaluate_symbol("TAOUSDC"))
        self.assertEqual(buy.call_count, 1)

    def test_refused_tier_is_recalculated_and_uses_fast_interval(self):
        state = MemoryState()
        extrema = (
            {"timestamp": 1, "price": 224.0},
            {"timestamp": 2, "price": 242.0},
        )
        provider = mock.Mock()
        provider.free_balance.return_value = 1000.0
        with mock.patch.object(ag.api, "get_current_price", return_value=225.0), \
             mock.patch.object(ag, "_get_symbol_window_extrema", return_value=extrema), \
             mock.patch.object(ag, "STATE", state), \
             mock.patch.object(ag.mkt, "provider_by_name", return_value=provider), \
             mock.patch.object(ag, "buy_with_all_cash", return_value=False):
            self.assertFalse(ag.evaluate_symbol("TAOUSDC"))
            self.assertEqual(ag._next_check_seconds(), ag.ACTIVE_TRIGGER_SECONDS)
        self.assertEqual(
            state.value["symbols"]["TAOUSDC"]["completed_tiers"], [])

    def test_near_tier_interval_is_computed_per_symbol(self):
        state = MemoryState({
            "version": 2,
            "symbols": {"TAOUSDC": {"completed_tiers": [7]}},
        })
        ag._last_evaluation["TAOUSDC"] = {
            "drawdown": -8.5, "pending_tier": False,
        }
        with mock.patch.object(ag, "STATE", state):
            self.assertEqual(ag._next_check_seconds(), ag.NEAR_TRIGGER_SECONDS)

    def test_evaluator_accepts_at_most_one_order_per_cycle(self):
        with mock.patch.object(ag, "evaluate_symbol", side_effect=[True, True]) as evaluate:
            self.assertTrue(ag.evaluate_and_maybe_sell_or_buy(
                symbols=("BTCUSDC", "TAOUSDC")))
        self.assertEqual(evaluate.call_count, 1)

    def test_guardian_owns_retry_for_buy(self):
        provider = mock.Mock()
        provider.free_balance.return_value = 1000.0
        with mock.patch.object(ag.mkt, "provider_by_name", return_value=provider), \
             mock.patch.object(ag.api, "get_current_price", return_value=100.0), \
             mock.patch.object(ag.mkt, "place", return_value={"orderId": 7}) as place:
            self.assertTrue(ag.buy_with_all_cash("TAOUSDC", cash_ratio=0.5))
        self.assertTrue(place.call_args.kwargs["caller_owns_retry"])
        self.assertEqual(place.call_args.kwargs["motivation"],
                         "assetguardian_drawdown_buy")
        self.assertEqual(place.call_args.args[0], "TAOUSDC")

    def test_guardian_owns_retry_for_asset_specific_sell(self):
        provider = mock.Mock()
        provider.free_balance.return_value = 0.5
        with mock.patch.object(ag.mkt, "provider_by_name", return_value=provider), \
             mock.patch.object(ag.mkt, "place", return_value={"orderId": 8}) as place:
            self.assertTrue(ag.sell_asset("TAOUSDC", current_price=250.0))
        self.assertTrue(place.call_args.kwargs["caller_owns_retry"])
        self.assertEqual(place.call_args.kwargs["motivation"],
                         "assetguardian_growth_exit")
        self.assertEqual(place.call_args.args[:4], ("TAOUSDC", "SELL", 250.0, 0.5))

    def test_nonfinite_current_price_cannot_trigger_any_order(self):
        with mock.patch.object(ag.api, "get_current_price", return_value=float("inf")), \
             mock.patch.object(ag, "sell_asset") as sell, \
             mock.patch.object(ag, "buy_with_all_cash") as buy:
            self.assertFalse(ag.evaluate_symbol("TAOUSDC"))
        sell.assert_not_called()
        buy.assert_not_called()

    def test_insufficient_price_baseline_cannot_trigger_any_order(self):
        now = 2_000_000
        with mock.patch.object(ag, "_read_symbol_price_rows", return_value=[]), \
             mock.patch.object(ag.time, "time", return_value=now), \
             mock.patch.object(ag.api, "get_current_price", return_value=225.0), \
             mock.patch.object(ag, "sell_asset") as sell, \
             mock.patch.object(ag, "buy_with_all_cash") as buy:
            self.assertFalse(ag.evaluate_symbol("TAOUSDC"))
        sell.assert_not_called()
        buy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
