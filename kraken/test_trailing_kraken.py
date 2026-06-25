#!/usr/bin/env python3
"""Teste pt trailing_stop Kraken (fara API real, fara bani)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trailing_stop as ts_mod
from trailing_stop import KrakenTrailing, should_sell


class FakeK:
    def __init__(self, price, total=25.0, held=0.0):
        self.price = price; self.total = total; self.held = held; self.orders = []
    def _private(self, method, data=None):
        if method == "BalanceEx":
            return {"HYPE": {"balance": str(self.total), "hold_trade": str(self.held)}}
        return {}
    def last_price(self, pair): return self.price
    def add_order(self, pair, side, volume, price=None, ordertype="limit", validate=False):
        self.orders.append({"side": side, "volume": volume, "price": price, "ordertype": ordertype})
        return {"txid": ["X"]}


class Base(unittest.TestCase):
    def setUp(self):
        fd, self.sf = tempfile.mkstemp(suffix=".json"); os.close(fd); os.remove(self.sf)
        ts_mod.notify = lambda **kw: None
    def tearDown(self):
        for p in (self.sf, self.sf + ".tmp"):
            if os.path.exists(p):
                os.remove(p)
    def ts(self, client, enabled=True, min_profit_pct=0.0):
        return KrakenTrailing(client, log=lambda *a: None, enabled=enabled, state_file=self.sf,
                              min_profit_pct=min_profit_pct)


class TestLogica(unittest.TestCase):
    def test_should_sell(self):
        self.assertTrue(should_sell(85, 100, 15))
        self.assertFalse(should_sell(86, 100, 15))
        self.assertFalse(should_sell(50, 0, 15))


class TestTrailingKraken(Base):
    def test_urca_actualizeaza_varf_nu_vinde(self):
        c = FakeK(60.0)
        self.ts(c).check_once()
        c.price = 65.0
        ts = self.ts(c); ts.check_once()
        self.assertEqual(c.orders, [])
        import json
        self.assertEqual(json.load(open(self.sf))["HYPE"]["peak"], 65.0)

    def test_crash_peste_15pct_vinde_liberul(self):
        c = FakeK(60.0, total=25.0, held=3.38)        # 21.62 liber
        ts = self.ts(c)
        ts.check_once()                                # varf 60
        c.price = 50.0                                 # -16.7% (prag 15%)
        ts.check_once()
        self.assertEqual(len(c.orders), 1)
        self.assertEqual(c.orders[0]["side"], "sell")
        self.assertAlmostEqual(c.orders[0]["volume"], 21.62, places=2)   # doar liberul, nu 25

    def test_cadere_mica_nu_vinde(self):
        c = FakeK(60.0)
        ts = self.ts(c); ts.check_once()
        c.price = 56.0                                 # -6.7% < 15%
        ts.check_once()
        self.assertEqual(c.orders, [])

    def test_dry_run_nu_vinde(self):
        c = FakeK(60.0)
        ts = self.ts(c, enabled=False); ts.check_once()
        c.price = 48.0
        ts.check_once()
        self.assertEqual(c.orders, [])

    def test_varf_persista_peste_restart(self):
        c = FakeK(65.0)
        self.ts(c).check_once()
        c.price = 55.0                                 # -15.4% de la 65
        self.ts(c).check_once()
        self.assertEqual(len(c.orders), 1)

    def test_sub_notional_ignora(self):
        c = FakeK(60.0, total=0.1)                     # 0.1*60 = $6 < $10
        ts = self.ts(c); ts.check_once()
        c.price = 40.0
        ts.check_once()
        self.assertEqual(c.orders, [])


class TestMinProfitKraken(Base):
    """Prag minim de profit inainte sa se activeze trailing-ul (Kraken)."""

    def test_warming_up_nu_vinde_sub_prag(self):
        c = FakeK(60.0)
        ts = self.ts(c, min_profit_pct=5.0)
        ts.check_once()                    # initial=60, activ la 63.0
        c.price = 48.0                     # crash -20% dar sub pragul de activare
        ts.check_once()
        self.assertEqual(c.orders, [], "nu vinde inainte sa atinga pragul de profit")

    def test_activ_dupa_prag_vinde(self):
        c = FakeK(60.0)
        ts = self.ts(c, min_profit_pct=5.0)
        ts.check_once()                    # initial=60
        c.price = 64.0; ts.check_once()   # +6.7% > 5% -> trailing activ, peak=64
        c.price = 53.0; ts.check_once()   # -17.2% de la peak 64 (prag HYPE 15%)
        self.assertEqual(len(c.orders), 1, "vinde dupa ce a trecut pragul de profit")
        self.assertEqual(c.orders[0]["side"], "sell")


if __name__ == "__main__":
    unittest.main(verbosity=2)
