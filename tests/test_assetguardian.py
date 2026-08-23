import os
import unittest
from unittest import mock

os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import assetguardian as ag


class MemoryState:
    def __init__(self, value=None):
        self.value = dict(value or {})

    def load(self):
        return dict(self.value)

    def save(self, value):
        self.value = dict(value)


class AssetGuardianTest(unittest.TestCase):
    def test_window_uses_legacy_rows_and_returns_min_max(self):
        now = 2_000_000
        rows = [
            {"timestamp": now - 120, "total_value_usdt": 90},
            {"timestamp": now - 60, "total_value_usdc": 110},
            {"timestamp": now - 30, "total_value_usdc": 100},
        ]
        with mock.patch.object(ag, "_read_cache_rows", return_value=rows), \
             mock.patch.object(ag.time, "time", return_value=now):
            minimum, maximum = ag._get_window_extrema_from_cache(10)
        self.assertEqual(ag._row_value_usdc(minimum), 90.0)
        self.assertEqual(ag._row_value_usdc(maximum), 110.0)

    def test_window_skips_malformed_nonfinite_and_future_rows(self):
        now = 2_000_000
        rows = [
            {"timestamp": "bad", "total_value_usdc": 1},
            {"timestamp": now - 50, "total_value_usdc": "NaN"},
            {"timestamp": now - 40, "total_value_usdc": "Infinity"},
            {"timestamp": now + 1, "total_value_usdc": 500},
            {"timestamp": now - 30, "total_value_usdc": 100},
            {"timestamp": now - 20, "total_value_usdc": 120},
        ]
        with mock.patch.object(ag, "_read_cache_rows", return_value=rows), \
             mock.patch.object(ag.time, "time", return_value=now):
            minimum, maximum = ag._get_window_extrema_from_cache(10)
        self.assertEqual(ag._row_value_usdc(minimum), 100.0)
        self.assertEqual(ag._row_value_usdc(maximum), 120.0)

    def test_drawdown_is_measured_from_window_maximum(self):
        extrema = (
            {"timestamp": 1, "total_value_usdc": 80},
            {"timestamp": 2, "total_value_usdc": 100},
        )
        provider = mock.Mock()
        provider.free_balance.return_value = 1000.0
        with mock.patch.object(ag.api, "get_total_assets_value_usdc", return_value=92), \
             mock.patch.object(ag, "_get_window_extrema_from_cache", return_value=extrema), \
             mock.patch.object(ag, "STATE", MemoryState()), \
             mock.patch.object(ag.mkt, "provider_by_name", return_value=provider), \
             mock.patch.object(ag, "buy_with_all_cash", return_value=True) as buy, \
             mock.patch.object(ag, "sell_all_assets") as sell:
            self.assertTrue(ag.evaluate_and_maybe_sell_or_buy(
                threshold_percent=100, drop_percent=7))
        buy.assert_called_once()
        sell.assert_not_called()

    def test_completed_tier_is_not_repeated_in_same_campaign(self):
        maximum = {"timestamp": 2, "total_value_usdc": 100}
        state = MemoryState({
            "peak_value": 100, "peak_ts": 2, "initial_cash": 1000,
            "completed_tiers": [7],
        })
        with mock.patch.object(ag, "STATE", state):
            tier, _ = ag._campaign_tier(8, maximum, 650)
        self.assertIsNone(tier)

    def test_refused_tier_uses_fast_interval(self):
        state = MemoryState()
        with mock.patch.object(ag, "STATE", state):
            ag._last_evaluation.update(drawdown=-7.2, pending_tier=True)
            self.assertEqual(ag._next_check_seconds(), ag.ACTIVE_TRIGGER_SECONDS)

    def test_near_next_tier_uses_near_interval(self):
        state = MemoryState({"completed_tiers": [7]})
        with mock.patch.object(ag, "STATE", state):
            ag._last_evaluation.update(drawdown=-8.5, pending_tier=False)
            self.assertEqual(ag._next_check_seconds(), ag.NEAR_TRIGGER_SECONDS)

    def test_guardian_owns_retry_for_buy(self):
        provider = mock.Mock()
        provider.free_balance.return_value = 1000.0
        with mock.patch.object(ag.mkt, "provider_by_name", return_value=provider), \
             mock.patch.object(ag.api, "get_current_price", return_value=100.0), \
             mock.patch.object(ag.mkt, "place", return_value={"orderId": 7}) as place:
            self.assertTrue(ag.buy_with_all_cash("BTCUSDC", cash_ratio=0.5))
        self.assertTrue(place.call_args.kwargs["caller_owns_retry"])
        self.assertEqual(place.call_args.kwargs["motivation"],
                         "assetguardian_drawdown_buy")

    def test_buy_rejects_nonfinite_balance(self):
        provider = mock.Mock()
        provider.free_balance.return_value = float("nan")
        with mock.patch.object(ag.mkt, "provider_by_name", return_value=provider), \
             mock.patch.object(ag.api, "get_current_price", return_value=100.0), \
             mock.patch.object(ag.mkt, "place") as place:
            self.assertFalse(ag.buy_with_all_cash("BTCUSDC"))
        place.assert_not_called()

    def test_growth_reports_false_when_no_sell_order_was_sent(self):
        extrema = (
            {"timestamp": 1, "total_value_usdc": 50},
            {"timestamp": 2, "total_value_usdc": 100},
        )
        with mock.patch.object(ag.api, "get_total_assets_value_usdc", return_value=101), \
             mock.patch.object(ag, "_get_window_extrema_from_cache", return_value=extrema), \
             mock.patch.object(ag, "sell_all_assets", return_value=False) as sell:
            self.assertFalse(ag.evaluate_and_maybe_sell_or_buy(threshold_percent=100))
        sell.assert_called_once()

    def test_nonfinite_current_value_cannot_trigger_an_order(self):
        with mock.patch.object(ag.api, "get_total_assets_value_usdc",
                               return_value=float("inf")), \
             mock.patch.object(ag, "sell_all_assets") as sell, \
             mock.patch.object(ag, "buy_with_all_cash") as buy:
            self.assertFalse(ag.evaluate_and_maybe_sell_or_buy())
        sell.assert_not_called()
        buy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
