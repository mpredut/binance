#!/usr/bin/env python3
"""Teste pt trailing_stop (fara API real, fara bani)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from binance_api.trailing_stop import TrailingStop, should_sell  # noqa: E402


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
    def __init__(self):
        self.orders = []
    def place_safe_order(self, side, symbol, price, qty, force=False, **kw):
        self.orders.append({"side": side, "symbol": symbol, "price": price,
                            "qty": qty, "force": force})
        return {"orderId": 1}


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
        self.assertEqual(self.po.orders, [], "dry-run: doar logheaza, nu plaseaza ordine")

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
        # simuleaza rebuy: pretul urca 1.2% de la 200 -> 202.4
        api.price = 199.0; ts.check_once() # low=199
        api.price = 201.5; ts.check_once() # +1.26% de la 199 -> re-buy; initial=201.5
        # acum trailing inactiv pana la 201.5*1.05=211.6
        api.price = 180.0; ts.check_once() # crash de la 201.5 dar sub pragul de activare
        # ordinele: 1 vanzare + 1 re-buy; al treilea NU se executa (warming up)
        sells = [o for o in self.po.orders if o["side"] == "SELL"]
        self.assertEqual(len(sells), 1, "al doilea crash nu declanseaza vanzare (warming up dupa rebuy)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
