"""
Testează comportamentul thread-ului de fallback din CacheCurrentPriceManager.

Scenarii:
  1. La startup cu cache încărcat din fișier, thread-ul NU suprascrie imediat.
  2. Dacă WS e sănătos, thread-ul NU face HTTP poll.
  3. Dacă WS e mort, thread-ul FACE HTTP poll după sync_ts.
  4. Dacă WS revine, thread-ul se oprește din polling.

Izolare: fiecare test folosește un MagicMock local pentru api_client,
evitând cross-contamination cauzată de thread-uri daemon rămase active.
"""
import os, sys, json, time, tempfile, unittest, threading
from unittest.mock import MagicMock

os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

# mock minim pentru import-ul modulului
_module_mock = MagicMock()
_module_mock.get_current_price = MagicMock(return_value=50000.0)
sys.modules.setdefault("bapi", _module_mock)

import cacheManager as cm

SYMBOLS = ["BTCUSDC"]


def _saved_file(tmp_dir, price, ts_ms=None):
    ts_ms = ts_ms or int(time.time() * 1000)
    fname = os.path.join(tmp_dir, "cache_currentprice.json")
    with open(fname, "w") as f:
        json.dump({
            "items":     {"BTCUSDC": [[ts_ms, price]]},
            "fetchtime": {"BTCUSDC": ts_ms},
        }, f)
    return fname


def _make_mgr(filename, sync_ts, api_mock):
    """Creează manager cu mock propriu — izolat de alte teste."""
    return cm.CacheCurrentPriceManager(
        sync_ts=sync_ts,
        symbols=SYMBOLS,
        filename=filename,
        ws_manager=None,
        api_client=api_mock,
    )


class TestFallbackThreadBehavior(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cm._current_price_instance = None

    def tearDown(self):
        cm._current_price_instance = None

    # ── Test 1: cache-ul din fișier NU e suprascris imediat ──────────────────

    def test_loaded_cache_not_overwritten_immediately(self):
        """
        Un manager nou cu sync_ts mare nu trebuie să suprascrie
        cache-ul încărcat din fișier înainte de primul sleep.
        """
        fname = _saved_file(self.tmp, price=99000.0)
        api_mock = MagicMock()
        api_mock.get_current_price.return_value = 50000.0

        mgr = _make_mgr(fname, sync_ts=9999, api_mock=api_mock)

        with mgr.lock:
            entries = mgr.cache.get("BTCUSDC", [])

        self.assertTrue(entries, "Cache gol după load din fișier")
        self.assertEqual(
            entries[0][1], 99000.0,
            f"Prețul din cache e {entries[0][1]}, nu 99000.0 — thread-ul a suprascris imediat!"
        )
        api_mock.get_current_price.assert_not_called()

    # ── Test 2: WS sănătos → NO HTTP poll ────────────────────────────────────

    def test_ws_healthy_skips_http_poll(self):
        """
        Dacă WS e sănătos (_ws_last_event_ts recent),
        thread-ul NU face HTTP poll pe toată durata testului.
        """
        fname = _saved_file(self.tmp, price=55000.0)
        api_mock = MagicMock()
        api_mock.get_current_price.return_value = 55000.0

        mgr = _make_mgr(fname, sync_ts=1, api_mock=api_mock)
        mgr._ws_last_event_ts = time.time()   # WS proaspăt

        time.sleep(2.5)   # 2 cicluri complete

        api_mock.get_current_price.assert_not_called()

    # ── Test 3: WS mort → face HTTP poll ─────────────────────────────────────

    def test_ws_dead_triggers_http_poll(self):
        """
        Cu _ws_last_event_ts = 0 (WS niciodată activ),
        thread-ul trebuie să polling HTTP după sync_ts secunde.
        """
        fname = _saved_file(self.tmp, price=55000.0)
        api_mock = MagicMock()
        api_mock.get_current_price.return_value = 55000.0

        mgr = _make_mgr(fname, sync_ts=1, api_mock=api_mock)
        mgr._ws_last_event_ts = 0.0   # WS mort explicit

        time.sleep(2.5)

        api_mock.get_current_price.assert_called()

    # ── Test 4: WS revine → poll se oprește ──────────────────────────────────

    def test_ws_recovery_stops_polling(self):
        """
        Dacă WS era mort și revine, thread-ul se oprește din polling.
        """
        fname = _saved_file(self.tmp, price=55000.0)
        api_mock = MagicMock()
        api_mock.get_current_price.return_value = 55000.0

        mgr = _make_mgr(fname, sync_ts=1, api_mock=api_mock)
        mgr._ws_last_event_ts = 0.0   # WS mort → va polua

        time.sleep(1.5)
        self.assertGreater(
            api_mock.get_current_price.call_count, 0,
            "Niciun poll cu WS mort — thread-ul nu funcționează"
        )

        # WS revine
        api_mock.get_current_price.reset_mock()
        mgr._ws_last_event_ts = time.time()

        time.sleep(2.5)   # 2 cicluri — nu ar trebui să mai polling
        self.assertEqual(
            api_mock.get_current_price.call_count, 0,
            "Thread-ul continuă să polling după ce WS a revenit"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
