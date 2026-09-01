"""
Test pt deadline-ul dur al fetch-ului de pret non-Binance (28 iul).

Incident: NonBinanceTrendPoller in cacheManager froze for 5.5h when
get_current_price(HYPEUSD) s-a blocat pe un DNS getaddrinfo (neacoperit de
the read timeout in hl_client). Fix: _fetch_price_with_deadline runs
fetch-ul intr-un worker separat cu future.result(timeout) -> orice blocaj
ridica TimeoutError in loc sa inghete poller-ul.
"""
import os
import sys
import time
import unittest
import concurrent.futures as futures

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import cacheManager as cm


class _FakeApi:
    """market_api fals: get_current_price fie intoarce rapid, fie se BLOCHEAZA
    (simuleaza DNS/retea hung), dupa flag."""
    def __init__(self, hang=False, price=55.5):
        self.hang = hang
        self.price = price
        self.calls = 0

    def get_current_price(self, symbol=None):
        self.calls += 1
        # blocaj TRANZITORIU: sta blocat cat timp flag-ul e True, se deblocheaza
        # when the network "comes back" (hang=False) — like a DNS/socket recovering.
        waited = 0.0
        while self.hang and waited < 30:
            time.sleep(0.05); waited += 0.05
        return self.price


class TestFetchDeadline(unittest.TestCase):
    def setUp(self):
        self.pool = futures.ThreadPoolExecutor(max_workers=2)

    def tearDown(self):
        if hasattr(self, "blocked_api"):
            self.blocked_api.hang = False
        self.pool.shutdown(wait=True, cancel_futures=True)

    def test_normal_fetch_returns_price(self):
        api = _FakeApi(hang=False, price=55.5)
        p = cm._fetch_price_with_deadline(api, "HYPEUSD", self.pool, deadline_sec=2)
        self.assertEqual(p, 55.5)

    def test_hung_fetch_raises_timeout_not_blocks(self):
        api = _FakeApi(hang=True)
        self.blocked_api = api
        t0 = time.time()
        with self.assertRaises(futures.TimeoutError):
            cm._fetch_price_with_deadline(api, "HYPEUSD", self.pool, deadline_sec=1)
        # must return at ~the deadline (1s), NOT wait out the 30s hang
        self.assertLess(time.time() - t0, 5,
                        "the hard deadline must return in ~1s, not wait for the 30s hang")

    def test_poller_survives_a_hang_and_recovers(self):
        """The poller, with a fetch that hangs and then recovers, must NOT
        sa inghete: dupa hang, la un ciclu urmator cu fetch bun, impinge pretul."""
        pushed = []
        api = _FakeApi(hang=True)

        class _Cpm:
            market_api = api
            def _push_price(self, s, p):  # noqa: N802 (semnatura ca in cacheManager)
                pushed.append((s, p))

        cpm = _Cpm()
        # interval mic + deadline scurt, ca sa observam timeout + recovery in test
        poller = cm._start_nonbinance_trend_poller(
            cpm, ["HYPEUSD"], interval_sec=0.3, fetch_deadline_sec=0.5)
        self.addCleanup(poller.stop)
        time.sleep(2)            # while the fetch is blocked -> timeouts, NOT pushes
        self.assertEqual(pushed, [], "while the fetch is blocked nothing must be pushed")
        api.hang = False         # reteaua revine -> fetch-urile blocate se deblocheaza
        time.sleep(3)            # cateva cicluri -> ar trebui sa impinga pretul
        self.assertTrue(any(s == "HYPEUSD" for s, _ in pushed),
                        "once the network returns, the poller must recover and push the price")


if __name__ == "__main__":
    unittest.main()
