#!/usr/bin/env python3
"""Teste pt trailing_stop (fara API real, fara bani)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trailing_stop import TrailingStop, should_sell  # noqa: E402


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
    def ts(self, api, enabled=True, frac=1.0):
        return TrailingStop(api, self.po, FakeSym(), log=lambda *a: None,
                            enabled=enabled, sell_fraction=frac, state_file=self.sf)


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
        api.price = 224.0                              # -10.4% de la 250 (prag TAO 10%)
        ts.check_once()
        self.assertEqual(len(self.po.orders), 1)
        self.assertEqual(self.po.orders[0]["side"], "SELL")
        self.assertTrue(self.po.orders[0]["force"], "trebuie force=True ca sa ocoleasca weight-ul")

    def test_cadere_mica_nu_vinde(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()
        api.price = 240.0                              # -4% < 10%
        ts.check_once()
        self.assertEqual(self.po.orders, [])

    def test_dry_run_nu_vinde(self):
        api = FakeApi(250.0)
        ts = self.ts(api, enabled=False)
        ts.check_once()
        api.price = 220.0
        ts.check_once()
        self.assertEqual(self.po.orders, [], "dry-run: doar logheaza, nu plaseaza ordine")

    def test_varf_persista_peste_restart(self):
        api = FakeApi(260.0)
        self.ts(api).check_once()                      # varf 260, instanta 1
        api.price = 233.0                              # -10.4% de la 260
        self.ts(api).check_once()                      # instanta 2 (restart) — citeste varful
        self.assertEqual(len(self.po.orders), 1, "varful 260 supravietuieste restartului")

    def test_vanzare_partiala(self):
        api = FakeApi(250.0, free=4.0)
        ts = self.ts(api, frac=0.5)
        ts.check_once()
        api.price = 220.0
        ts.check_once()
        self.assertAlmostEqual(self.po.orders[0]["qty"], 2.0)   # 50% din 4

    def test_re_armeaza_dupa_vanzare(self):
        api = FakeApi(250.0)
        ts = self.ts(api)
        ts.check_once()
        api.price = 220.0; ts.check_once()             # vinde, varf se reseteaza la 220
        import json
        self.assertEqual(json.load(open(self.sf))["TAOUSDC"]["peak"], 220.0)

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
        self.assertEqual(ts.trail_pct_for("BTCUSDC"), 5.0)
        self.assertEqual(ts.trail_pct_for("TAOUSDC"), 10.0)
        self.assertEqual(ts.trail_pct_for("XYZUSDC"), 10.0)   # default


if __name__ == "__main__":
    unittest.main(verbosity=2)
