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
    def __init__(self):
        self.order_calls = []
        self.open = []

    def get_symbol_info(self, symbol):
        return {"baseAsset": "BTC", "quoteAsset": "USDC", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
            {"filterType": "LOT_SIZE", "stepSize": "0.00100000", "minQty": "0.00100000"}]}

    def get_klines(self, symbol, interval, limit):
        return [[0, "1", "1", "1", "10"], [0, "1", "1", "1", "11"], [0, "1", "1", "1", "12"]]

    def get_order(self, symbol, orderId=None, origClientOrderId=None):
        if origClientOrderId is not None:
            return {
                "orderId": 77, "clientOrderId": origClientOrderId,
                "side": "BUY", "price": "60", "origQty": "2",
                "executedQty": "0", "status": "NEW",
            }
        return {"status": "FILLED", "executedQty": "2.0", "cummulativeQuoteQty": "120.0"}

    def get_open_orders(self, symbol):
        return list(self.open)

    def get_my_trades(self, symbol, orderId):
        return [{
            "orderId": orderId, "price": "60", "commission": "0.12",
            "commissionAsset": "USDC",
        }]

    def order_market_buy(self, symbol, quantity, **kwargs):
        self.order_calls.append(("buy", symbol, quantity, kwargs))
        return {"orderId": 555}

    def order_market_sell(self, symbol, quantity, **kwargs):
        self.order_calls.append(("sell", symbol, quantity, kwargs))
        return {"orderId": 556}


class FakeBapi:
    def __init__(self):
        self.client = FakeClient()
        self.calls = []
        self.cancel_result = True

    def get_current_price(self, symbol):
        return 600.0

    def cancel_order(self, symbol, order_id):
        self.calls.append((symbol, order_id))
        return self.cancel_result


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
        self.assertAlmostEqual(st.fee, 0.12)

    def test_submit_order_market_intoarce_order_id(self):
        oid = self.p.submit_order("BTCUSDC", "buy", 0.01, price=None, market=True)
        self.assertEqual(oid, "555")

    def test_submit_order_market_propaga_client_order_id(self):
        client_id = "SD_0123456789abcdef0123456789abcdef"
        self.p.submit_order(
            "BTCUSDC", "buy", 0.01, market=True,
            client_order_id=client_id,
        )
        self.assertEqual(
            self.fake.client.order_calls[-1][-1]["newClientOrderId"], client_id,
        )

    def test_submit_order_without_an_orderId_raises(self):
        self.fake.client.order_market_buy = lambda symbol, quantity, **kwargs: {}
        with self.assertRaises(ProviderError):
            self.p.submit_order("BTCUSDC", "buy", 0.01, market=True)

    def test_cancel_deleaga(self):
        self.p.cancel_order("BTCUSDC", "42")
        self.assertIn(("BTCUSDC", 42), self.fake.calls)

    def test_cancel_neconfirmat_ridica(self):
        self.fake.cancel_result = False
        with self.assertRaises(ProviderError):
            self.p.cancel_order("BTCUSDC", "42")

    def test_open_orders_keeps_the_identity_for_recovery(self):
        self.fake.client.open = [{
            "orderId": 77, "clientOrderId": "RT_abc", "side": "buy",
            "price": "60", "origQty": "2", "executedQty": "0.5",
            "status": "PARTIALLY_FILLED",
        }]
        self.assertEqual(self.p.open_orders("BTCUSDC"), [{
            "orderId": "77", "clientOrderId": "RT_abc", "side": "BUY",
            "price": 60.0, "origQty": 2.0, "executedQty": 0.5,
            "status": "PARTIALLY_FILLED",
        }])

    def test_lookup_by_client_order_id(self):
        order = self.p.order_by_client_id("BTCUSDC", "RT_abc")
        self.assertEqual(order["orderId"], 77)
        self.assertEqual(order["clientOrderId"], "RT_abc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
