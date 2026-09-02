import os, sys, unittest
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from binance_api import bapi_allorders as ao


class TestPaginateMyTrades(unittest.TestCase):
    def test_single_page_one_call(self):
        client = MagicMock()
        client.get_my_trades.return_value = [{"id": i, "time": 1} for i in range(10)]
        out = ao.paginate_my_trades(client, "BTCUSDC", 0, limit=1000)
        self.assertEqual(len(out), 10)
        self.assertEqual(client.get_my_trades.call_count, 1)   # < limit -> a single request
        # First page by startTime, without fromId
        _, kw = client.get_my_trades.call_args_list[0]
        self.assertEqual(kw.get("startTime"), 0)
        self.assertNotIn("fromId", kw)

    def test_multi_page_uses_fromid(self):
        client = MagicMock()
        page1 = [{"id": i, "time": 1} for i in range(1000)]
        page2 = [{"id": 1000 + i, "time": 1} for i in range(500)]
        client.get_my_trades.side_effect = [page1, page2]
        out = ao.paginate_my_trades(client, "BTCUSDC", 0, limit=1000)
        self.assertEqual(len(out), 1500)                        # Not truncated at 1000.
        self.assertEqual(client.get_my_trades.call_count, 2)
        _, kw2 = client.get_my_trades.call_args_list[1]
        self.assertEqual(kw2.get("fromId"), 1000)               # Last ID (999) plus one.
        self.assertNotIn("startTime", kw2)

    def test_empty(self):
        client = MagicMock()
        client.get_my_trades.return_value = []
        self.assertEqual(ao.paginate_my_trades(client, "X", 0), [])

    def test_exact_multiple_of_limit_stops_on_empty(self):
        client = MagicMock()
        page1 = [{"id": i, "time": 1} for i in range(2)]
        client.get_my_trades.side_effect = [page1, []]          # Exactly at the limit -> it asks once more
        out = ao.paginate_my_trades(client, "X", 0, limit=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(client.get_my_trades.call_count, 2)

    def test_none_response_remains_empty_for_legacy_readers(self):
        client = MagicMock()
        client.get_my_trades.return_value = None
        self.assertEqual(ao.paginate_my_trades(client, "X", 0), [])

    def test_none_response_fails_a_strict_reconciliation(self):
        client = MagicMock()
        client.get_my_trades.return_value = None
        with self.assertRaisesRegex(
                RuntimeError, "Binance returned no trade page"):
            ao.paginate_my_trades(client, "X", 0, strict=True)


class TestCachedOrders(unittest.TestCase):
    def test_reader_does_not_start_a_duplicate_polling_loop(self):
        manager = MagicMock()
        manager.cache = {"BTCUSDC": []}
        with patch("cacheManager.get_cache_manager", return_value=manager) as get_manager:
            self.assertEqual(ao.get_trade_orders("BUY", "BTCUSDC", 60), [])
        get_manager.assert_called_once_with("Order", start_sync=False)

    def test_reader_copies_mutable_order_rows_under_manager_lock(self):
        now_ms = int(time.time() * 1000)
        manager = MagicMock()
        manager.cache = {
            "BTCUSDC": [{
                "orderId": 7,
                "price": 100.0,
                "quantity": 0.5,
                "timestamp": now_ms,
                "side": "BUY",
            }],
        }

        class MutatingLock:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                manager.cache["BTCUSDC"][0]["price"] = 999.0

        manager.lock = MutatingLock()
        with patch("cacheManager.get_cache_manager", return_value=manager):
            result = ao.get_trade_orders("BUY", "BTCUSDC", 60)

        self.assertEqual(result[0]["price"], 100.0)
        self.assertEqual(manager.cache["BTCUSDC"][0]["price"], 999.0)


if __name__ == "__main__":
    unittest.main()
