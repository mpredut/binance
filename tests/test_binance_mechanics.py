"""
Teste pt MECANICA de plasare Binance extrasa (30 iul) — adjust_price_and_cancel_opposite
+ place_order_mechanics — si pt hook-urile BinanceProvider care le expun catre pipeline-ul
agnostic (Instrument.place). Reteaua e mock-uita integral (patch pe binance_api.bapi +
functiile de dispatch); NICIUN apel real.

Scop: acopera FLIP-ul (guards_internally=False -> Binance prin pipeline agnostic),
care nu e atins de suita existenta (FakeProvider / replay).
"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

mock_bapi = MagicMock()
sys.modules.setdefault("bapi", mock_bapi)
sys.modules.setdefault("bapi_trades", MagicMock())
sys.modules.setdefault("bapi_allorders", MagicMock())

from binance_api import bapi_placeorder as po
from providers.market_api import BinanceProvider

SYMBOL = "BTCUSDC"


class TestAdjustPriceAndCancelOpposite(unittest.TestCase):
    def test_buy_nudges_down_and_cancels_low_sells(self):
        with patch.object(po.api, "get_current_price", return_value=100.0), \
             patch.object(po.api, "get_open_orders", return_value={"1": {"price": 90.0}, "2": {"price": 110.0}}) as goo, \
             patch.object(po.api, "cancel_order", return_value=True) as cancel:
            out = po.adjust_price_and_cancel_opposite("BUY", SYMBOL, 105.0, cancel_opposite=True)
        # pret cerut 105 > current 100 -> clamp la 100, apoi *0.999 -> round(99.9)=100
        self.assertEqual(out, round(min(105.0, 100.0) * 0.999, 0))
        goo.assert_called_once_with("SELL", SYMBOL)
        # anuleaza DOAR SELL-ul sub pretul cerut (90 < 105); 110 ramane
        cancel.assert_called_once_with(SYMBOL, "1")

    def test_sell_nudges_up_and_cancels_high_buys(self):
        with patch.object(po.api, "get_current_price", return_value=100.0), \
             patch.object(po.api, "get_open_orders", return_value={"1": {"price": 110.0}, "2": {"price": 90.0}}), \
             patch.object(po.api, "cancel_order", return_value=True) as cancel:
            out = po.adjust_price_and_cancel_opposite("SELL", SYMBOL, 95.0, cancel_opposite=True)
        # pret cerut 95 < current 100 -> clamp la 100, apoi *1.001 -> round
        self.assertEqual(out, round(max(95.0, 100.0) * 1.001, 0))
        cancel.assert_called_once_with(SYMBOL, "1")   # doar BUY-ul peste pret (110)

    def test_no_cancel_when_disabled(self):
        with patch.object(po.api, "get_current_price", return_value=100.0), \
             patch.object(po.api, "get_open_orders") as goo, \
             patch.object(po.api, "cancel_order") as cancel:
            po.adjust_price_and_cancel_opposite("BUY", SYMBOL, 105.0, cancel_opposite=False)
        goo.assert_not_called()
        cancel.assert_not_called()


class TestPlaceOrderMechanics(unittest.TestCase):
    def test_buy_dispatches_limit(self):
        with patch.object(po.api, "get_current_price", return_value=100.0), \
             patch.object(po.api, "get_asset_info", return_value=10.0), \
             patch.object(po, "place_BUY_order", return_value={"orderId": 42}) as pbuy:
            order = po.place_order_mechanics("BUY", SYMBOL, 100.0, 5.0, force=False)
        self.assertEqual(order, {"orderId": 42})
        self.assertTrue(pbuy.called)

    def test_client_order_id_reaches_limit_dispatch(self):
        client_id = "SD_0123456789abcdef0123456789abcdef"
        with patch.object(po.api, "get_current_price", return_value=100.0), \
             patch.object(po.api, "get_asset_info", return_value=10.0), \
             patch.object(po, "place_BUY_order", return_value={"orderId": 42}) as pbuy:
            po.place_order_mechanics(
                "BUY", SYMBOL, 100.0, 5.0, client_order_id=client_id,
            )
        pbuy.assert_called_once_with(SYMBOL, 100.0, 5.0, client_id)

    def test_sell_market_when_force(self):
        with patch.object(po.api, "get_current_price", return_value=100.0), \
             patch.object(po.api, "get_asset_info", return_value=10.0), \
             patch.object(po, "place_SELL_order_at_market", return_value={"orderId": 7}) as pmkt:
            order = po.place_order_mechanics("SELL", SYMBOL, 100.0, 5.0, force=True)
        self.assertEqual(order, {"orderId": 7})
        self.assertTrue(pmkt.called)

    def test_min_notional_rejected(self):
        # qty*price sub 100 -> refuz (None), fara dispatch
        with patch.object(po.api, "get_current_price", return_value=100.0), \
             patch.object(po.api, "get_asset_info", return_value=10.0), \
             patch.object(po, "place_BUY_order", return_value={"orderId": 1}) as pbuy:
            order = po.place_order_mechanics("BUY", SYMBOL, 100.0, 0.5, force=False)  # 0.5*100=50 < 100
        self.assertIsNone(order)
        pbuy.assert_not_called()

    def test_zero_available_returns_none(self):
        with patch.object(po.api, "get_current_price", return_value=100.0), \
             patch.object(po.api, "get_asset_info", return_value=0.0), \
             patch.object(po, "place_BUY_order") as pbuy:
            order = po.place_order_mechanics("BUY", SYMBOL, 100.0, 5.0)
        self.assertIsNone(order)
        pbuy.assert_not_called()


class TestBinanceProviderHooks(unittest.TestCase):
    def setUp(self):
        self.p = BinanceProvider()

    def test_guards_internally_false(self):
        # FLIP-ul: Binance trece acum prin pipeline-ul agnostic Instrument.place().
        self.assertFalse(self.p.guards_internally())

    def test_adjust_order_price_delegates(self):
        with patch.object(po, "adjust_price_and_cancel_opposite", return_value=99.0) as f:
            out = self.p.adjust_order_price(SYMBOL, "BUY", 100.0, cancel_opposite=True)
        self.assertEqual(out, 99.0)
        f.assert_called_once_with("BUY", SYMBOL, 100.0, cancel_opposite=True)

    def test_place_order_delegates_to_mechanics(self):
        with patch.object(po, "place_order_mechanics", return_value={"orderId": 5}) as f:
            out = self.p.place_order(SYMBOL, "BUY", 100.0, 5.0, force=True, safeback_seconds=999, pair=None)
        self.assertEqual(out, {"orderId": 5})
        f.assert_called_once_with("BUY", SYMBOL, 100.0, 5.0, force=True)

    def test_cap_quantity_uses_manage_quantity(self):
        with patch.object(po, "manage_quantity", return_value=(3.0, 8.0)) as f:
            out = self.p.cap_quantity(SYMBOL, "BUY", 100.0, 5.0, base="BTC", quote="USDC")
        self.assertEqual(out, 3.0)   # qty plafonat de weight, NU available
        self.assertTrue(f.called)

    def test_profit_guard_window_ref_uses_safeback(self):
        import order_guard
        with patch.object(order_guard, "window_reference", return_value=123.0) as f:
            out = self.p.profit_guard_window_ref(SYMBOL, "BUY", 14 * 24 * 3600)
        self.assertEqual(out, 123.0)
        # a doua pozitionala = safeback trecut (nu window_for config)
        args = f.call_args[0]
        self.assertEqual(args[3], 14 * 24 * 3600)


if __name__ == "__main__":
    unittest.main(verbosity=2)
