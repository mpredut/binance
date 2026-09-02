"""Tests for KrakenClient.add_order — a centralised MECHANICS protection: it rounds the price
to the pair's real precision (pair_decimals). No network (pair_info plus _private are mocked).
Regresie pt bug-ul '(504/EOrder) Invalid price: HYPE/USD price can only be specified up to
2 decimals' that made Kraken trailing fail on sell/rebuy (1353 rejections)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kraken"))

from kraken_client import KrakenClient


class AddOrderRoundingTest(unittest.TestCase):
    def _client(self, pair_decimals):
        c = KrakenClient.__new__(KrakenClient)   # No __init__ (no keys, no network)
        c.pair_info = lambda pair: {"pair_decimals": pair_decimals}
        sent = {}
        c._private = lambda method, data: sent.update(data) or {"ok": True}
        return c, sent

    def test_limit_price_respects_pair_decimals(self):
        cases = (
            (2, "HYPEUSD", "sell", 1.23456789, 53.839 * 0.995, "53.57"),
            (5, "XBTUSD", "buy", 1.0, 0.123456789, "0.12346"),
        )
        for decimals, pair, side, volume, price, expected in cases:
            with self.subTest(pair=pair, decimals=decimals):
                c, sent = self._client(decimals)
                c.add_order(pair, side, volume, price, ordertype="limit")
                self.assertEqual(sent["price"], expected)

    def test_price_decimals_fallback(self):
        def boom(pair):
            raise RuntimeError("API down")

        for label, pair_info in (("missing", lambda pair: None), ("exception", boom)):
            with self.subTest(case=label):
                c = KrakenClient.__new__(KrakenClient)
                c.pair_info = pair_info
                self.assertEqual(c.price_decimals("WHATEVERUSD"), 2)

    def test_market_order_no_price_field(self):
        c, sent = self._client(2)
        c.add_order("HYPEUSD", "sell", 1.0, None, ordertype="market")
        self.assertNotIn("price", sent)

    def test_client_order_id_is_validated_and_sent(self):
        c, sent = self._client(2)
        client_id = "0123456789ABCDEF0123456789ABCDEF"
        c.add_order(
            "HYPEUSD", "buy", 1.0, 60.0, ordertype="limit",
            cl_ord_id=client_id,
        )
        self.assertEqual(sent["cl_ord_id"], client_id.lower())

        with self.assertRaisesRegex(ValueError, "128 biti"):
            c.add_order("HYPEUSD", "buy", 1.0, 60.0, cl_ord_id="prea-scurt")


if __name__ == "__main__":
    unittest.main(verbosity=2)
