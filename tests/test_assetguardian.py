import os
import unittest
from unittest import mock

os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import assetguardian as ag


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

    def test_drawdown_is_measured_from_window_maximum(self):
        extrema = (
            {"timestamp": 1, "total_value_usdc": 80},
            {"timestamp": 2, "total_value_usdc": 100},
        )
        with mock.patch.object(ag.api, "get_total_assets_value_usdc", return_value=92), \
             mock.patch.object(ag, "_get_window_extrema_from_cache", return_value=extrema), \
             mock.patch.object(ag, "buy_with_all_cash", return_value=True) as buy, \
             mock.patch.object(ag, "sell_all_assets") as sell:
            self.assertTrue(ag.evaluate_and_maybe_sell_or_buy(
                threshold_percent=100, drop_percent=7))
        buy.assert_called_once()
        sell.assert_not_called()

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


if __name__ == "__main__":
    unittest.main()
