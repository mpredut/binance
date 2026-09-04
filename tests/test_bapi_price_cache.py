"""Regression tests for the bounded-age Binance REST quote cache."""

import unittest
from unittest import mock

from binance_api import bapi


class BinanceRestPriceCacheTest(unittest.TestCase):
    SYMBOL = "BTCUSDC"

    def setUp(self):
        bapi.cprice.clear()
        bapi.cprice_time.clear()

    def tearDown(self):
        bapi.cprice.clear()
        bapi.cprice_time.clear()

    def test_failed_refresh_does_not_keep_an_old_price_fresh(self):
        ttl = bapi.BINANCE_REST_PRICE_CACHE_TTL_SEC
        client = mock.Mock()
        client.get_symbol_ticker.return_value = {"price": "100.25"}
        with (
            mock.patch.object(bapi, "client", client),
            mock.patch.object(bapi.time, "time", side_effect=[10.0, 10.0]),
        ):
            self.assertEqual(
                100.25, bapi.get_current_price(self.SYMBOL)
            )
        self.assertEqual(10.0, bapi.cprice_time[self.SYMBOL])

        client.get_symbol_ticker.side_effect = ConnectionError(
            "ticker unavailable")
        with (
            mock.patch.object(bapi, "client", client),
            # Constant stale clock: get_current_price now calls time.time() both
            # directly and inside update_price (even on a failed refresh), so a fixed
            # two-element list exhausted mid-call. A lambda never exhausts and keeps
            # every read past the TTL, which is all this staleness check needs.
            mock.patch.object(
                bapi.time, "time",
                side_effect=lambda: 10.0 + ttl + 1,
            ),
        ):
            self.assertIsNone(bapi.get_current_price(self.SYMBOL))
            self.assertIsNone(bapi.get_current_price(self.SYMBOL))

        self.assertEqual(3, client.get_symbol_ticker.call_count)
        self.assertEqual(10.0, bapi.cprice_time[self.SYMBOL])
        self.assertEqual(100.25, bapi.cprice[self.SYMBOL])

    def test_invalid_ticker_is_never_cached(self):
        for invalid in ("0", "-1", "nan", "inf"):
            with self.subTest(price=invalid):
                client = mock.Mock()
                client.get_symbol_ticker.return_value = {"price": invalid}
                with mock.patch.object(bapi, "client", client):
                    self.assertIsNone(bapi.update_price(self.SYMBOL))
                self.assertNotIn(self.SYMBOL, bapi.cprice)
                self.assertNotIn(self.SYMBOL, bapi.cprice_time)

    def test_clock_rollback_forces_a_refresh(self):
        bapi.cprice[self.SYMBOL] = 100.0
        bapi.cprice_time[self.SYMBOL] = 20.0
        client = mock.Mock()
        client.get_symbol_ticker.return_value = {"price": "101"}
        with (
            mock.patch.object(bapi, "client", client),
            mock.patch.object(bapi.time, "time", side_effect=[10.0, 10.0]),
        ):
            self.assertEqual(101.0, bapi.get_current_price(self.SYMBOL))


if __name__ == "__main__":
    unittest.main()
