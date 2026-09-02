"""Verify cache freshness again after synchronizing a trading process reader."""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import binance_cache_health
from binance_api import bapi_placeorder as placeorder
from providers.strategy_executor import SubmissionRefused


class AccountCacheReaderConfirmationTest(unittest.TestCase):
    def setUp(self):
        self.trade_enabled = patch.object(
            placeorder.cfg, "is_trade_enabled", return_value=True)
        self.trade_enabled.start()

    def tearDown(self):
        self.trade_enabled.stop()

    @staticmethod
    def _status(order_version, trade_version, *, age=1.0):
        return binance_cache_health.CacheHealthStatus(
            True, "", age, age, order_version, trade_version)

    def test_reader_is_confirmed_against_a_post_load_marker(self):
        requested = self._status("order-1", "trade-1")
        confirmed = self._status("order-1", "trade-1", age=2.0)
        cache_module = SimpleNamespace(
            ensure_account_cache_readers=MagicMock())
        with patch.dict(sys.modules, {"cacheManager": cache_module}), \
             patch.object(
                 placeorder.binance_cache_health,
                 "require_fresh_account_cache",
                 side_effect=[requested, confirmed],
             ):
            result = placeorder.require_account_cache_for_submit()

        self.assertIs(result, confirmed)
        cache_module.ensure_account_cache_readers.assert_called_once_with(
            requested)

    def test_one_bounded_retry_loads_a_marker_that_advanced(self):
        first = self._status("order-1", "trade-1")
        advanced = self._status("order-2", "trade-2")
        cache_module = SimpleNamespace(
            ensure_account_cache_readers=MagicMock())
        with patch.dict(sys.modules, {"cacheManager": cache_module}), \
             patch.object(
                 placeorder.binance_cache_health,
                 "require_fresh_account_cache",
                 side_effect=[first, advanced, advanced, advanced],
             ):
            result = placeorder.require_account_cache_for_submit()

        self.assertIs(result, advanced)
        self.assertEqual(
            cache_module.ensure_account_cache_readers.call_args_list,
            [call(first), call(advanced)],
        )

    def test_marker_becoming_stale_during_reader_load_refuses(self):
        requested = self._status("order-1", "trade-1")
        cache_module = SimpleNamespace(
            ensure_account_cache_readers=MagicMock())
        with patch.dict(sys.modules, {"cacheManager": cache_module}), \
             patch.object(
                 placeorder.binance_cache_health,
                 "require_fresh_account_cache",
                 side_effect=[
                     requested,
                     binance_cache_health.AccountCacheNotReady(
                         "trade_cache_stale"),
                 ],
             ):
            with self.assertRaisesRegex(
                    SubmissionRefused, "account_cache_not_fresh") as raised:
                placeorder.require_account_cache_for_submit()

        self.assertEqual(raised.exception.__cause__.reason, "trade_cache_stale")
        cache_module.ensure_account_cache_readers.assert_called_once_with(
            requested)


if __name__ == "__main__":
    unittest.main()
