"""Regression tests for the final Binance submission freshness boundary."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import binance_cache_health
from binance_api import bapi_placeorder as placeorder
from providers.strategy_executor import SubmissionRefused


class BinanceSubmitGuardTest(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.client.order_limit_buy.return_value = {"orderId": 1}
        self.client.order_market_buy.return_value = {"orderId": 2}
        self.trade_enabled = patch.object(
            placeorder.cfg, "is_trade_enabled", return_value=True)
        self.stale_cache = patch.object(
            placeorder.binance_cache_health,
            "require_fresh_account_cache",
            side_effect=binance_cache_health.AccountCacheNotReady(
                "trade_cache_stale"),
        )
        self.trade_enabled.start()
        self.stale_cache.start()

    def tearDown(self):
        self.stale_cache.stop()
        self.trade_enabled.stop()

    def test_stale_cache_blocks_limit_and_market_before_client_call(self):
        submissions = (
            {"price": 100.0, "market": False},
            {"market": True},
        )
        for options in submissions:
            with self.subTest(options=options):
                with self.assertRaisesRegex(
                        SubmissionRefused, "account_cache_not_fresh"):
                    placeorder._submit_binance_order(
                        "BUY", "BTCUSDC", 0.01,
                        api_client=self.client, **options)
        self.client.order_limit_buy.assert_not_called()
        self.client.order_market_buy.assert_not_called()
