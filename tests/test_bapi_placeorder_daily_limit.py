"""
Teste pentru binance_api.bapi_placeorder.if_place_safe_order() dupa refactor-ul de
30 iul (delegare la order_guard.daily_limit_guard() in loc de o a doua implementare
inline a plafonului zilnic + anti-spam — vezi order_guard.py, commit-ul care a facut
Binance sa foloseasca aceeasi functie ca Kraken/Hyperliquid prin Instrument.place()).

Nicio functie testata aici nu atinge reteaua: get_trade_orders si get_current_price
sunt mock-uite direct (patch pe modulul-sursa binance_api.bapi_allorders/binance_api.bapi
— BinanceProvider.get_orders() e doar un wrapper subtire peste EXACT acelasi apel,
deci patch-ul afecteaza ambele cai, cea veche (apiorders direct) si cea noua
(prin BinanceProvider)."""
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from binance_api import bapi_placeorder as po

SYMBOL = "BTCUSDC"


def _trades(n, side="BUY", age_sec=4000.0, price=100.0):
    ts = (time.time() - age_sec) * 1000
    return [{"orderId": i, "price": price, "qty": 1.0, "quantity": 1.0,
             "timestamp": ts, "side": side} for i in range(n)]


class IfPlaceSafeOrderDailyLimitTestCase(unittest.TestCase):
    def _run(self, same_side_trades, opposite_side_trades=None):
        opposite_side_trades = opposite_side_trades or []

        def fake_get_trade_orders(order_type, symbol, max_age_seconds):
            return same_side_trades if order_type == "BUY" else opposite_side_trades

        with patch("binance_api.bapi_allorders.get_trade_orders", side_effect=fake_get_trade_orders), \
             patch("binance_api.bapi.get_current_price", return_value=100.0):
            # bypass_profit_guard=True: izoleaza plafonul zilnic/anti-spam de gardul
            # de profit (care ar cere si mock pe last_opposite_fill) — comportament
            # PREEXISTENT: bypass_profit_guard sare DOAR profit_guard, plafonul zilnic
            # ramane activ (vezi docstring if_place_safe_order).
            return po.if_place_safe_order("BUY", SYMBOL, 100.0, 1.0,
                                          time_back_in_seconds=48 * 3600 + 60,
                                          bypass_profit_guard=True)

    def test_no_trades_allowed(self):
        ok, reason = self._run(same_side_trades=[])
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_daily_limit_blocks(self):
        # backdays = math.ceil((48h+60s)/86400) = 3 -> prag 25*3=75; 90 le depaseste.
        ok, reason = self._run(same_side_trades=_trades(90, age_sec=4000.0))
        self.assertFalse(ok)
        self.assertEqual(reason, "daily_limit")

    def test_recent_transaction_blocks(self):
        ok, reason = self._run(same_side_trades=_trades(1, age_sec=5.0))
        self.assertFalse(ok)
        self.assertEqual(reason, "recent_transaction")

    def test_old_trades_below_threshold_allowed(self):
        # 10 tranzactii vechi (>3min, sub pragul de 75) -> trece.
        ok, reason = self._run(same_side_trades=_trades(10, age_sec=4000.0))
        self.assertTrue(ok)
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
