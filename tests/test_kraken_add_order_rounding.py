"""Teste pt KrakenClient.add_order — protectie de MECANICA centralizata: rotunjeste pretul
la precizia reala a perechii (pair_decimals). Fara retea (pair_info + _private mock-uite).
Regresie pt bug-ul '(504/EOrder) Invalid price: HYPE/USD price can only be specified up to
2 decimals' care facea trailing-ul Kraken sa esueze la vanzare/rebuy (1353 respingeri)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kraken"))

from kraken_client import KrakenClient


class AddOrderRoundingTest(unittest.TestCase):
    def _client(self, pair_decimals):
        c = KrakenClient.__new__(KrakenClient)   # fara __init__ (fara chei/retea)
        c.pair_info = lambda pair: {"pair_decimals": pair_decimals}
        sent = {}
        c._private = lambda method, data: sent.update(data) or {"ok": True}
        return c, sent

    def test_price_rounded_to_pair_decimals(self):
        c, sent = self._client(2)
        c.add_order("HYPEUSD", "sell", 1.23456789, 53.839 * 0.995, ordertype="limit")
        self.assertEqual(sent["price"], "53.57")     # 53.569805 -> 2 zecimale

    def test_more_decimals_pair_kept(self):
        c, sent = self._client(5)
        c.add_order("XBTUSD", "buy", 1.0, 0.123456789, ordertype="limit")
        self.assertEqual(sent["price"], "0.12346")   # rotunjit la 5

    def test_price_decimals_fallback_2_when_info_missing(self):
        c = KrakenClient.__new__(KrakenClient)
        c.pair_info = lambda pair: None
        self.assertEqual(c.price_decimals("WHATEVERUSD"), 2)

    def test_price_decimals_fallback_on_exception(self):
        c = KrakenClient.__new__(KrakenClient)
        def boom(pair):
            raise RuntimeError("API down")
        c.pair_info = boom
        self.assertEqual(c.price_decimals("X"), 2)   # nu arunca -> fallback prudent

    def test_market_order_no_price_field(self):
        c, sent = self._client(2)
        c.add_order("HYPEUSD", "sell", 1.0, None, ordertype="market")
        self.assertNotIn("price", sent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
