"""
Test pentru toate modificările din sesiunea curentă:
  1. bapi_ws  — subscriber pattern (subscribe/unsubscribe/_notify_subscribers)
  2. CacheManagerInterface — query_remote_and_update_cache, on_items_update
  3. CacheCurrentPriceManager — WS→cache, HTTP fallback, subscribe_price/on_price_update
  4. Cache24PriceManager — primește exclusiv prin on_price_update, trim
  5. Integrare: BinanceWebSocketManager → CacheCurrentPriceManager → Cache24PriceManager
"""
import os, sys, json, time, tempfile, unittest
from unittest.mock import MagicMock, patch, PropertyMock

os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

# ─── mock bapi înainte de import ──────────────────────────────────────────────
mock_bapi = MagicMock()
mock_bapi.get_current_price = MagicMock(return_value=50000.0)
mock_bapi.client = MagicMock()
sys.modules.setdefault("bapi", mock_bapi)

from binance_api import bapi_ws
from binance_api.bapi_ws import BinanceWebSocketManager

# bapi e deja în sys.modules (setdefault de mai sus), importăm direct
import cacheManager as cm


# ═══════════════════════════════════════════════════════════════════════════════
# 1. bapi_ws — subscriber pattern
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
        self.ws._notify_subscribers("BTCUSDC", [1.0])   # nu trebuie să arunce
        good.on_items_update.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers — creează manageri cu fișiere temporare
# ═══════════════════════════════════════════════════════════════════════════════
SYMBOLS = ["BTCUSDC"]

def _make_current_price_manager(tmp_dir, price=50000.0):
    mock_bapi.get_current_price.return_value = price
    filename = os.path.join(tmp_dir, "cache_currentprice.json")
    mgr = cm.CacheCurrentPriceManager(
        sync_ts=9999,      # timer practic oprit
        symbols=SYMBOLS,
        filename=filename,
        ws_manager=None,
        api_client=mock_bapi,
        market_api=mock_bapi,   # fetch-ul HTTP trece prin fațada market-data (injectabilă)
    )
    mgr.enable_save_state_to_file()
    return mgr

def _make_cache24_manager(tmp_dir):
    filename = os.path.join(tmp_dir, "cache_24price_BTCUSDC.json")
    mgr = cm.Cache24PriceManager(
        sync_ts=9999,
        symbols=SYMBOLS,
        filename=filename,
        api_client=mock_bapi,
    )
    mgr.enable_save_state_to_file()
    return mgr


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CacheCurrentPriceManager
# ═══════════════════════════════════════════════════════════════════════════════
class TestCacheCurrentPriceManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.mgr = _make_current_price_manager(self.tmp)

    def test_on_items_update_stores_price(self):
        self.mgr.on_items_update("BTCUSDC", [55000.0])
        entry = self.mgr.get_price("BTCUSDC")
        self.assertIsNotNone(entry)
        self.assertEqual(entry[1], 55000.0)

    def test_get_price_value_returns_float(self):
        self.mgr.on_items_update("BTCUSDC", [55000.0])
        val = self.mgr.get_price_value("BTCUSDC")
        self.assertIsInstance(val, float)
        self.assertEqual(val, 55000.0)

    def test_get_price_fresh_no_http(self):
        """Dacă prețul e proaspăt (< STALE_THRESHOLD_MS) nu se apelează HTTP."""
        self.mgr.on_items_update("BTCUSDC", [55000.0])
        mock_bapi.get_current_price.reset_mock()
        self.mgr.get_price("BTCUSDC")
        mock_bapi.get_current_price.assert_not_called()

    def test_get_price_stale_forces_http(self):
        """Entry cu timestamp vechi → get_price forțează HTTP fetch."""
        mock_bapi.get_current_price.return_value = 60000.0
        # forțăm un entry cu timestamp = 0 (garantat stale)
        with self.mgr.lock:
            self.mgr.cache["BTCUSDC"] = [[0, 50000.0]]
        mock_bapi.get_current_price.reset_mock()
        val = self.mgr.get_price_value("BTCUSDC")
        mock_bapi.get_current_price.assert_called()
        self.assertEqual(val, 60000.0)

    def test_subscribe_price_notified_on_ws_update(self):
        sub = MagicMock()
        self.mgr.subscribe_price(sub)
        self.mgr.on_items_update("BTCUSDC", [62000.0])
        sub.on_price_update.assert_called_once()
        args = sub.on_price_update.call_args[0]
        self.assertEqual(args[0], "BTCUSDC")
        self.assertAlmostEqual(args[2], 62000.0)

    def test_subscribe_price_notified_on_http_fetch(self):
        """Notifică subscribers și când HTTP fetch actualizează prețul (entry stale)."""
        mock_bapi.get_current_price.return_value = 63000.0
        sub = MagicMock()
        self.mgr.subscribe_price(sub)
        # forțăm stale
        with self.mgr.lock:
            self.mgr.cache["BTCUSDC"] = [[0, 50000.0]]
        self.mgr.get_price("BTCUSDC")   # stale → HTTP → notify
        sub.on_price_update.assert_called_once()

    def test_unsubscribe_price_no_longer_notified(self):
        sub = MagicMock()
        self.mgr.subscribe_price(sub)
        self.mgr.unsubscribe_price(sub)
        self.mgr.on_items_update("BTCUSDC", [70000.0])
        sub.on_price_update.assert_not_called()

    def test_subscriber_exception_doesnt_stop_others(self):
        bad = MagicMock(); bad.on_price_update.side_effect = RuntimeError("err")
        good = MagicMock()
        self.mgr.subscribe_price(bad)
        self.mgr.subscribe_price(good)
        self.mgr.on_items_update("BTCUSDC", [1.0])
        good.on_price_update.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Cache24PriceManager
# ═══════════════════════════════════════════════════════════════════════════════
class TestCache24PriceManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        mock_bapi.get_current_price.return_value = 50000.0
        # singleton reset pt test izolat
        cm._current_price_instance = None
        self.mgr = _make_cache24_manager(self.tmp)

    def tearDown(self):
        cm._current_price_instance = None

    def test_on_price_update_appends_entry(self):
        ts = int(time.time() * 1000)
        self.mgr.on_price_update("BTCUSDC", ts, 51000.0)
        with self.mgr.lock:
            entries = self.mgr.cache.get("BTCUSDC", [])
        # cel puțin un entry cu prețul nostru
        prices = [e[1] for e in entries]
        self.assertIn(51000.0, prices)

    def test_on_price_update_multiple_entries(self):
        base_ts = int(time.time() * 1000)
        for i in range(5):
            self.mgr.on_price_update("BTCUSDC", base_ts + i * 100, 50000.0 + i)
        with self.mgr.lock:
            entries = self.mgr.cache.get("BTCUSDC", [])
        self.assertGreaterEqual(len(entries), 5)

    def test_trim_removes_old_data(self):
        # adaugă entry vechi (> KEEP_HOURS ore)
        old_ts = int((time.time() - (self.mgr.KEEP_HOURS + 1) * 3600) * 1000)
        fresh_ts = int(time.time() * 1000)
        with self.mgr.lock:
            self.mgr.cache["BTCUSDC"] = [[old_ts, 1.0], [fresh_ts, 2.0]]
        self.mgr._trim_old_data("BTCUSDC")
        with self.mgr.lock:
            entries = self.mgr.cache["BTCUSDC"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1], 2.0)

    def test_no_polling_thread(self):
        """periodic_sync nu face query_remote — doar salvează."""
        with patch.object(self.mgr, 'query_remote_and_update_cache') as mock_poll:
            time.sleep(0.05)
            mock_poll.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Integrare: BinanceWebSocketManager → CacheCurrentPriceManager → Cache24PriceManager
# ═══════════════════════════════════════════════════════════════════════════════
class TestIntegration(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cm._current_price_instance = None
        mock_bapi.get_current_price.return_value = 50000.0
        self.ws  = BinanceWebSocketManager(symbols=["BTCUSDC"])
        self.cur = _make_current_price_manager(self.tmp)
        self.h24 = _make_cache24_manager(self.tmp)
        # wirings
        self.ws.subscribe(self.cur)
        self.cur.subscribe_price(self.h24)

    def tearDown(self):
        cm._current_price_instance = None

    def test_ws_event_updates_current_price(self):
        self.ws._notify_subscribers("BTCUSDC", [55000.0])
        val = self.cur.get_price_value("BTCUSDC")
        self.assertEqual(val, 55000.0)

    def test_ws_event_propagates_to_cache24(self):
        self.ws._notify_subscribers("BTCUSDC", [56000.0])
        with self.h24.lock:
            entries = self.h24.cache.get("BTCUSDC", [])
        prices = [e[1] for e in entries]
        self.assertIn(56000.0, prices)

    def test_multiple_ws_events_all_recorded_in_cache24(self):
        prices = [50000.0, 50100.0, 50200.0, 50300.0]
        for p in prices:
            self.ws._notify_subscribers("BTCUSDC", [p])
        with self.h24.lock:
            entries = self.h24.cache.get("BTCUSDC", [])
        recorded = [e[1] for e in entries]
        for p in prices:
            self.assertIn(p, recorded)

    def test_current_price_not_in_cache24_history_duplicated(self):
        """CacheCurrentPriceManager e snapshot (append_mode=False),
           Cache24PriceManager e history (append_mode=True)."""
        self.ws._notify_subscribers("BTCUSDC", [57000.0])
        with self.cur.lock:
            cur_entries = self.cur.cache.get("BTCUSDC", [])
        # snapshot → exact 1 entry în CacheCurrentPriceManager
        self.assertEqual(len(cur_entries), 1)

    def test_persistence_currentprice(self):
        """După restart, cache-ul este încărcat din fișier."""
        self.cur.enable_save_state_to_file()
        self.ws._notify_subscribers("BTCUSDC", [58000.0])
        self.cur.save_state_to_file_if_enabled()
        # nou manager din același fișier
        mgr2 = cm.CacheCurrentPriceManager(
            sync_ts=9999, symbols=SYMBOLS,
            filename=self.cur.filename, api_client=mock_bapi, market_api=mock_bapi,
        )
        # citim direct din cache (nu prin get_price care are staleness check)
        with mgr2.lock:
            entries = mgr2.cache.get("BTCUSDC", [])
        self.assertTrue(entries, "cache gol după load")
        self.assertEqual(entries[0][1], 58000.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
