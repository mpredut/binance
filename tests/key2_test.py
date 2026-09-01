"""
Tests for the small helpers around the Binance client.
Runs with mocks (no network/keys) -> deterministic. For a REAL API smoke test,
run it directly: `python tests/key2_test.py` (see the __main__ block).
"""
import unittest
from unittest.mock import MagicMock


# ─── testable functions (the client is injected -> mockable) ────────────────
def get_btc_price(client):
    return client.get_symbol_ticker(symbol="BTCUSDT")["price"]


def get_account_balance(client):
    account = client.get_account()
    return {b["asset"]: b["free"] for b in account["balances"] if float(b["free"]) > 0}


def get_open_orders(client):
    return client.get_open_orders(symbol="BTCUSDT")


class TestBinanceHelpers(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()

    def test_btc_price(self):
        self.client.get_symbol_ticker.return_value = {"price": "65000.00"}
        self.assertEqual(get_btc_price(self.client), "65000.00")
        self.client.get_symbol_ticker.assert_called_once_with(symbol="BTCUSDT")

    def test_account_balance_filters_zero(self):
        self.client.get_account.return_value = {"balances": [
            {"asset": "BTC", "free": "0.5"},
            {"asset": "ETH", "free": "0.0"},     # 0 → exclus
            {"asset": "USDT", "free": "100"},
        ]}
        bal = get_account_balance(self.client)
        self.assertEqual(bal, {"BTC": "0.5", "USDT": "100"})
        self.assertNotIn("ETH", bal)

    def test_open_orders_passthrough(self):
        self.client.get_open_orders.return_value = [{"orderId": 1}]
        self.assertEqual(get_open_orders(self.client), [{"orderId": 1}])
        self.client.get_open_orders.assert_called_once_with(symbol="BTCUSDT")


if __name__ == "__main__":
    # REAL API smoke test (needs keys/network) — a single read, no infinite loop
    import time
    from binance.client import Client
    from keys.apikeys import api_key, api_secret
    c = Client(api_key, api_secret)
    print("BTC:", get_btc_price(c))
    print("Balances:", get_account_balance(c))
    print("Orders:", get_open_orders(c))
