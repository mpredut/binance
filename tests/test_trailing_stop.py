#!/usr/bin/env python3
"""Tests for trailing_stop (no real API, no money)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from binance_api.trailing_stop import TrailingStop, should_sell  # noqa: E402
from providers.strategy_executor import OrderStatus  # noqa: E402


class FakeApi:
    def __init__(self, price, free=5.0, asset="TAO"):
        self.price = price
        self.free = free
        self.asset = asset
    def get_account_assets_balances(self):
        return [{"asset": self.asset, "free": str(self.free)}]
    def get_current_price(self, symbol):
        return self.price
    def split_symbol(self, symbol):
        return (symbol.replace("USDC", "").replace("USDT", ""), "USDC")


class FakePo:
    # 30 Jul: TrailingStop now uses the single guarded proxy (.place(symbol, side,...)),
    # not place_safe_order. The fake exposes both (the new place plus the legacy place_safe_order)
    # so it stays robust; execute_sell/rebuy call .place().
    def __init__(self):
        self.orders = []
        self.result = {"orderId": 1}
        self.status = "closed"
        self.by_client_id = {}
    def place(self, symbol, side, price, qty, force=False, **kw):
        order = {"side": side, "symbol": symbol, "price": price,
                            "qty": qty, "force": force,
                            "client_order_id": kw.get("client_order_id"),
                            "bypass_profit_guard": bool(
                                kw.get("bypass_profit_guard", False)
                            )}
        self.orders.append(order)
        if self.result and order["client_order_id"]:
            self.by_client_id[order["client_order_id"]] = self.result
        return self.result
    def order_by_client_id(self, symbol, client_order_id, *, provider_name=None):
        return self.by_client_id.get(client_order_id)
    def order_status(self, symbol, order_id, *, provider_name=None):
        order = next(o for o in self.orders
                     if str(self.by_client_id.get(o["client_order_id"], {}).get("orderId"))
                     == str(order_id))
        filled = order["qty"] if self.status == "closed" else 0.0
        return OrderStatus(self.status, filled, filled * order["price"], 0.0)
    def cancel_order(self, symbol, order_id, *, provider_name=None):
        self.status = "canceled"
    def place_safe_order(self, side, symbol, price, qty, force=False, **kw):
        return self.place(symbol, side, price, qty, force=force, **kw)


class FakeSym:
    symbols = ["TAOUSDC"]


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.sf = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(self.sf)
        self.po = FakePo()
        os.environ.pop("TRAILING_ENABLED", None)
    def tearDown(self):
        for p in (self.sf, self.sf + ".tmp"):
            if os.path.exists(p):
                os.remove(p)
    def ts(self, api, enabled=True, frac=1.0, min_profit_pct=0.0):
        return TrailingStop(api, self.po, FakeSym(), log=lambda *a: None,
                            enabled=enabled, sell_fraction=frac, state_file=self.sf,
                            min_profit_pct=min_profit_pct)


class TestLogica(unittest.TestCase):
    def test_should_sell(self):
        self.assertTrue(should_sell(90, 100, 10))      # exact -10%
        self.assertTrue(should_sell(89, 100, 10))
        self.assertFalse(should_sell(91, 100, 10))     # only -9%
        self.assertFalse(should_sell(100, 100, 10))
        self.assertFalse(should_sell(50, 0, 10))       # no peak


class TestTrailing(Base):
    def test_an_empty_balance_snapshot_does_not_change_the_state(self):
        api = FakeApi(250.0)
        api.get_account_assets_balances = lambda: []
        ts = self.ts(api)
        ts.check_once()
        self.assertFalse(os.path.exists(self.sf))
        self.assertEqual(self.po.orders, [])

    def test_a_non_finite_price_does_not_change_the_state(self):
        api = FakeApi(float("nan"))
        self.ts(api).check_once()
        import json
        self.assertEqual(json.load(open(self.sf)), {})

    def test_a_rise_does_not_sell_and_updates_the_peak(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()
        api.price = 260.0
        ts.check_once()
        self.assertEqual(self.po.orders, [])
        import json
        self.assertEqual(json.load(open(self.sf))["TAOUSDC"]["peak"], 260.0)

    def test_falling_below_the_threshold_sells_with_force(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()                                # varf 250
        api.price = 190.0                              # -24% de la 250 (prag TAO 22%)
        ts.check_once()
        self.assertEqual(len(self.po.orders), 1)
        self.assertEqual(self.po.orders[0]["side"], "SELL")
        self.assertTrue(self.po.orders[0]["force"], "force=True is required so it bypasses the weight")
        self.assertTrue(
            self.po.orders[0]["bypass_profit_guard"],
            "the protective exit must bypass the profit guard explicitly",
        )

    def test_a_refused_sell_keeps_the_peak_and_does_not_arm_a_rebuy(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()
        self.po.result = None
        api.price = 190.0
        ts.check_once()
        import json
        state = json.load(open(self.sf))["TAOUSDC"]
        self.assertEqual(state["peak"], 250.0)
        self.assertNotIn("rebuy", state)

    def test_a_refused_rebuy_stays_for_a_retry(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()
        api.price = 190.0
        ts.check_once()
        self.po.result = None
        api.free = 0.0
        api.price = 193.0
        ts.check_once()
        import json
        self.assertIn("rebuy", json.load(open(self.sf))["TAOUSDC"])

    def test_a_small_fall_does_not_sell(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()
        api.price = 240.0                              # -4% < 22%
        ts.check_once()
        self.assertEqual(self.po.orders, [])

    def test_a_dry_run_does_not_sell(self):
        api = FakeApi(250.0)
        ts = self.ts(api, enabled=False)
        ts.check_once()
        api.price = 190.0
        ts.check_once()
        self.assertEqual(self.po.orders, [], "dry run: it only logs, it does not place orders")

    def test_the_peak_survives_a_restart(self):
        api = FakeApi(260.0)
        self.ts(api).check_once()                      # varf 260, instanta 1
        api.price = 200.0                              # -23% de la 260 (prag 22%)
        self.ts(api).check_once()                      # instance 2 (a restart) — it reads the peak
        self.assertEqual(len(self.po.orders), 1, "varful 260 supravietuieste restartului")

    def test_a_partial_sale(self):
        api = FakeApi(250.0, free=4.0)
        ts = self.ts(api, frac=0.5)
        ts.check_once()
        api.price = 190.0
        ts.check_once()
        self.assertAlmostEqual(self.po.orders[0]["qty"], 2.0)   # 50% of 4

    def test_it_re_arms_after_a_sale(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()
        api.price = 190.0; ts.check_once()             # it sells, the peak resets to 190
        ts.check_once()                                # status terminal confirmat
        import json
        self.assertEqual(json.load(open(self.sf))["TAOUSDC"]["peak"], 190.0)

    def test_sub_notional_minim_ignora(self):
        api = FakeApi(250.0, free=0.01)                # 0.01*250 = $2.5 < $11
        ts = self.ts(api)
        ts.check_once()
        api.price = 200.0
        ts.check_once()
        self.assertEqual(self.po.orders, [])


class TestPerMoneda(Base):
    def test_prag_diferentiat(self):
        ts = self.ts(FakeApi(1.0))
        self.assertEqual(ts.trail_pct_for("BTCUSDC"), 20.0)
        self.assertEqual(ts.trail_pct_for("TAOUSDC"), 22.0)
        self.assertEqual(ts.trail_pct_for("XYZUSDC"), 22.0)   # default

    def test_sell_fraction_invalid_esueaza_la_start(self):
        with self.assertRaises(ValueError):
            self.ts(FakeApi(1.0), frac=1.01)
        with self.assertRaises(ValueError):
            self.ts(FakeApi(1.0), frac=float("nan"))


class TestMinProfit(Base):
    """The minimum profit threshold before the trailing activates."""

    def test_warming_up_does_not_sell_below_the_threshold(self):
        api = FakeApi(250.0)
        ts = self.ts(api, min_profit_pct=5.0)
        ts.check_once()                    # initial=250, activ la 262.5
        api.price = 190.0                  # a crash of -24% but below the activation threshold
        ts.check_once()
        self.assertEqual(self.po.orders, [], "it does not sell before the profit threshold is reached")

    def test_active_past_the_threshold_sells(self):
        api = FakeApi(250.0)
        ts = self.ts(api, min_profit_pct=5.0)
        ts.check_once()                    # initial=250
        api.price = 263.0                  # +5.2% > 5% prag -> trailing activ
        ts.check_once()                    # peak=263
        api.price = 200.0                  # -23.9% de la peak 263 (prag TAO 22%)
        ts.check_once()
        self.assertEqual(len(self.po.orders), 1, "it sells once the profit threshold is passed")
        self.assertEqual(self.po.orders[0]["side"], "SELL")

    def test_it_initially_resets_to_the_rebuy(self):
        """After a crash sell plus a re-buy, it initially resets to the re-buy price."""
        api = FakeApi(250.0)
        ts = self.ts(api, min_profit_pct=5.0)
        ts.check_once()                    # initial=250, peak=250
        api.price = 263.0; ts.check_once() # trece de prag -> activ
        api.price = 200.0; ts.check_once() # a crash of -23.9% -> it sells; it arms the rebuy
        self.assertEqual(len(self.po.orders), 1)
        ts.check_once()                    # it confirms the SELL fill and arms the re-buy
        # simulate the rebuy: the price rises 1.2% from 200 -> 202.4
        api.price = 199.0; ts.check_once() # low=199
        api.price = 201.5; ts.check_once() # +1.26% de la 199 -> re-buy; initial=201.5
        ts.check_once()                    # it confirms the REBUY fill and sets the warmup
        # now the trailing is inactive until 201.5*1.05=211.6
        api.price = 180.0; ts.check_once() # a crash from 201.5 but below the activation threshold
        # the orders: 1 sell + 1 re-buy; the third does NOT execute (warming up)
        sells = [o for o in self.po.orders if o["side"] == "SELL"]
        self.assertEqual(len(sells), 1, "the second crash does not trigger a sell (warming up after the rebuy)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
