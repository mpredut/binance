"""
Teste comprehensive pentru cacheManager.py.
Fișierul este critic — folosit de toate modulele din proiect.

Acoperire:
  - CacheManagerInterface (via ConcreteTestManager)
  - CacheTradeManager
  - CacheOrderManager
  - CachePriceManager
  - Cache24PriceManager
  - CachePriceTrendManager
  - CacheAssetValueManager
  - CacheCurrentPriceManager
  - CacheFactory / get_cache_manager
  - get_current_price_manager (singleton)
  - funcții WS health
"""
import os, sys, json, time, tempfile, unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

# ── mock bapi înainte de orice import ────────────────────────────────────────
mock_api = MagicMock()
mock_api.get_current_price = MagicMock(return_value=50000.0)
mock_api.client = MagicMock()
mock_api.client.get_symbol_ticker = MagicMock(return_value={"price": "50000.0"})
sys.modules.setdefault("bapi", mock_api)
sys.modules.setdefault("bapi_trades", MagicMock())
sys.modules.setdefault("bapi_allorders", MagicMock())

import cacheManager as cm


# ═══════════════════════════════════════════════════════════════════════════════
# Helper — manager concret pentru testarea interfeței abstracte
# ═══════════════════════════════════════════════════════════════════════════════
class ConcreteTestManager(cm.CacheManagerInterface):
    """Implementare minimă pentru testarea CacheManagerInterface."""
    def __init__(self, sync_ts, symbols, filename, append_mode=True, api_client=None,
                 remote_items=None, append_persist=False):
        self._remote_items = remote_items or {}   # {symbol: [items]}
        super().__init__(sync_ts, symbols, filename, append_mode=append_mode,
                         api_client=api_client or MagicMock(), append_persist=append_persist)

    def rebuild_fetchtime_times(self):
        return {}

    def get_remote_items(self, symbol, startTime):
        return self._remote_items.get(symbol, [])


def _tmp_file(tmp_dir, name="cache_test.json"):
    return os.path.join(tmp_dir, name)


def _write_cache_file(fname, items_dict, fetchtime_dict=None):
    with open(fname, "w") as f:
        json.dump({"items": items_dict, "fetchtime": fetchtime_dict or {}}, f)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. CacheManagerInterface
# ═══════════════════════════════════════════════════════════════════════════════
class TestCacheManagerInterface(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    # ── load_state ────────────────────────────────────────────────────────────

    def test_load_state_from_existing_file(self):
        fname = _tmp_file(self.tmp)
        _write_cache_file(fname, {"SYM": [[1000, 50.0]]}, {"SYM": 1000})
        mgr = ConcreteTestManager(9999, ["SYM"], fname)
        with mgr.lock:
            self.assertEqual(mgr.cache["SYM"], [[1000, 50.0]])

    def test_load_state_missing_file_calls_remote(self):
        fname = _tmp_file(self.tmp, "nonexistent.json")
        remote = {"SYM": [[int(time.time()*1000), 100.0]]}
        mgr = ConcreteTestManager(9999, ["SYM"], fname, remote_items=remote)
        with mgr.lock:
            self.assertIn("SYM", mgr.cache)

    def test_load_state_corrupt_file_calls_remote(self):
        fname = _tmp_file(self.tmp)
        with open(fname, "w") as f:
            f.write("NOT_JSON{{{")
        remote = {"SYM": [[int(time.time()*1000), 77.0]]}
        mgr = ConcreteTestManager(9999, ["SYM"], fname, remote_items=remote)
        with mgr.lock:
            prices = [e[1] for e in mgr.cache.get("SYM", [])]
        self.assertIn(77.0, prices)

    # ── save_state_to_file_if_enabled ─────────────────────────────────────────

    def test_save_disabled_by_default(self):
        fname = _tmp_file(self.tmp, "no_save.json")
        mgr = ConcreteTestManager(9999, ["SYM"], fname)
        mgr.cache["SYM"] = [[999, 1.0]]
        mgr.save_state_to_file_if_enabled()
        self.assertFalse(os.path.exists(fname))

    def test_save_enabled_writes_file(self):
        fname = _tmp_file(self.tmp, "save_test.json")
        mgr = ConcreteTestManager(9999, ["SYM"], fname)
        mgr.enable_save_state_to_file()
        mgr.cache["SYM"] = [[999, 42.0]]
        mgr.save_state_to_file_if_enabled()
        self.assertTrue(os.path.exists(fname))
        with open(fname) as f:
            data = json.load(f)
        self.assertEqual(data["items"]["SYM"], [[999, 42.0]])

    def test_save_uses_tmp_then_replace(self):
        """Salvarea atomică: .tmp dispare după scriere."""
        fname = _tmp_file(self.tmp, "atomic.json")
        mgr = ConcreteTestManager(9999, ["SYM"], fname)
        mgr.enable_save_state_to_file()
        mgr.cache["SYM"] = [[1, 2.0]]
        mgr.save_state_to_file_if_enabled()
        self.assertFalse(os.path.exists(fname + ".tmp"))

    # ── update_cache_per_symbol ───────────────────────────────────────────────

    def test_update_append_mode_extends(self):
        fname = _tmp_file(self.tmp, "append.json")
        mgr = ConcreteTestManager(9999, ["SYM"], fname, append_mode=True)
        mgr.cache["SYM"] = [[1, 10.0]]
        mgr.update_cache_per_symbol("SYM", [[2, 20.0]])
        with mgr.lock:
            self.assertEqual(len(mgr.cache["SYM"]), 2)
            self.assertEqual(mgr.cache["SYM"][1][1], 20.0)

    def test_update_snapshot_mode_replaces(self):
        fname = _tmp_file(self.tmp, "snap.json")
        mgr = ConcreteTestManager(9999, ["SYM"], fname, append_mode=False)
        mgr.cache["SYM"] = [[1, 10.0]]
        mgr.update_cache_per_symbol("SYM", [[2, 20.0]])
        with mgr.lock:
            self.assertEqual(mgr.cache["SYM"], [[2, 20.0]])

    def test_update_creates_symbol_if_missing(self):
        fname = _tmp_file(self.tmp, "new_sym.json")
        mgr = ConcreteTestManager(9999, ["SYM"], fname, append_mode=True)
        mgr.update_cache_per_symbol("SYM", [[1, 5.0]])
        with mgr.lock:
            self.assertIn("SYM", mgr.cache)

    def test_update_deduplicates_in_append_mode(self):
        fname = _tmp_file(self.tmp, "dedup.json")
        mgr = ConcreteTestManager(9999, ["SYM"], fname, append_mode=True)
        mgr.cache["SYM"] = [[1, 10.0]]
        mgr.update_cache_per_symbol("SYM", [[1, 10.0]])  # duplicat
        with mgr.lock:
            self.assertEqual(len(mgr.cache["SYM"]), 1)

    def test_update_sets_fetchtime(self):
        fname = _tmp_file(self.tmp, "ft.json")
        mgr = ConcreteTestManager(9999, ["SYM"], fname, append_mode=True)
        mgr.update_cache_per_symbol("SYM", [[int(time.time()*1000), 1.0]])
        self.assertIn("SYM", mgr.fetchtime_time_per_symbol)

    # ── filter_new_items ──────────────────────────────────────────────────────

    def test_filter_removes_duplicates(self):
        fname = _tmp_file(self.tmp)
        mgr = ConcreteTestManager(9999, ["SYM"], fname)
        existing = [[1, 10.0], [2, 20.0]]
        new = [[2, 20.0], [3, 30.0]]
        result = mgr.filter_new_items(existing, new)
        self.assertEqual(result, [[3, 30.0]])

    def test_filter_all_new(self):
        fname = _tmp_file(self.tmp)
        mgr = ConcreteTestManager(9999, ["SYM"], fname)
        result = mgr.filter_new_items([], [[1, 1.0], [2, 2.0]])
        self.assertEqual(len(result), 2)

    # ── query_remote_and_update_cache ─────────────────────────────────────────

    def test_query_remote_fetches_and_stores(self):
        fname = _tmp_file(self.tmp)
        ts = int(time.time() * 1000)
        remote = {"SYM": [[ts, 99.0]]}
        mgr = ConcreteTestManager(9999, ["SYM"], fname, remote_items=remote)
        mgr.cache = {}
        mgr.fetchtime_time_per_symbol = {}
        mgr.query_remote_and_update_cache()
        with mgr.lock:
            prices = [e[1] for e in mgr.cache.get("SYM", [])]
        self.assertIn(99.0, prices)

    def test_query_remote_skips_empty(self):
        fname = _tmp_file(self.tmp)
        mgr = ConcreteTestManager(9999, ["SYM"], fname, remote_items={"SYM": []})
        mgr.cache = {}
        mgr.fetchtime_time_per_symbol = {}
        mgr.query_remote_and_update_cache()
        with mgr.lock:
            self.assertEqual(mgr.cache.get("SYM", []), [])

    def test_query_remote_continues_on_empty_symbol(self):
        """continue (nu return) la simbol fără date — celelalte simboluri se procesează."""
        fname = _tmp_file(self.tmp)
        ts = int(time.time() * 1000)
        remote = {"SYM1": [], "SYM2": [[ts, 5.0]]}
        mgr = ConcreteTestManager(9999, ["SYM1", "SYM2"], fname, remote_items=remote)
        mgr.cache = {}
        mgr.fetchtime_time_per_symbol = {}
        mgr.query_remote_and_update_cache()
        with mgr.lock:
            prices = [e[1] for e in mgr.cache.get("SYM2", [])]
        self.assertIn(5.0, prices)

    # ── on_items_update (baza) ────────────────────────────────────────────────

    def test_on_items_update_stores_entry(self):
        fname = _tmp_file(self.tmp)
        mgr = ConcreteTestManager(9999, ["SYM"], fname, append_mode=True,
                                  remote_items={"SYM": []})
        mgr.on_items_update("SYM", [[int(time.time()*1000), 123.0]])
        with mgr.lock:
            prices = [e[1] for e in mgr.cache.get("SYM", [])]
        self.assertIn(123.0, prices)


# ═══════════════════════════════════════════════════════════════════════════════
# 2. CacheTradeManager
# ═══════════════════════════════════════════════════════════════════════════════
class TestCacheTradeManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _make(self):
        api_mock = MagicMock()
        api_mock.client.get_my_trades.return_value = []
        fname = _tmp_file(self.tmp, "cache_trade.json")
        return cm.CacheTradeManager(9999, ["BTC"], fname, api_client=api_mock)

    def test_is_valid_trade_all_keys(self):
        mgr = self._make()
        valid = {'symbol': 'BTC', 'id': 1, 'orderId': 2, 'price': '100',
                 'qty': '1', 'time': 123, 'isBuyer': True}
        self.assertTrue(mgr._is_valid_trade(valid))

    def test_is_valid_trade_missing_key(self):
        mgr = self._make()
        invalid = {'symbol': 'BTC', 'id': 1}
        self.assertFalse(mgr._is_valid_trade(invalid))

    def test_rebuild_fetchtime_returns_none(self):
        mgr = self._make()
        self.assertIsNone(mgr.rebuild_fetchtime_times())

    def test_append_mode_true(self):
        mgr = self._make()
        self.assertTrue(mgr.append_mode)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CacheOrderManager
# ═══════════════════════════════════════════════════════════════════════════════
class TestCacheOrderManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _make(self):
        api_mock = MagicMock()
        fname = _tmp_file(self.tmp, "cache_order.json")
        with patch.dict(sys.modules, {"bapi_allorders": MagicMock()}):
            return cm.CacheOrderManager(9999, ["BTC"], fname, api_client=api_mock)

    def test_is_valid_order_all_keys(self):
        mgr = self._make()
        valid = {'orderId': 1, 'price': '100', 'quantity': '1',
                 'timestamp': 123, 'side': 'BUY'}
        self.assertTrue(mgr._is_valid_trade(valid))

    def test_is_valid_order_missing_key(self):
        mgr = self._make()
        self.assertFalse(mgr._is_valid_trade({'orderId': 1}))

    def test_rebuild_fetchtime_returns_none(self):
        mgr = self._make()
        self.assertIsNone(mgr.rebuild_fetchtime_times())

    def test_get_all_symbols_from_cache(self):
        mgr = self._make()
        mgr.cache = {"BTC": [], "ETH": []}
        self.assertEqual(set(mgr.get_all_symbols_from_cache()), {"BTC", "ETH"})


# ═══════════════════════════════════════════════════════════════════════════════
# 4. CachePriceManager
# ═══════════════════════════════════════════════════════════════════════════════
class TestCachePriceManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cm._current_price_instance = None

    def tearDown(self):
        cm._current_price_instance = None

    def _make(self, price=50000.0):
        api_mock = MagicMock()
        api_mock.get_current_price.return_value = price
        fname = _tmp_file(self.tmp, "cache_price_BTC.json")
        # patch get_current_price_manager pentru a folosi mock-ul nostru
        cur_mgr = MagicMock()
        cur_mgr.get_price_value.return_value = price
        with patch("cacheManager.get_current_price_manager", return_value=cur_mgr):
            mgr = cm.CachePriceManager(9999, ["BTC"], fname, api_client=api_mock)
        self._cur_mgr_mock = cur_mgr
        return mgr

    def test_rebuild_fetchtime_from_cache(self):
        mgr = self._make()
        ts = int(time.time() * 1000)
        mgr.cache = {"BTC": [[ts - 1000, 100.0], [ts, 200.0]]}
        result = mgr.rebuild_fetchtime_times()
        self.assertEqual(result["BTC"], ts)

    def test_rebuild_fetchtime_empty_cache(self):
        mgr = self._make()
        mgr.cache = {}
        result = mgr.rebuild_fetchtime_times()
        self.assertEqual(result, {})

    def test_get_remote_items_returns_price_entry(self):
        fname = _tmp_file(self.tmp, "cp.json")
        api_mock = MagicMock()
        cur_mgr = MagicMock()
        cur_mgr.get_price_value.return_value = 55000.0
        with patch("cacheManager.get_current_price_manager", return_value=cur_mgr):
            mgr = cm.CachePriceManager(9999, ["BTC"], fname, api_client=api_mock)
            result = mgr.get_remote_items("BTC", 0)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][1], 55000.0)

    def test_get_remote_items_none_price_returns_empty(self):
        fname = _tmp_file(self.tmp, "cp_none.json")
        api_mock = MagicMock()
        cur_mgr = MagicMock()
        cur_mgr.get_price_value.return_value = None
        with patch("cacheManager.get_current_price_manager", return_value=cur_mgr):
            mgr = cm.CachePriceManager(9999, ["BTC"], fname, api_client=api_mock)
            result = mgr.get_remote_items("BTC", 0)
        self.assertEqual(result, [])

    def test_append_mode_true(self):
        mgr = self._make()
        self.assertTrue(mgr.append_mode)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Cache24PriceManager
# ═══════════════════════════════════════════════════════════════════════════════
class TestCache24PriceManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cm._current_price_instance = None

    def tearDown(self):
        cm._current_price_instance = None

    def _make(self):
        api_mock = MagicMock()
        fname = _tmp_file(self.tmp, "cache_24price_BTC.json")
        cur_mgr = MagicMock()
        cur_mgr.get_price_value.return_value = 50000.0
        with patch("cacheManager.get_current_price_manager", return_value=cur_mgr):
            mgr = cm.Cache24PriceManager(9999, ["BTC"], fname, api_client=api_mock)
        return mgr

    def test_on_price_update_appends_entry(self):
        mgr = self._make()
        ts = int(time.time() * 1000)
        mgr.on_price_update("BTC", ts, 60000.0)
        with mgr.lock:
            prices = [e[1] for e in mgr.cache.get("BTC", [])]
        self.assertIn(60000.0, prices)

    def test_on_price_update_multiple_entries_all_kept(self):
        mgr = self._make()
        base_ts = int(time.time() * 1000)
        for i in range(5):
            mgr.on_price_update("BTC", base_ts + i*1000, 50000.0 + i)
        with mgr.lock:
            self.assertGreaterEqual(len(mgr.cache.get("BTC", [])), 5)

    def test_trim_removes_entries_older_than_keep_hours(self):
        mgr = self._make()
        old_ts  = int((time.time() - (mgr.KEEP_HOURS + 1) * 3600) * 1000)
        fresh_ts = int(time.time() * 1000)
        with mgr.lock:
            mgr.cache["BTC"] = [[old_ts, 1.0], [fresh_ts, 2.0]]
        mgr._trim_old_data("BTC")
        with mgr.lock:
            entries = mgr.cache["BTC"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1], 2.0)

    def test_trim_keeps_all_fresh_entries(self):
        mgr = self._make()
        now = int(time.time() * 1000)
        with mgr.lock:
            mgr.cache["BTC"] = [[now - 100, 1.0], [now, 2.0]]
        mgr._trim_old_data("BTC")
        with mgr.lock:
            self.assertEqual(len(mgr.cache["BTC"]), 2)

    def test_rebuild_fetchtime_max_timestamp(self):
        mgr = self._make()
        mgr.cache = {"BTC": [[100, 1.0], [500, 2.0], [300, 3.0]]}
        result = mgr.rebuild_fetchtime_times()
        self.assertEqual(result["BTC"], 500)

    def test_no_polling_only_saves(self):
        mgr = self._make()
        with patch.object(mgr, "query_remote_and_update_cache") as mock_poll:
            time.sleep(0.1)
            mock_poll.assert_not_called()

    def test_append_mode_true(self):
        mgr = self._make()
        self.assertTrue(mgr.append_mode)


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CachePriceTrendManager
# ═══════════════════════════════════════════════════════════════════════════════
class TestCachePriceTrendManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _make(self):
        api_mock = MagicMock()
        fname = _tmp_file(self.tmp, "cache_price_trend.json")
        return cm.CachePriceTrendManager(9999, ["BTC"], fname, api_client=api_mock)

    def test_rebuild_fetchtime_from_dict_cache(self):
        mgr = self._make()
        mgr.cache = {
            "BTC": [{"timestamp": 100}, {"timestamp": 500}, {"timestamp": 300}]
        }
        result = mgr.rebuild_fetchtime_times()
        # max minus offset 60s
        self.assertEqual(result["BTC"], max(0, 500 * 1000 - 60_000))

    def test_rebuild_fetchtime_empty(self):
        mgr = self._make()
        mgr.cache = {}
        result = mgr.rebuild_fetchtime_times()
        self.assertEqual(result, {})

    def test_get_remote_items_missing_file(self):
        mgr = self._make()
        # priceanalysis.json nu există → []
        with patch("os.path.exists", return_value=False):
            result = mgr.get_remote_items("BTC", 0)
        self.assertEqual(result, [])

    def test_get_remote_items_symbol_not_in_data(self):
        mgr = self._make()
        fake_data = {"ETH": {"trend": "up"}}
        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(fake_data))):
            with patch("os.path.exists", return_value=True):
                result = mgr.get_remote_items("BTC", 0)
        self.assertEqual(result, [])

    def test_get_remote_items_returns_symbol_data(self):
        mgr = self._make()
        fake_data = {"BTC": {"trend": "up", "score": 0.9}}
        with patch("builtins.open", unittest.mock.mock_open(read_data=json.dumps(fake_data))):
            with patch("os.path.exists", return_value=True):
                result = mgr.get_remote_items("BTC", 0)
        self.assertEqual(result, [{"trend": "up", "score": 0.9}])

    def test_append_mode_false(self):
        mgr = self._make()
        self.assertFalse(mgr.append_mode)


# ═══════════════════════════════════════════════════════════════════════════════
# 7. CacheAssetValueManager
# ═══════════════════════════════════════════════════════════════════════════════
class TestCacheAssetValueManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _make(self, total_value=1000.0):
        api_mock = MagicMock()
        api_mock.get_total_assets_value_usdt.return_value = total_value
        fname = _tmp_file(self.tmp, "cache_asset_value.json")
        return cm.CacheAssetValueManager(9999, ["TOTAL"], fname, api_client=api_mock)

    def test_rebuild_fetchtime_from_timestamp_field(self):
        mgr = self._make()
        mgr.cache = {
            "TOTAL": [{"timestamp": 200, "total_value_usdt": 1000.0},
                      {"timestamp": 500, "total_value_usdt": 1100.0}]
        }
        result = mgr.rebuild_fetchtime_times()
        self.assertEqual(result["TOTAL"], max(0, 500 * 1000 - 60_000))

    def test_get_remote_items_returns_snapshot(self):
        mgr = self._make(total_value=2500.0)
        result = mgr.get_remote_items("TOTAL", 0)
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result[0]["total_value_usdt"], 2500.0)
        self.assertIn("timestamp", result[0])
        self.assertIn("datetime_local", result[0])

    def test_get_remote_items_none_value_returns_empty(self):
        mgr = self._make()
        mgr.api_client.get_total_assets_value_usdt.return_value = None
        result = mgr.get_remote_items("TOTAL", 0)
        self.assertEqual(result, [])

    def test_get_remote_items_zero_value_returns_empty(self):
        mgr = self._make()
        mgr.api_client.get_total_assets_value_usdt.return_value = 0
        result = mgr.get_remote_items("TOTAL", 0)
        self.assertEqual(result, [])

    def test_append_mode_true(self):
        mgr = self._make()
        self.assertTrue(mgr.append_mode)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. CacheCurrentPriceManager
# ═══════════════════════════════════════════════════════════════════════════════
class TestCacheCurrentPriceManager(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        cm._current_price_instance = None

    def tearDown(self):
        cm._current_price_instance = None

    def _make(self, price=50000.0):
        api_mock = MagicMock()
        api_mock.get_current_price.return_value = price
        fname = _tmp_file(self.tmp, "cache_currentprice.json")
        return cm.CacheCurrentPriceManager(
            sync_ts=9999, symbols=["BTC"], filename=fname,
            ws_manager=None, api_client=api_mock
        ), api_mock

    # ── on_items_update ───────────────────────────────────────────────────────

    def test_on_items_update_stores_price(self):
        mgr, _ = self._make()
        mgr.on_items_update("BTC", [55000.0])
        with mgr.lock:
            entries = mgr.cache.get("BTC", [])
        self.assertTrue(entries)
        self.assertEqual(entries[0][1], 55000.0)

    def test_on_items_update_updates_ws_timestamp(self):
        mgr, _ = self._make()
        before = time.time()
        mgr.on_items_update("BTC", [1.0])
        self.assertGreaterEqual(mgr._ws_last_event_ts, before)

    def test_on_items_update_ignores_none_price(self):
        mgr, _ = self._make()
        # Clear any entries populated during __init__ (file missing → remote fetch)
        with mgr.lock:
            mgr.cache.clear()
        mgr.on_items_update("BTC", [])
        with mgr.lock:
            entries = mgr.cache.get("BTC", [])
        self.assertFalse(entries)

    def test_on_items_update_snapshot_mode_replaces(self):
        mgr, _ = self._make()
        mgr.on_items_update("BTC", [50000.0])
        mgr.on_items_update("BTC", [60000.0])
        with mgr.lock:
            entries = mgr.cache["BTC"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0][1], 60000.0)

    # ── get_price / get_price_value ───────────────────────────────────────────

    def test_get_price_fresh_returns_cached(self):
        mgr, api_mock = self._make()
        mgr.on_items_update("BTC", [55000.0])
        api_mock.get_current_price.reset_mock()
        entry = mgr.get_price("BTC")
        self.assertIsNotNone(entry)
        self.assertEqual(entry[1], 55000.0)
        api_mock.get_current_price.assert_not_called()

    def test_get_price_stale_forces_http(self):
        mgr, api_mock = self._make()
        api_mock.get_current_price.return_value = 65000.0
        with mgr.lock:
            mgr.cache["BTC"] = [[0, 50000.0]]   # timestamp=0 → stale garantat
        api_mock.get_current_price.reset_mock()
        entry = mgr.get_price("BTC")
        api_mock.get_current_price.assert_called()
        self.assertEqual(entry[1], 65000.0)

    def test_get_price_missing_forces_http(self):
        mgr, api_mock = self._make()
        api_mock.get_current_price.return_value = 70000.0
        with mgr.lock:
            mgr.cache = {}
        api_mock.get_current_price.reset_mock()
        entry = mgr.get_price("BTC")
        api_mock.get_current_price.assert_called()
        self.assertEqual(entry[1], 70000.0)

    def test_get_price_returns_timestamp_and_value(self):
        mgr, _ = self._make()
        mgr.on_items_update("BTC", [55000.0])
        entry = mgr.get_price("BTC")
        self.assertEqual(len(entry), 2)
        self.assertIsInstance(entry[0], int)  # timestamp ms
        self.assertIsInstance(entry[1], float)

    def test_get_price_value_returns_float(self):
        mgr, _ = self._make()
        mgr.on_items_update("BTC", [55000.0])
        val = mgr.get_price_value("BTC")
        self.assertIsInstance(val, float)
        self.assertEqual(val, 55000.0)

    def test_get_price_value_none_if_unavailable(self):
        mgr, api_mock = self._make()
        api_mock.get_current_price.return_value = None
        with mgr.lock:
            mgr.cache = {}
        val = mgr.get_price_value("BTC")
        self.assertIsNone(val)

    # ── subscribe_price / unsubscribe_price ───────────────────────────────────

    def test_subscribe_adds_subscriber(self):
        mgr, _ = self._make()
        sub = MagicMock()
        mgr.subscribe_price(sub)
        with mgr.lock:
            self.assertIn(sub, mgr._price_subscribers)

    def test_subscribe_no_duplicates(self):
        mgr, _ = self._make()
        sub = MagicMock()
        mgr.subscribe_price(sub)
        mgr.subscribe_price(sub)
        with mgr.lock:
            self.assertEqual(mgr._price_subscribers.count(sub), 1)

    def test_unsubscribe_removes_subscriber(self):
        mgr, _ = self._make()
        sub = MagicMock()
        mgr.subscribe_price(sub)
        mgr.unsubscribe_price(sub)
        with mgr.lock:
            self.assertNotIn(sub, mgr._price_subscribers)

    def test_ws_update_notifies_price_subscriber(self):
        mgr, _ = self._make()
        sub = MagicMock()
        mgr.subscribe_price(sub)
        mgr.on_items_update("BTC", [62000.0])
        sub.on_price_update.assert_called_once()
        args = sub.on_price_update.call_args[0]
        self.assertEqual(args[0], "BTC")
        self.assertAlmostEqual(args[2], 62000.0)

    def test_http_fetch_notifies_price_subscriber(self):
        mgr, api_mock = self._make()
        api_mock.get_current_price.return_value = 63000.0
        sub = MagicMock()
        mgr.subscribe_price(sub)
        with mgr.lock:
            mgr.cache["BTC"] = [[0, 1.0]]   # stale
        mgr.get_price("BTC")
        sub.on_price_update.assert_called_once()

    def test_subscriber_exception_doesnt_block_others(self):
        mgr, _ = self._make()
        bad = MagicMock()
        bad.on_price_update.side_effect = RuntimeError("crash")
        good = MagicMock()
        mgr.subscribe_price(bad)
        mgr.subscribe_price(good)
        mgr.on_items_update("BTC", [1.0])
        good.on_price_update.assert_called_once()

    def test_unsubscribed_not_notified(self):
        mgr, _ = self._make()
        sub = MagicMock()
        mgr.subscribe_price(sub)
        mgr.unsubscribe_price(sub)
        mgr.on_items_update("BTC", [1.0])
        sub.on_price_update.assert_not_called()

    # ── WS health ─────────────────────────────────────────────────────────────

    def test_ws_healthy_when_recent_event(self):
        mgr, _ = self._make()
        mgr._ws_last_event_ts = time.time()
        self.assertTrue(mgr._ws_is_healthy())

    def test_ws_unhealthy_when_old_event(self):
        mgr, _ = self._make()
        mgr._ws_last_event_ts = 0.0
        self.assertFalse(mgr._ws_is_healthy())

    # ── persistență ──────────────────────────────────────────────────────────

    def test_persistence_reload(self):
        mgr, api_mock = self._make()
        mgr.enable_save_state_to_file()
        mgr.on_items_update("BTC", [58000.0])
        mgr.save_state_to_file_if_enabled()

        mgr2 = cm.CacheCurrentPriceManager(
            sync_ts=9999, symbols=["BTC"],
            filename=mgr.filename, api_client=api_mock
        )
        with mgr2.lock:
            entries = mgr2.cache.get("BTC", [])
        self.assertTrue(entries)
        self.assertEqual(entries[0][1], 58000.0)


# ═══════════════════════════════════════════════════════════════════════════════
# 9. WS health functions
# ═══════════════════════════════════════════════════════════════════════════════
class TestWsHealthFunctions(unittest.TestCase):

    def setUp(self):
        cm._ws_available = False
        cm._ws_last_event_ts = 0.0
        cm._ws_is_healthy = False

    def test_mark_ws_available_true(self):
        cm._mark_ws_available(True)
        with cm._ws_health_lock:
            self.assertTrue(cm._ws_available)

    def test_mark_ws_available_false(self):
        cm._mark_ws_available(False)
        with cm._ws_health_lock:
            self.assertFalse(cm._ws_available)

    def test_mark_ws_event_received_sets_healthy(self):
        cm._mark_ws_event_received()
        with cm._ws_health_lock:
            self.assertTrue(cm._ws_is_healthy)
            self.assertGreater(cm._ws_last_event_ts, 0)

    def test_mark_ws_unhealthy(self):
        cm._mark_ws_event_received()
        cm._mark_ws_unhealthy()
        with cm._ws_health_lock:
            self.assertFalse(cm._ws_is_healthy)

    def test_should_poll_ws_only_mode_off(self):
        cm.WS_ONLY_MODE = False
        self.assertTrue(cm._should_poll_for_manager("CacheOrderManager"))

    def test_should_poll_ws_only_mode_on_not_managed(self):
        cm.WS_ONLY_MODE = True
        # CachePriceManager nu e în lista managed → polling mereu
        self.assertTrue(cm._should_poll_for_manager("CachePriceManager"))
        cm.WS_ONLY_MODE = False

    def test_should_poll_ws_only_mode_on_ws_unavailable(self):
        cm.WS_ONLY_MODE = True
        cm._ws_available = False
        self.assertTrue(cm._should_poll_for_manager("CacheOrderManager"))
        cm.WS_ONLY_MODE = False


# ═══════════════════════════════════════════════════════════════════════════════
# 10. CacheFactory / get_cache_manager
# ═══════════════════════════════════════════════════════════════════════════════
class TestCacheFactory(unittest.TestCase):

    def setUp(self):
        # reset singleton-uri
        cm.CacheFactory._instances = {}
        cm._current_price_instance = None

    def tearDown(self):
        cm.CacheFactory._instances = {}
        cm._current_price_instance = None

    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            cm.CacheFactory.get("NonExistent")

    def test_returns_same_instance_twice(self):
        with patch("cacheManager.get_current_price_manager", return_value=MagicMock()):
            i1 = cm.CacheFactory.get("CurrentPrice", symbols=["BTC"])
            i2 = cm.CacheFactory.get("CurrentPrice", symbols=["BTC"])
        self.assertIs(i1, i2)

    def test_price_returns_dict_per_symbol(self):
        cur_mock = MagicMock()
        cur_mock.get_price_value.return_value = 50000.0
        with patch("cacheManager.get_current_price_manager", return_value=cur_mock):
            result = cm.CacheFactory.get("Price", symbols=["BTC", "ETH"])
        self.assertIsInstance(result, dict)
        self.assertIn("BTC", result)
        self.assertIn("ETH", result)

    def test_price24_returns_dict_per_symbol(self):
        cur_mock = MagicMock()
        cur_mock.get_price_value.return_value = 50000.0
        with patch("cacheManager.get_current_price_manager", return_value=cur_mock):
            result = cm.CacheFactory.get("Price24", symbols=["BTC"])
        self.assertIsInstance(result, dict)
        self.assertIn("BTC", result)

    def test_price_filename_per_symbol(self):
        cur_mock = MagicMock()
        cur_mock.get_price_value.return_value = 50000.0
        with patch("cacheManager.get_current_price_manager", return_value=cur_mock):
            result = cm.CacheFactory.get("Price", symbols=["BTC"])
        self.assertIn("cache_price_BTC.json", result["BTC"].filename)

    def test_price24_filename_per_symbol(self):
        cur_mock = MagicMock()
        cur_mock.get_price_value.return_value = 50000.0
        with patch("cacheManager.get_current_price_manager", return_value=cur_mock):
            result = cm.CacheFactory.get("Price24", symbols=["BTC"])
        self.assertIn("cache_24price_BTC.json", result["BTC"].filename)

    def test_correct_class_for_trade(self):
        result = cm.CacheFactory.get("Trade", symbols=["BTC"])
        self.assertIsInstance(result, cm.CacheTradeManager)

    def test_correct_class_for_order(self):
        result = cm.CacheFactory.get("Order", symbols=["BTC"])
        self.assertIsInstance(result, cm.CacheOrderManager)

    def test_correct_class_for_currentprice(self):
        result = cm.CacheFactory.get("CurrentPrice", symbols=["BTC"])
        self.assertIsInstance(result, cm.CacheCurrentPriceManager)

    def test_get_cache_manager_delegates_to_factory(self):
        r1 = cm.get_cache_manager("Trade", symbols=["BTC"])
        r2 = cm.CacheFactory.get("Trade", symbols=["BTC"])
        self.assertIs(r1, r2)


# ═══════════════════════════════════════════════════════════════════════════════
# 11. get_current_price_manager — singleton
# ═══════════════════════════════════════════════════════════════════════════════
class TestGetCurrentPriceManagerSingleton(unittest.TestCase):

    def setUp(self):
        cm._current_price_instance = None

    def tearDown(self):
        cm._current_price_instance = None

    def test_returns_cache_current_price_manager(self):
        mgr = cm.get_current_price_manager(symbols=["BTC"])
        self.assertIsInstance(mgr, cm.CacheCurrentPriceManager)

    def test_same_instance_on_repeated_calls(self):
        m1 = cm.get_current_price_manager(symbols=["BTC"])
        m2 = cm.get_current_price_manager(symbols=["BTC"])
        self.assertIs(m1, m2)

    def test_ws_manager_subscribed_if_provided(self):
        ws = MagicMock()
        mgr = cm.get_current_price_manager(ws_manager=ws, symbols=["BTC"])
        ws.subscribe.assert_called_once_with(mgr)


class TestRefreshSymbolInCache(unittest.TestCase):
    """Helper folosit de handler-ul WS pentru a reîmprospăta un singur simbol."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_refresh_single_symbol(self):
        fname = _tmp_file(self.tmp)
        remote = {"SYM": [[int(time.time()*1000), 42.0]]}
        mgr = ConcreteTestManager(9999, ["SYM"], fname, remote_items=remote)
        with mgr.lock:
            mgr.cache["SYM"] = []
        cm._refresh_symbol_in_cache(mgr, "SYM")
        with mgr.lock:
            self.assertIn(42.0, [e[1] for e in mgr.cache.get("SYM", [])])

    def test_refresh_missing_symbol_no_crash(self):
        fname = _tmp_file(self.tmp, "x.json")
        mgr = ConcreteTestManager(9999, ["SYM"], fname, remote_items={})
        cm._refresh_symbol_in_cache(mgr, "NOPE")   # nu trebuie să arunce


class TestFactorySingletonWarning(unittest.TestCase):
    """Singleton pe nume: simbolurile diferite la apeluri ulterioare sunt ignorate."""

    def setUp(self):
        cm.CacheFactory._instances.pop("AssetValue", None)

    def tearDown(self):
        cm.CacheFactory._instances.pop("AssetValue", None)

    def test_same_instance_returned_and_warns(self):
        m1 = cm.get_cache_manager("AssetValue", symbols=["TOTAL"])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            m2 = cm.get_cache_manager("AssetValue", symbols=["OTHER"])
        self.assertIs(m1, m2)                       # aceeași instanță
        self.assertIn("IGNORAT", buf.getvalue())    # a avertizat

    def test_no_warning_same_symbols(self):
        cm.get_cache_manager("AssetValue", symbols=["TOTAL"])
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cm.get_cache_manager("AssetValue", symbols=["TOTAL"])
        self.assertNotIn("IGNORAT", buf.getvalue())


class TestAppendJsonlPersist(unittest.TestCase):
    """Persistență prin append JSONL pentru cache-uri pur-append (Trade/AssetValue)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_append_writes_only_delta(self):
        fname = os.path.join(self.tmp, "t.jsonl")
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        m.save_state = True
        with m.lock:
            m.cache["SYM"] = [{"id": 1}, {"id": 2}]
        m.save_state_to_file_if_enabled()
        n1 = sum(1 for _ in open(fname))
        self.assertEqual(n1, 2)
        # adăugăm încă unul → se scrie DOAR delta (1 linie nouă)
        with m.lock:
            m.cache["SYM"].append({"id": 3})
        m.save_state_to_file_if_enabled()
        n2 = sum(1 for _ in open(fname))
        self.assertEqual(n2, 3)   # 2 + 1, nu rescris

    def test_load_jsonl_rebuilds_cache(self):
        fname = os.path.join(self.tmp, "t2.jsonl")
        w = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        w.save_state = True
        with w.lock:
            w.cache["SYM"] = [{"id": 1}, {"id": 2}]
        w.save_state_to_file_if_enabled()
        # alt manager încarcă din JSONL la startup
        r = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        self.assertEqual(r.cache.get("SYM"), [{"id": 1}, {"id": 2}])

    def test_compact_dedups(self):
        fname = os.path.join(self.tmp, "dedup.jsonl")
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        m.save_state = True
        with m.lock:
            m.cache["SYM"] = [{"id": 1}, {"id": 1}, {"id": 2}, {"id": 2}, {"id": 3}]
        m.save_state_to_file_if_enabled()
        m.compact_jsonl()
        with m.lock:
            self.assertEqual(m.cache["SYM"], [{"id": 1}, {"id": 2}, {"id": 3}])  # dedup în memorie
        n = sum(1 for _ in open(fname))
        self.assertEqual(n, 3)   # dedup pe disc

    def test_save_to_file_unconditional_vs_if_enabled(self):
        fname = _tmp_file(self.tmp, "split.json")
        m = ConcreteTestManager(9999, ["SYM"], fname)
        m.save_state = False
        with m.lock:
            m.cache["SYM"] = [[1, 1.0]]
        m.save_state_to_file_if_enabled()       # save_state=False → NU scrie
        self.assertFalse(os.path.exists(fname))
        m.save_state_to_file()                  # neconditionat → scrie
        self.assertTrue(os.path.exists(fname))

    def test_compact_rewrites(self):
        fname = os.path.join(self.tmp, "t3.jsonl")
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        m.save_state = True
        with m.lock:
            m.cache["SYM"] = [{"id": i} for i in range(5)]
        m.save_state_to_file_if_enabled()
        m.compact_jsonl()
        n = sum(1 for _ in open(fname))
        self.assertEqual(n, 5)
        r = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        self.assertEqual(len(r.cache.get("SYM")), 5)

    def test_maintain_prunes_old_entries(self):
        fname = os.path.join(self.tmp, "p.jsonl")
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        m.save_state = True
        m.RETENTION_DAYS = 730
        now_ms = int(time.time() * 1000)
        old_ms = now_ms - 800 * 24 * 3600 * 1000   # >2 ani
        with m.lock:
            m.cache["SYM"] = [[old_ms, 1.0], [now_ms, 2.0]]
        m.save_state_to_file_if_enabled()
        m.maintain_append_persist()
        with m.lock:
            self.assertEqual(m.cache["SYM"], [[now_ms, 2.0]])   # cea veche ștearsă

    def test_rotation_archives_and_keeps_latest(self):
        fname = os.path.join(self.tmp, "r.jsonl")
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        m.save_state = True
        m.MAX_FILE_BYTES = 1          # forțăm rotația
        m.ROTATE_KEEP_FRACTION = 0.10
        now_ms = int(time.time() * 1000)
        with m.lock:
            m.cache["SYM"] = [[now_ms + i, float(i)] for i in range(100)]
        m.save_state_to_file_if_enabled()
        m.maintain_append_persist()
        # arhivă creată + memoria păstrează ultimele 10%
        archives = [f for f in os.listdir(self.tmp) if ".archive" in f]
        self.assertTrue(archives)
        with m.lock:
            self.assertEqual(len(m.cache["SYM"]), 10)
            self.assertEqual(m.cache["SYM"][-1], [now_ms + 99, 99.0])

    # ── Siguranță: rotația/mentenanța NU pierde date ──────────────────────────

    def _count_lines(self, path):
        return sum(1 for _ in open(path))

    def test_rotation_archive_has_FULL_history(self):
        """Datele NU se pierd: arhiva conține TOATE înregistrările originale."""
        fname = os.path.join(self.tmp, "full.jsonl")
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        m.save_state = True
        m.MAX_FILE_BYTES = 1
        now = int(time.time() * 1000)
        with m.lock:
            m.cache["SYM"] = [[now + i, float(i)] for i in range(100)]
        m.save_state_to_file_if_enabled()
        self.assertEqual(self._count_lines(fname), 100)
        m.maintain_append_persist()
        archive = [os.path.join(self.tmp, f) for f in os.listdir(self.tmp) if ".archive" in f][0]
        self.assertEqual(self._count_lines(archive), 100)   # arhiva = TOT istoricul
        self.assertEqual(self._count_lines(fname), 10)       # curent = ultimele 10%
        # reconstruire din arhivă → toate datele recuperabile
        r = ConcreteTestManager(9999, ["SYM"], archive, append_persist=True)
        self.assertEqual(len(r.cache["SYM"]), 100)

    def test_maintain_noop_leaves_file_intact(self):
        """Date recente, fișier mic → maintain NU șterge nimic, NU creează arhivă."""
        fname = os.path.join(self.tmp, "intact.jsonl")
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        m.save_state = True
        now = int(time.time() * 1000)
        with m.lock:
            m.cache["SYM"] = [[now, 1.0], [now + 1, 2.0]]
        m.save_state_to_file_if_enabled()
        before = open(fname).read()
        m.maintain_append_persist()
        self.assertEqual(open(fname).read(), before)         # fișier neschimbat
        self.assertEqual([f for f in os.listdir(self.tmp) if ".archive" in f], [])  # fără arhivă
        self.assertEqual(len(m.cache["SYM"]), 2)             # date intacte

    def test_maintain_missing_file_no_crash(self):
        fname = os.path.join(self.tmp, "nofile.jsonl")
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        m.maintain_append_persist()   # fișier inexistent → fără crash
        self.assertEqual([f for f in os.listdir(self.tmp) if ".archive" in f], [])

    def test_maintain_noop_when_not_append_persist(self):
        fname = os.path.join(self.tmp, "fullrw.json")
        _write_cache_file(fname, {"SYM": [[1, 1.0]]})
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=False)
        before = open(fname).read()
        m.maintain_append_persist()   # no-op pentru non-append
        self.assertEqual(open(fname).read(), before)

    def test_prune_keeps_file_and_recent(self):
        fname = os.path.join(self.tmp, "prune.jsonl")
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        m.save_state = True
        now = int(time.time() * 1000)
        old = now - 800 * 24 * 3600 * 1000
        with m.lock:
            m.cache["SYM"] = [[old, 1.0], [now, 2.0]]
        m.save_state_to_file_if_enabled()
        m.maintain_append_persist()
        self.assertTrue(os.path.exists(fname))               # fișierul EXISTĂ
        r = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        self.assertEqual(r.cache["SYM"], [[now, 2.0]])       # recentul păstrat, vechiul șters

    def test_skips_corrupt_lines(self):
        fname = os.path.join(self.tmp, "t4.jsonl")
        with open(fname, "w") as f:
            f.write(json.dumps({"s": "SYM", "i": {"id": 1}}) + "\n")
            f.write("{partial broken line\n")   # linie coruptă (crash la append)
            f.write(json.dumps({"s": "SYM", "i": {"id": 2}}) + "\n")
        r = ConcreteTestManager(9999, ["SYM"], fname, append_persist=True)
        self.assertEqual(r.cache.get("SYM"), [{"id": 1}, {"id": 2}])   # sare linia coruptă


class TestMemFileResync(unittest.TestCase):
    """Reziliență: guard anti-suprascriere date vechi + reconciliere mem↔fișier."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _mgr(self, fname, append_persist=False):
        m = ConcreteTestManager(9999, ["SYM"], fname, append_persist=append_persist)
        m.save_state = True
        return m

    def test_refuses_overwrite_with_older(self):
        fname = _tmp_file(self.tmp, "g.json")
        # Writerul A scrie date NOI (fetchtime mare)
        a = self._mgr(fname)
        with a.lock:
            a.cache["SYM"] = [[1, 1.0]]
            a.fetchtime_time_per_symbol["SYM"] = 2000   # nou
        a.save_state_to_file_if_enabled()
        # Writerul B are date VECHI (fetchtime mic) → NU trebuie să suprascrie
        b = ConcreteTestManager(9999, ["SYM"], fname)
        b.save_state = True
        with b.lock:
            b.cache["SYM"] = [[9, 9.0]]
            b.fetchtime_time_per_symbol["SYM"] = 1000   # mai vechi
        b.save_state_to_file_if_enabled()
        # fișierul rămâne cu datele lui A (nu suprascris cu B vechi)
        data = json.load(open(fname))
        self.assertEqual(data["items"]["SYM"], [[1, 1.0]])

    def test_allows_overwrite_with_newer(self):
        fname = _tmp_file(self.tmp, "g2.json")
        a = self._mgr(fname)
        with a.lock:
            a.cache["SYM"] = [[1, 1.0]]
            a.fetchtime_time_per_symbol["SYM"] = 1000
        a.save_state_to_file_if_enabled()
        b = ConcreteTestManager(9999, ["SYM"], fname)
        b.save_state = True
        with b.lock:
            b.cache["SYM"] = [[9, 9.0]]
            b.fetchtime_time_per_symbol["SYM"] = 3000   # mai NOU → suprascrie
        b.save_state_to_file_if_enabled()
        data = json.load(open(fname))
        self.assertEqual(data["items"]["SYM"], [[9, 9.0]])

    def test_resync_reloads_when_file_newer(self):
        fname = _tmp_file(self.tmp, "r.json")
        # Procesul A scrie date noi pe disc
        a = self._mgr(fname)
        with a.lock:
            a.cache["SYM"] = [[5, 5.0]]
            a.fetchtime_time_per_symbol["SYM"] = 5000
        a.save_state_to_file_if_enabled()
        # Procesul B are date vechi în memorie → resync trebuie să reîncarce
        b = ConcreteTestManager(9999, ["SYM"], fname)
        with b.lock:
            b.cache["SYM"] = [[1, 1.0]]
            b.fetchtime_time_per_symbol["SYM"] = 1000
        b.resync_mem_file()
        with b.lock:
            self.assertEqual(b.cache["SYM"], [[5, 5.0]])   # reîncărcat din fișier


class TestAtomicWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_atomic_write_json_roundtrip(self):
        p = os.path.join(self.tmp, "a.json")
        cm.atomic_write_json(p, {"x": 1, "y": [2, 3]})
        with open(p) as f:
            self.assertEqual(json.load(f), {"x": 1, "y": [2, 3]})
        self.assertFalse(os.path.exists(p + ".tmp"))   # tmp curățat

    def test_atomic_write_cleanup_on_error(self):
        p = os.path.join(self.tmp, "b.json")
        with self.assertRaises(ValueError):
            with cm.atomic_write(p) as f:
                f.write("partial")
                raise ValueError("boom")
        self.assertFalse(os.path.exists(p))            # fișierul țintă neatins
        self.assertFalse(os.path.exists(p + ".tmp"))   # tmp șters

    def test_atomic_write_preserves_old_on_error(self):
        p = os.path.join(self.tmp, "c.json")
        cm.atomic_write_json(p, {"v": "vechi"})
        with self.assertRaises(ValueError):
            with cm.atomic_write(p) as f:
                f.write("nou-incomplet")
                raise ValueError("boom")
        with open(p) as f:
            self.assertEqual(json.load(f), {"v": "vechi"})   # conținutul vechi intact


class TestWSBridgeClassify(unittest.TestCase):
    """_classify decide între ping / răspuns comandă / eveniment real."""

    def test_ping_response(self):
        kind, payload = cm.BinanceUserDataStreamBridge._classify({"id": "ping", "status": 200})
        self.assertEqual(kind, "ping")

    def test_command_response(self):
        kind, _ = cm.BinanceUserDataStreamBridge._classify({"id": "sub", "status": 200})
        self.assertEqual(kind, "response")

    def test_wrapped_event_unwrapped(self):
        inner = {"e": "executionReport", "s": "BTCUSDT", "i": 42}
        kind, payload = cm.BinanceUserDataStreamBridge._classify({"event": inner})
        self.assertEqual(kind, "event")
        self.assertEqual(payload, inner)          # despachetat din 'event'

    def test_bare_event(self):
        ev = {"e": "executionReport", "s": "BTCUSDT"}
        kind, payload = cm.BinanceUserDataStreamBridge._classify(ev)
        self.assertEqual(kind, "event")
        self.assertEqual(payload, ev)

    def test_ping_not_routed_to_handler(self):
        # bug fix: răspunsul la ping NU trebuie să fie tratat ca eveniment
        kind, _ = cm.BinanceUserDataStreamBridge._classify({"id": "ping", "status": 200})
        self.assertNotEqual(kind, "event")


if __name__ == "__main__":
    unittest.main(verbosity=2)
