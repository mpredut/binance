import os, sys, unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from binance_api import bapi_allorders as ao


class TestPaginateMyTrades(unittest.TestCase):
    def test_single_page_one_call(self):
        client = MagicMock()
        client.get_my_trades.return_value = [{"id": i, "time": 1} for i in range(10)]
        out = ao.paginate_my_trades(client, "BTCUSDC", 0, limit=1000)
        self.assertEqual(len(out), 10)
        self.assertEqual(client.get_my_trades.call_count, 1)   # < limit → o singură cerere
        # prima pagină pe startTime, fără fromId
        _, kw = client.get_my_trades.call_args_list[0]
        self.assertEqual(kw.get("startTime"), 0)
        self.assertNotIn("fromId", kw)

    def test_multi_page_uses_fromid(self):
        client = MagicMock()
        page1 = [{"id": i, "time": 1} for i in range(1000)]
        page2 = [{"id": 1000 + i, "time": 1} for i in range(500)]
        client.get_my_trades.side_effect = [page1, page2]
        out = ao.paginate_my_trades(client, "BTCUSDC", 0, limit=1000)
        self.assertEqual(len(out), 1500)                        # nu trunchiat la 1000
        self.assertEqual(client.get_my_trades.call_count, 2)
        _, kw2 = client.get_my_trades.call_args_list[1]
        self.assertEqual(kw2.get("fromId"), 1000)               # id ultimului (999) + 1
        self.assertNotIn("startTime", kw2)

    def test_empty(self):
        client = MagicMock()
        client.get_my_trades.return_value = []
        self.assertEqual(ao.paginate_my_trades(client, "X", 0), [])

    def test_exact_multiple_of_limit_stops_on_empty(self):
        client = MagicMock()
        page1 = [{"id": i, "time": 1} for i in range(2)]
        client.get_my_trades.side_effect = [page1, []]          # exact limit → mai cere o dată
        out = ao.paginate_my_trades(client, "X", 0, limit=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(client.get_my_trades.call_count, 2)


if __name__ == "__main__":
    unittest.main()
