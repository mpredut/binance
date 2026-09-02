"""kraken_client._public: un rezultat GOL ({}) de la un endpoint public (AssetPairs/
Ticker) is NOT cached — it is always a transient failed fetch, not a valid state.
If it were cached (an AssetPairs TTL of 1h), pair_info->None->ordermin=0 would disable the
anti-'volume minimum not met' guard for ~1h -> churn of orders rejected on dust (HYPE 0.0175<0.1)."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kraken"))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import kraken_client as kc


EMPTY = (200, b'{"error":[],"result":{}}')
FULL = (200, b'{"error":[],"result":{"HYPEUSD":{"ordermin":"0.1"}}}')


class PublicCacheTest(unittest.TestCase):
    def setUp(self):
        kc._CACHE.clear()

    def test_empty_not_cached_refetches_then_caches_full(self):
        with mock.patch.object(kc, "http_get") as mget:
            mget.side_effect = [EMPTY, FULL]
            client = kc.KrakenClient()
            r1 = client._public("AssetPairs", {"pair": "HYPEUSD"})
            self.assertEqual(r1, {})                       # gol
            r2 = client._public("AssetPairs", {"pair": "HYPEUSD"})
            self.assertIn("HYPEUSD", r2)                   # a RE-FETCH-uit (gol necache-uit)
            self.assertEqual(mget.call_count, 2)
            # now the full result IS cached -> no new fetch
            r3 = client._public("AssetPairs", {"pair": "HYPEUSD"})
            self.assertIn("HYPEUSD", r3)
            self.assertEqual(mget.call_count, 2)

    def test_pair_info_none_on_empty_then_recovers(self):
        with mock.patch.object(kc, "http_get") as mget:
            mget.side_effect = [EMPTY, FULL]
            client = kc.KrakenClient()
            self.assertIsNone(client.pair_info("HYPEUSD"))       # gol -> None
            info = client.pair_info("HYPEUSD")                   # re-fetch -> plin
            self.assertEqual(info.get("ordermin"), "0.1")

    def test_ohlc_closes_excludes_potentially_incomplete_last_candle(self):
        response = {
            "HYPEUSD": [
                [1, "9", "11", "8", "10", "10", "1", 1],
                [2, "10", "12", "9", "11", "11", "1", 1],
                [3, "11", "99", "1", "42", "42", "1", 1],
            ],
            "last": 3,
        }
        client = kc.KrakenClient()
        with mock.patch.object(client, "_public", return_value=response):
            closes = client.ohlc_closes("HYPEUSD", 240)

        self.assertEqual(closes, [10.0, 11.0])

    def test_cache_prunes_expired_and_enforces_hard_cap(self):
        with mock.patch.object(kc, "_CACHE_MAX", 2), \
             mock.patch.object(kc.time, "time", return_value=100.0):
            kc._CACHE[("Ticker", (("pair", "OLD"),))] = (99.0, {"old": 1})
            kc._cache_put("Ticker", {"pair": "A"}, 3.0, {"a": 1})
            kc._cache_put("Ticker", {"pair": "B"}, 4.0, {"b": 1})
            kc._cache_put("Ticker", {"pair": "C"}, 5.0, {"c": 1})
        self.assertEqual(len(kc._CACHE), 2)
        self.assertNotIn(("Ticker", (("pair", "OLD"),)), kc._CACHE)
        self.assertNotIn(("Ticker", (("pair", "A"),)), kc._CACHE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
