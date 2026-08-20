"""Faza 4 provider-unify — BinanceProvider satisface contractul StrategyExecutor
(cablare bapi). _bapi patch-uit cu un fake (fara client/chei Binance reale)."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from providers import market_api  # noqa: E402
from providers.market_api import BinanceProvider  # noqa: E402
from providers.strategy_executor import (  # noqa: E402
    StrategyExecutor, OrderStatus, PairPrecision, ProviderError)


class FakeClient:
    def get_symbol_info(self, symbol):
        return {"baseAsset": "BTC", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
            {"filterType": "LOT_SIZE", "stepSize": "0.00100000", "minQty": "0.00100000"}]}

    def get_klines(self, symbol, interval, limit):
        return [[0, "1", "1", "1", "10"], [0, "1", "1", "1", "11"], [0, "1", "1", "1", "12"]]

    def get_order(self, symbol, orderId):
        return {"status": "FILLED", "executedQty": "2.0", "cummulativeQuoteQty": "120.0"}

    def order_market_buy(self, symbol, quantity):
        return {"orderId": 555}

    def order_market_sell(self, symbol, quantity):
        return {"orderId": 556}


class FakeBapi:
    def __init__(self):
        self.client = FakeClient()
        self.calls = []

    def cancel_order(self, symbol, order_id):
        self.calls.append((symbol, order_id))
        return True


class BinanceExecutorContractTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeBapi()
        self._patch = mock.patch.object(market_api, "_bapi", self.fake)
        self._patch.start()
        self.p = BinanceProvider()

    def tearDown(self):
        self._patch.stop()

    def test_satisface_protocolul(self):
        self.assertIsInstance(self.p, StrategyExecutor)

    def test_pair_precision(self):
        pp = self.p.pair_precision("BTCUSDC")
        self.assertEqual(pp, PairPrecision(price_decimals=2, volume_decimals=3,
                                           order_min=0.001, base_asset="BTC"))

    def test_ohlc_closes_exclude_bara_in_formare(self):
        self.assertEqual(self.p.ohlc_closes("BTCUSDC", 240), [10.0, 11.0])

    def test_order_status_filled(self):
        st = self.p.order_status("BTCUSDC", "42")
        self.assertEqual(st.status, "closed")
        self.assertAlmostEqual(st.filled_qty, 2.0)
        self.assertAlmostEqual(st.cost, 120.0)

    def test_submit_order_market_intoarce_order_id(self):
        oid = self.p.submit_order("BTCUSDC", "buy", 0.01, price=None, market=True)
        self.assertEqual(oid, "555")

    def test_submit_order_fara_orderId_ridica(self):
        self.fake.client.order_market_buy = lambda symbol, quantity: {}
        with self.assertRaises(ProviderError):
            self.p.submit_order("BTCUSDC", "buy", 0.01, market=True)

    def test_cancel_deleaga(self):
        self.p.cancel_order("BTCUSDC", "42")
        self.assertIn(("BTCUSDC", 42), self.fake.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
