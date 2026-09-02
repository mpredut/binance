"""
Bridge-ul event-driven WS -> cache (28 iul: fost test_session_changes.py, un
nume vag de "modificari de sesiune"; redenumit + curatat).

Pastrat AICI (unic, neacoperit altundeva):
  1. bapi_ws — subscriber pattern (subscribe/unsubscribe/_notify_subscribers).
  4. Integrare end-to-end: BinanceWebSocketManager -> CacheCurrentPriceManager
     -> Cache24PriceManager (propagarea unui eveniment WS pe tot lantul).

ELIMINAT de aici (era REDUNDANT): testele izolate pt CacheCurrentPriceManager
and Cache24PriceManager — test_cache_manager_full.py has SUPERSET versions of
them (they cover the same behaviours plus many more). No coverage is lost.
"""
import os, sys, tempfile, unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

# ─── mock bapi before the import ────────────────────────────────────────────
mock_bapi = MagicMock()
mock_bapi.get_current_price = MagicMock(return_value=50000.0)
mock_bapi.client = MagicMock()
sys.modules.setdefault("bapi", mock_bapi)

from binance_api import bapi_ws  # noqa: E402,F401
from binance_api.bapi_ws import BinanceWebSocketManager  # noqa: E402
import cacheManager as cm  # noqa: E402

SYMBOLS = ["BTCUSDC"]


# ═══════════════════════════════════════════════════════════════════════════════
# 1. bapi_ws — subscriber pattern (UNIC — neacoperit in test_bapi_ws.py)
# ═══════════════════════════════════════════════════════════════════════════════
class TestBapiWsSubscriber(unittest.TestCase):

    def setUp(self):
        self.ws = BinanceWebSocketManager()

    def test_subscribe_adds_subscriber(self):
        sub = MagicMock()
        self.ws.subscribe(sub)
        self.assertIn(sub, self.ws._subscribers)

    def test_subscribe_no_duplicates(self):
        sub = MagicMock()
        self.ws.subscribe(sub)
        self.ws.subscribe(sub)
        self.assertEqual(self.ws._subscribers.count(sub), 1)

    def test_unsubscribe_removes_subscriber(self):
        sub = MagicMock()
        self.ws.subscribe(sub)
        self.ws.unsubscribe(sub)
        self.assertNotIn(sub, self.ws._subscribers)

    def test_notify_calls_on_items_update(self):
        sub = MagicMock()
        self.ws.subscribe(sub)
        self.ws._notify_subscribers("BTCUSDC", [50000.0])
        sub.on_items_update.assert_called_once_with("BTCUSDC", [50000.0])

    def test_notify_multiple_subscribers(self):
        subs = [MagicMock() for _ in range(3)]
        for s in subs:
            self.ws.subscribe(s)
        self.ws._notify_subscribers("TAOUSDC", [300.0])
        for s in subs:
            s.on_items_update.assert_called_once_with("TAOUSDC", [300.0])

    def test_notify_subscriber_exception_doesnt_stop_others(self):
        bad = MagicMock()
        bad.on_items_update.side_effect = RuntimeError("boom")
        good = MagicMock()
        self.ws.subscribe(bad)
        self.ws.subscribe(good)
        self.ws._notify_subscribers("BTCUSDC", [1.0])   # must not raise
        good.on_items_update.assert_called_once()


# ─── Helpers pt integrare ─────────────────────────────────────────────────────
def _make_current_price_manager(tmp_dir, price=50000.0):
    mock_bapi.get_current_price.return_value = price
    mgr = cm.CacheCurrentPriceManager(
        sync_ts=9999, symbols=SYMBOLS,
        filename=os.path.join(tmp_dir, "cache_currentprice.json"),
        ws_manager=None, api_client=mock_bapi, market_api=mock_bapi,
    )
    mgr.enable_save_state_to_file()
    return mgr


def _make_cache24_manager(tmp_dir):
    mgr = cm.Cache24PriceManager(
        sync_ts=9999, symbols=SYMBOLS,
        filename=os.path.join(tmp_dir, "cache_24price_BTCUSDC.json"),
        api_client=mock_bapi,
    )
    mgr.enable_save_state_to_file()
    return mgr


# ═══════════════════════════════════════════════════════════════════════════════
# 2. End-to-end integration: WS -> CurrentPrice -> Cache24 (UNIQUE — it is not in
#    test_cache_manager_full.py, which tests the managers IN ISOLATION)
# ═══════════════════════════════════════════════════════════════════════════════
class TestIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cm._current_price_instance = None
        mock_bapi.get_current_price.return_value = 50000.0
        self.ws = BinanceWebSocketManager(symbols=["BTCUSDC"])
        self.cur = _make_current_price_manager(self.tmp)
        self.h24 = _make_cache24_manager(self.tmp)
        self.ws.subscribe(self.cur)
        self.cur.subscribe_price(self.h24)

    def tearDown(self):
        cm._current_price_instance = None

    def test_ws_event_updates_current_price(self):
        self.ws._notify_subscribers("BTCUSDC", [55000.0])
        self.assertEqual(self.cur.get_price_value("BTCUSDC"), 55000.0)

    def test_ws_event_propagates_to_cache24(self):
        self.ws._notify_subscribers("BTCUSDC", [56000.0])
        with self.h24.lock:
            entries = self.h24.cache.get("BTCUSDC", [])
        self.assertIn(56000.0, [e[1] for e in entries])

    def test_multiple_ws_events_all_recorded_in_cache24(self):
        prices = [50000.0, 50100.0, 50200.0, 50300.0]
        for p in prices:
            self.ws._notify_subscribers("BTCUSDC", [p])
        with self.h24.lock:
            recorded = [e[1] for e in self.h24.cache.get("BTCUSDC", [])]
        for p in prices:
            self.assertIn(p, recorded)

    def test_current_price_snapshot_not_history(self):
        """CacheCurrentPriceManager e snapshot (append_mode=False) -> 1 entry;
        Cache24PriceManager e history (append_mode=True)."""
        self.ws._notify_subscribers("BTCUSDC", [57000.0])
        with self.cur.lock:
            self.assertEqual(len(self.cur.cache.get("BTCUSDC", [])), 1)

    def test_persistence_currentprice(self):
        """After a restart, the cache is loaded from the file."""
        self.cur.enable_save_state_to_file()
        self.ws._notify_subscribers("BTCUSDC", [58000.0])
        self.cur.save_state_to_file_if_enabled()
        mgr2 = cm.CacheCurrentPriceManager(
            sync_ts=9999, symbols=SYMBOLS,
            filename=self.cur.filename, api_client=mock_bapi, market_api=mock_bapi,
        )
        with mgr2.lock:
            entries = mgr2.cache.get("BTCUSDC", [])
        self.assertTrue(entries, "empty cache after load")
        self.assertEqual(entries[0][1], 58000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
