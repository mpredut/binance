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
    # 30 iul: TrailingStop foloseste acum proxy-ul unic guardat (.place(symbol, side,...)),
    # nu place_safe_order. Fake-ul expune ambele (place nou + place_safe_order legacy)
    # ca sa ramana robust; execute_sell/rebuy cheama .place().
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
        self.assertFalse(should_sell(91, 100, 10))     # doar -9%
        self.assertFalse(should_sell(100, 100, 10))
        self.assertFalse(should_sell(50, 0, 10))       # fara varf


class TestTrailing(Base):
    def test_snapshot_balante_gol_nu_modifica_starea(self):
        api = FakeApi(250.0)
        api.get_account_assets_balances = lambda: []
        ts = self.ts(api)
        ts.check_once()
        self.assertFalse(os.path.exists(self.sf))
        self.assertEqual(self.po.orders, [])

    def test_pret_nefinit_nu_modifica_starea(self):
        api = FakeApi(float("nan"))
        self.ts(api).check_once()
        import json
        self.assertEqual(json.load(open(self.sf)), {})

    def test_urca_nu_vinde_actualizeaza_varful(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()
        api.price = 260.0
        ts.check_once()
        self.assertEqual(self.po.orders, [])
        import json
        self.assertEqual(json.load(open(self.sf))["TAOUSDC"]["peak"], 260.0)

    def test_cade_sub_prag_vinde_cu_force(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()                                # varf 250
        api.price = 190.0                              # -24% de la 250 (prag TAO 22%)
        ts.check_once()
        self.assertEqual(len(self.po.orders), 1)
        self.assertEqual(self.po.orders[0]["side"], "SELL")
        self.assertTrue(self.po.orders[0]["force"], "trebuie force=True ca sa ocoleasca weight-ul")
        self.assertTrue(
            self.po.orders[0]["bypass_profit_guard"],
            "iesirea protectoare trebuie sa ocoleasca explicit profit guard",
        )

    def test_sell_refuzat_pastreaza_varful_si_nu_armeaza_rebuy(self):
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

    def test_rebuy_refuzat_ramane_pentru_retry(self):
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

    def test_cadere_mica_nu_vinde(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()
        api.price = 240.0                              # -4% < 22%
        ts.check_once()
        self.assertEqual(self.po.orders, [])

    def test_dry_run_nu_vinde(self):
        api = FakeApi(250.0)
        ts = self.ts(api, enabled=False)
        ts.check_once()
        api.price = 190.0
        ts.check_once()
        self.assertEqual(self.po.orders, [], "dry run: it only logs, it does not place orders")

    def test_varf_persista_peste_restart(self):
        api = FakeApi(260.0)
        self.ts(api).check_once()                      # varf 260, instanta 1
        api.price = 200.0                              # -23% de la 260 (prag 22%)
        self.ts(api).check_once()                      # instanta 2 (restart) — citeste varful
        self.assertEqual(len(self.po.orders), 1, "varful 260 supravietuieste restartului")

    def test_vanzare_partiala(self):
        api = FakeApi(250.0, free=4.0)
        ts = self.ts(api, frac=0.5)
        ts.check_once()
        api.price = 190.0
        ts.check_once()
        self.assertAlmostEqual(self.po.orders[0]["qty"], 2.0)   # 50% din 4

    def test_re_armeaza_dupa_vanzare(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()
        api.price = 190.0; ts.check_once()             # vinde, varf se reseteaza la 190
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
    """Prag minim de profit inainte sa se activeze trailing-ul."""

    def test_warming_up_nu_vinde_sub_prag(self):
        api = FakeApi(250.0)
        ts = self.ts(api, min_profit_pct=5.0)
        ts.check_once()                    # initial=250, activ la 262.5
        api.price = 190.0                  # crash -24% dar sub pragul de activare
        ts.check_once()
        self.assertEqual(self.po.orders, [], "nu vinde inainte sa atinga pragul de profit")

    def test_activ_dupa_prag_vinde(self):
        api = FakeApi(250.0)
        ts = self.ts(api, min_profit_pct=5.0)
        ts.check_once()                    # initial=250
        api.price = 263.0                  # +5.2% > 5% prag -> trailing activ
        ts.check_once()                    # peak=263
        api.price = 200.0                  # -23.9% de la peak 263 (prag TAO 22%)
        ts.check_once()
        self.assertEqual(len(self.po.orders), 1, "vinde dupa ce a trecut de pragul de profit")
        self.assertEqual(self.po.orders[0]["side"], "SELL")

    def test_initial_se_reseteaza_la_rebuy(self):
        """Dupa un crash-sell + re-buy, initial se reseteaza la pretul de re-buy."""
        api = FakeApi(250.0)
        ts = self.ts(api, min_profit_pct=5.0)
        ts.check_once()                    # initial=250, peak=250
        api.price = 263.0; ts.check_once() # trece de prag -> activ
        api.price = 200.0; ts.check_once() # crash -23.9% -> vinde; armeaza rebuy
        self.assertEqual(len(self.po.orders), 1)
        ts.check_once()                    # confirma fill-ul SELL si armeaza re-buy
        # simuleaza rebuy: pretul urca 1.2% de la 200 -> 202.4
        api.price = 199.0; ts.check_once() # low=199
        api.price = 201.5; ts.check_once() # +1.26% de la 199 -> re-buy; initial=201.5
        ts.check_once()                    # confirma fill-ul REBUY si seteaza warmup
        # acum trailing inactiv pana la 201.5*1.05=211.6
        api.price = 180.0; ts.check_once() # crash de la 201.5 dar sub pragul de activare
        # the orders: 1 sell + 1 re-buy; the third does NOT execute (warming up)
        sells = [o for o in self.po.orders if o["side"] == "SELL"]
        self.assertEqual(len(sells), 1, "the second crash does not trigger a sell (warming up after the rebuy)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
