"""KrakenProvider.min_order_qty: un lookup ESUAT (blip DNS / AssetPairs gol ->
ordermin 0) must NOT be cached — otherwise the cached 0 permanently disables the guard
anti-'volume minimum not met' din monitortrades._place_guarded, producand churn de
and dust orders get rejected (e.g. HYPE 0.0175 < min 0.1). Only POSITIVE values are cached."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from providers.kraken_provider import KrakenProvider


class _FakeClient:
    """pair_info() intoarce pe rand valorile date; o exceptie in lista e ridicata."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def pair_info(self, symbol):
        self.calls += 1
        r = self._responses.pop(0) if self._responses else {}
        if isinstance(r, Exception):
            raise r
        return r


def _provider(fake):
    p = KrakenProvider()
    p._client = lambda: fake      # stub: injecteaza clientul fake
    return p


class MinOrderQtyCacheTest(unittest.TestCase):
    def test_failed_lookup_not_cached_then_recovers(self):
        for label, first_response in (("empty", {}), ("exception", RuntimeError("blip DNS"))):
            with self.subTest(case=label):
                fake = _FakeClient([first_response, {"ordermin": "0.1"}])
                p = _provider(fake)
                self.assertEqual(p.min_order_qty("HYPEUSD"), 0.0)
                self.assertNotIn("HYPEUSD", p._minqty)
                self.assertEqual(p.min_order_qty("HYPEUSD"), 0.1)
                self.assertEqual(p._minqty.get("HYPEUSD"), 0.1)

    def test_positive_cached_no_refetch(self):
        fake = _FakeClient([{"ordermin": "0.1"}])
        p = _provider(fake)
        self.assertEqual(p.min_order_qty("HYPEUSD"), 0.1)
        self.assertEqual(p.min_order_qty("HYPEUSD"), 0.1)  # a doua oara din cache
        self.assertEqual(fake.calls, 1)                    # fara re-fetch


if __name__ == "__main__":
    unittest.main(verbosity=2)
