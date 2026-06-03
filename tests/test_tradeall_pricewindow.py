"""
Teste pentru tradeall.py — PriceWindow, PriceTrendAnalyzer, TrendState
și integrarea cu Cache24PriceManager.

Acoperire:
  - PriceTrendAnalyzer: linreg, gradient, date insuficiente
  - PriceWindow: sample_rate_sec, recent_n, process_price, get_trend (4 valori)
  - PriceWindow.from_cache24: factory din Cache24PriceManager cu date reale
  - PriceWindow._sample_rate_from_entries: calcul rată din timestamp-uri
  - TrendState: lifecycle complet
  - CacheCurrentPriceManager: get_sample_rate / get_update_frequency
"""
import os, sys, json, time, tempfile, unittest
from collections import deque
from unittest.mock import MagicMock, patch

# ── mock-uri pentru dependențele externe (înainte de orice import local) ───
_mock_bapi = MagicMock()
_mock_bapi.quantities        = {"BTCUSDT": 0.001, "ETHUSDT": 0.01}
_mock_bapi.get_current_price = MagicMock(return_value=60000.0)
_mock_bapi.cancel_order      = MagicMock(return_value=True)
_mock_bapi.cancel_expired_orders = MagicMock()
_mock_bapi.client            = MagicMock()

_mock_sym = MagicMock()
_mock_sym.symbols   = ["BTCUSDT"]
_mock_sym.btcsymbol = "BTCUSDT"
_mock_sym.validate_ordertype = MagicMock()

for _mod, _obj in [
    ("bapi",            _mock_bapi),
    ("symbols",         _mock_sym),
    ("bapi_trades",     MagicMock(**{"get_my_trades_24.return_value": []})),
    ("bapi_allorders",  MagicMock()),
    ("bapi_placeorder", MagicMock()),
    ("alertnotifiers",  MagicMock()),
    ("generateweb",     MagicMock()),
    ("log",             MagicMock()),
    ("keys",            MagicMock()),
    ("keys.apikeys",    MagicMock(**{"api_key_ws": "fake"})),
]:
    sys.modules[_mod] = _obj

# cacheManager pornește un WS thread la import — îl blocăm
with patch("cacheManager._initialize_once", return_value=None):
    import cacheManager as cm

mock_bapi = _mock_bapi

import tradeall as ta


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _window(prices, sample_rate=0.8, symbol="BTCUSDT"):
    pw = ta.PriceWindow(symbol, len(prices), sample_rate_sec=sample_rate)
    for p in prices:
        pw.process_price(p)
    return pw


def _load_cache_prices_multi(symbol="BTCUSDC") -> list:
    """Încarcă intrările [ts_ms, price] din cache_prices_multi.json."""
    path = os.path.join(os.path.dirname(__file__), "..", "cache_prices_multi.json")
    path = os.path.normpath(path)
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    return data.get("items", {}).get(symbol, [])


def _make_cache24_manager(symbol, entries, tmp_dir):
    """Creează un Cache24PriceManager pre-populat cu entries = [[ts_ms, price], ...]."""
    fname = os.path.join(tmp_dir, f"cache_24_{symbol}.json")
    with open(fname, "w") as f:
        json.dump({"items": {symbol: entries}, "fetchtime": {}}, f)
    mgr = cm.Cache24PriceManager(
        sync_ts   = 9999,
        symbols   = [symbol],
        filename  = fname,
        api_client= mock_bapi,
    )
    return mgr


def _synthetic_entries(n=100, start_price=60000.0, delta=10.0,
                       interval_ms=800, start_ts_ms=None):
    """Generează n intrări [ts_ms, price] cu interval și delta constante."""
    if start_ts_ms is None:
        start_ts_ms = int(time.time() * 1000) - n * interval_ms
    return [
        [start_ts_ms + i * interval_ms, start_price + i * delta]
        for i in range(n)
    ]


# ═══════════════════════════════════════════════════════════════════════════
# PriceTrendAnalyzer
# ═══════════════════════════════════════════════════════════════════════════

class TestPriceTrendAnalyzer(unittest.TestCase):

    def test_linreg_uptrend(self):
        a = ta.PriceTrendAnalyzer([100 + i for i in range(10)])
        _, slope, r = a.linear_regression_trend()
        self.assertAlmostEqual(slope, 1.0, places=6)
        self.assertAlmostEqual(abs(r), 1.0, places=6)

    def test_linreg_downtrend(self):
        a = ta.PriceTrendAnalyzer([100 - i for i in range(10)])
        _, slope, _ = a.linear_regression_trend()
        self.assertLess(slope, 0)

    def test_linreg_single_price(self):
        _, slope, r = ta.PriceTrendAnalyzer([42.0]).linear_regression_trend()
        self.assertIsNone(slope)

    def test_linreg_constant(self):
        _, slope, _ = ta.PriceTrendAnalyzer([100.0] * 10).linear_regression_trend()
        self.assertIsNone(slope)

    def test_gradient_uptrend(self):
        _, avg = ta.PriceTrendAnalyzer(list(range(10))).calculate_gradient()
        self.assertGreater(avg, 0)

    def test_gradient_downtrend(self):
        _, avg = ta.PriceTrendAnalyzer([10 - i for i in range(10)]).calculate_gradient()
        self.assertLess(avg, 0)

    def test_gradient_single_price(self):
        grad_lst, avg = ta.PriceTrendAnalyzer([5.0]).calculate_gradient()
        self.assertEqual(grad_lst, [])
        self.assertEqual(avg, 0)


# ═══════════════════════════════════════════════════════════════════════════
# PriceWindow — sample_rate_sec + recent_n
# ═══════════════════════════════════════════════════════════════════════════

class TestPriceWindowSampleRate(unittest.TestCase):

    def test_default_sample_rate(self):
        pw = ta.PriceWindow("BTCUSDT", 50)
        self.assertAlmostEqual(pw.sample_rate_sec, ta.TIME_SLEEP_GET_PRICE)

    def test_custom_sample_rate(self):
        pw = ta.PriceWindow("BTCUSDT", 50, sample_rate_sec=2.0)
        self.assertAlmostEqual(pw.sample_rate_sec, 2.0)

    def test_recent_n_formula(self):
        pw = ta.PriceWindow("BTCUSDT", 50, sample_rate_sec=1.0)
        expected = max(2, int(ta.RECENT_GRADIENT_SECONDS / 1.0))
        self.assertEqual(pw.recent_n, expected)

    def test_recent_n_minimum_two(self):
        pw = ta.PriceWindow("BTCUSDT", 50, sample_rate_sec=9999.0)
        self.assertEqual(pw.recent_n, 2)

    def test_recent_n_larger_for_faster_rate(self):
        pw = ta.PriceWindow("BTCUSDT", 50, sample_rate_sec=0.5)
        n_fast = pw.recent_n
        pw.sample_rate_sec = 2.0
        n_slow = pw.recent_n
        self.assertGreater(n_fast, n_slow)

    def test_sample_rate_updatable(self):
        pw = ta.PriceWindow("BTCUSDT", 50)
        pw.sample_rate_sec = 1.5
        self.assertAlmostEqual(pw.sample_rate_sec, 1.5)


# ═══════════════════════════════════════════════════════════════════════════
# PriceWindow._sample_rate_from_entries
# ═══════════════════════════════════════════════════════════════════════════

class TestSampleRateFromEntries(unittest.TestCase):

    def test_uniform_interval(self):
        entries = [[i * 800, 100.0] for i in range(10)]
        rate = ta.PriceWindow._sample_rate_from_entries(entries)
        self.assertAlmostEqual(rate, 0.8, places=3)

    def test_single_entry_returns_default(self):
        rate = ta.PriceWindow._sample_rate_from_entries([[0, 100.0]])
        self.assertAlmostEqual(rate, ta.TIME_SLEEP_GET_PRICE)

    def test_empty_returns_default(self):
        rate = ta.PriceWindow._sample_rate_from_entries([])
        self.assertAlmostEqual(rate, ta.TIME_SLEEP_GET_PRICE)

    def test_uses_median_ignores_outlier_gap(self):
        # 8 gaps de 0.8s + 1 gap mare de 60s → median = 0.8
        entries = [[i * 800, 100.0] for i in range(9)]
        entries.append([entries[-1][0] + 60_000, 100.0])
        rate = ta.PriceWindow._sample_rate_from_entries(entries)
        self.assertAlmostEqual(rate, 0.8, places=1)

    def test_real_cache_data(self):
        entries = _load_cache_prices_multi("BTCUSDC")
        if len(entries) < 2:
            self.skipTest("cache_prices_multi.json lipsă sau prea puțin date")
        rate = ta.PriceWindow._sample_rate_from_entries(entries)
        self.assertGreater(rate, 0)
        self.assertLess(rate, 3600)   # ceva rezonabil


# ═══════════════════════════════════════════════════════════════════════════
# PriceWindow.from_cache24 — factory cu Cache24PriceManager
# ═══════════════════════════════════════════════════════════════════════════

class TestPriceWindowFromCache24(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _make(self, entries, symbol="BTCUSDT", window_seconds=None):
        mgr = _make_cache24_manager(symbol, entries, self.tmp)
        if window_seconds is None:
            window_seconds = len(entries) * 0.8
        return ta.PriceWindow.from_cache24(symbol, window_seconds, mgr)

    # ── date sintetice ──────────────────────────────────────────────────────

    def test_prices_loaded(self):
        entries = _synthetic_entries(50)
        pw = self._make(entries, window_seconds=50 * 0.8)
        self.assertGreater(len(pw.prices), 0)

    def test_window_size_bounded_by_window_seconds(self):
        entries = _synthetic_entries(200, interval_ms=800)
        pw = self._make(entries, window_seconds=60.0)  # 60s / 0.8s = 75 samples
        self.assertLessEqual(pw.window_size, 100)

    def test_sample_rate_computed_from_real_intervals(self):
        entries = _synthetic_entries(50, interval_ms=2000)  # 2s per sample
        pw = self._make(entries, window_seconds=100.0)
        self.assertAlmostEqual(pw.sample_rate_sec, 2.0, delta=0.1)

    def test_get_trend_uptrend(self):
        entries = _synthetic_entries(60, delta=10.0, interval_ms=800)
        pw = self._make(entries, window_seconds=60 * 0.8)
        final_trend, gc, sf, gr = pw.get_trend()
        self.assertEqual(final_trend, 1)
        self.assertGreater(gc, 0)

    def test_get_trend_downtrend(self):
        entries = _synthetic_entries(60, delta=-10.0, interval_ms=800)
        pw = self._make(entries, window_seconds=60 * 0.8)
        final_trend, gc, sf, gr = pw.get_trend()
        self.assertEqual(final_trend, -1)
        self.assertLess(gc, 0)

    def test_window_within_24h_only(self):
        # Intrări mai vechi de 24h ar fi eliminate de Cache24PriceManager._trim_old_data
        # Verificăm că from_cache24 cu window>24h nu crează ferestre imposibil de mari
        entries = _synthetic_entries(100, interval_ms=800)
        max_seconds = cm.Cache24PriceManager.KEEP_HOURS * 3600
        pw = self._make(entries, window_seconds=max_seconds)
        self.assertLessEqual(pw.window_size, max_seconds / pw.sample_rate_sec + 1)

    def test_minimum_window_size_ten(self):
        entries = _synthetic_entries(2)
        pw = self._make(entries, window_seconds=1.0)
        self.assertGreaterEqual(pw.window_size, 10)

    # ── date reale din cache ────────────────────────────────────────────────

    def test_from_real_cache_prices_multi(self):
        entries = _load_cache_prices_multi("BTCUSDC")
        if len(entries) < 10:
            self.skipTest("cache_prices_multi.json insuficient")
        # Timestamp-urile din fișier pot fi vechi față de time.time().
        # Înlocuim ts_ms cu valori recente păstrând intervalele originale.
        base_ts = entries[0][0]
        now_ms  = int(time.time() * 1000)
        shifted = [[now_ms + (e[0] - base_ts), e[1]] for e in entries]

        mgr = _make_cache24_manager("BTCUSDC", shifted, self.tmp)
        span_sec = (shifted[-1][0] - shifted[0][0]) / 1000.0
        pw = ta.PriceWindow.from_cache24("BTCUSDC", span_sec, mgr)
        self.assertGreater(len(pw.prices), 0)
        final_trend, gc, sf, gr = pw.get_trend()
        self.assertIn(final_trend, (-1, 0, 1))
        self.assertIsInstance(gc, float)

    def _shift_entries_to_now(self, entries):
        """Mută timestamp-urile la momentul curent păstrând intervalele."""
        base_ts = entries[0][0]
        now_ms  = int(time.time() * 1000)
        return [[now_ms + (e[0] - base_ts), e[1]] for e in entries]

    def test_real_data_recent_gradient_vs_full(self):
        entries = _load_cache_prices_multi("BTCUSDC")
        if len(entries) < 20:
            self.skipTest("date insuficiente")
        shifted = self._shift_entries_to_now(entries)
        mgr = _make_cache24_manager("BTCUSDC", shifted, self.tmp)
        span_sec = (shifted[-1][0] - shifted[0][0]) / 1000.0
        pw = ta.PriceWindow.from_cache24("BTCUSDC", span_sec, mgr)
        _, _, slope_full, gradient_recent = pw.get_trend()
        self.assertIsInstance(slope_full, float)
        self.assertIsInstance(gradient_recent, float)

    def test_small_window_vs_large_window_real_data(self):
        entries = _load_cache_prices_multi("BTCUSDC")
        if len(entries) < 30:
            self.skipTest("date insuficiente")
        shifted   = self._shift_entries_to_now(entries)
        mgr_full  = _make_cache24_manager("BTCUSDC", shifted, self.tmp)
        span_full  = (shifted[-1][0] - shifted[0][0]) / 1000.0
        span_small = min(300.0, span_full / 4)

        pw_full  = ta.PriceWindow.from_cache24("BTCUSDC", span_full,  mgr_full)
        pw_small = ta.PriceWindow.from_cache24("BTCUSDC", span_small, mgr_full)

        _, _, sf_full,  _ = pw_full.get_trend()
        _, _, sf_small, _ = pw_small.get_trend()

        self.assertIsInstance(sf_full,  float)
        self.assertIsInstance(sf_small, float)
        self.assertLessEqual(len(pw_small.prices), len(pw_full.prices))


# ═══════════════════════════════════════════════════════════════════════════
# PriceWindow — get_trend() cele 4 valori
# ═══════════════════════════════════════════════════════════════════════════

class TestPriceWindowGetTrend(unittest.TestCase):

    def test_returns_four_values(self):
        pw = _window([100 + i for i in range(20)])
        self.assertEqual(len(pw.get_trend()), 4)

    def test_uptrend(self):
        pw = _window([100 + i * 2 for i in range(30)])
        ft, gc, sf, gr = pw.get_trend()
        self.assertEqual(ft, 1)
        self.assertGreater(gc, 0)
        self.assertGreater(sf, 0)
        self.assertGreater(gr, 0)

    def test_downtrend(self):
        pw = _window([200 - i * 2 for i in range(30)])
        ft, gc, sf, gr = pw.get_trend()
        self.assertEqual(ft, -1)
        self.assertLess(gc, 0)

    def test_gc_is_average_of_sf_and_gr(self):
        pw = _window([100 + i for i in range(20)])
        _, gc, sf, gr = pw.get_trend()
        self.assertAlmostEqual(gc, (sf + gr) / 2.0, places=10)

    def test_single_price_returns_zeros(self):
        pw = ta.PriceWindow("BTCUSDT", 10, sample_rate_sec=0.8)
        pw.process_price(100.0)
        ft, gc, sf, gr = pw.get_trend()
        self.assertEqual(ft, 0)
        self.assertEqual(gc, 0.0)
        self.assertEqual(sf, 0.0)
        self.assertEqual(gr, 0.0)

    def test_constant_prices_zero(self):
        pw = _window([100.0] * 20)
        ft, gc, sf, gr = pw.get_trend()
        self.assertEqual(ft, 0)
        self.assertAlmostEqual(gc, 0.0, places=6)

    def test_recent_gradient_captures_late_reversal(self):
        # Trend general UP, dar ultimele 5 prețuri cad brusc
        prices = [100 + i for i in range(30)] + [129 - i * 8 for i in range(1, 6)]
        pw = _window(prices, sample_rate=0.8)
        _, _, _, gr = pw.get_trend()
        self.assertLess(gr, 0)   # momentumul recent e negativ

    def test_slope_full_sees_whole_window(self):
        # Trend general UP chiar dacă ultimele 2 prețuri scad ușor
        prices = [100 + i for i in range(30)] + [129, 128]
        pw = _window(prices)
        _, _, sf, _ = pw.get_trend()
        self.assertGreater(sf, 0)

    def test_final_trend_consistent_with_gc(self):
        for prices, exp in [([100 + i for i in range(20)], 1),
                            ([100 - i for i in range(20)], -1)]:
            pw = _window(prices)
            ft, gc, _, _ = pw.get_trend()
            self.assertEqual(ft, exp)
            self.assertEqual(ft, 1 if gc > 0 else -1)


# ═══════════════════════════════════════════════════════════════════════════
# PriceWindow — min/max/slope/proximities
# ═══════════════════════════════════════════════════════════════════════════

class TestPriceWindowMinMax(unittest.TestCase):

    def test_get_min_max(self):
        pw = _window([100, 105, 95, 110, 90])
        self.assertAlmostEqual(pw.get_min(), 90.0, delta=1.0)
        self.assertAlmostEqual(pw.get_max(), 110.0, delta=1.0)

    def test_sorted_consistency(self):
        pw = _window([50, 80, 60, 70, 90])
        self.assertEqual(len(pw.prices), len(pw.sorted_prices))

    def test_eviction(self):
        pw = ta.PriceWindow("BTCUSDT", 3)
        for p in [10, 20, 30, 40]:
            pw.process_price(p)
        self.assertEqual(len(pw.prices), 3)
        self.assertNotIn(10, pw.prices)

    def test_proximities_midpoint(self):
        pw = _window([100, 200])
        min_p, max_p = pw.calculate_proximities(150)
        self.assertAlmostEqual(min_p, 0.5, places=5)
        self.assertAlmostEqual(max_p, 0.5, places=5)

    def test_proximities_at_min(self):
        pw = _window([100, 200])
        min_p, max_p = pw.calculate_proximities(100)
        self.assertAlmostEqual(min_p, 0.0, places=5)
        self.assertAlmostEqual(max_p, 1.0, places=5)


# ═══════════════════════════════════════════════════════════════════════════
# TrendState
# ═══════════════════════════════════════════════════════════════════════════

class TestTrendState(unittest.TestCase):

    def _ts(self, exp_time=9999, fresh_time=60):
        return ta.TrendState(3600, exp_time, fresh_time)

    def test_initial_state(self):
        ts = self._ts()
        self.assertEqual(ts.state, "HOLD")
        self.assertEqual(ts.confirm_count, 0)

    def test_start_up(self):
        ts = self._ts()
        ts.start_trend("UP")
        self.assertEqual(ts.state, "UP")
        self.assertEqual(ts.confirm_count, 1)

    def test_start_invalid_raises(self):
        with self.assertRaises(AssertionError):
            self._ts().start_trend("INVALID")

    def test_confirm_increments(self):
        ts = self._ts()
        ts.start_trend("UP")
        ts.confirm_trend()
        self.assertEqual(ts.confirm_count, 2)

    def test_is_trend_up(self):
        ts = self._ts()
        ts.start_trend("UP")
        self.assertGreater(ts.is_trend_up(), 0)
        self.assertEqual(ts.is_trend_down(), 0)

    def test_trend_expiration(self):
        ts = self._ts(exp_time=1)
        ts.start_trend("UP")
        time.sleep(1.1)
        self.assertTrue(ts.check_trend_expiration())

    def test_is_fresh_true(self):
        ts = self._ts(fresh_time=60)
        ts.start_trend("UP")
        self.assertTrue(ts.is_trend_fresh())

    def test_is_fresh_false(self):
        ts = self._ts(fresh_time=1)
        ts.start_trend("UP")
        time.sleep(1.1)
        self.assertFalse(ts.is_trend_fresh())

    def test_older_than(self):
        ts = self._ts()
        ts.start_trend("UP")
        time.sleep(0.1)
        self.assertTrue(ts.is_started_trend_older_than(0.05))
        self.assertFalse(ts.is_started_trend_older_than(9999))

    def test_not_validated_initially(self):
        ts = self._ts()
        ts.start_trend("UP")
        self.assertFalse(ts.is_trend_a_minim_validated())


# ═══════════════════════════════════════════════════════════════════════════
# CacheCurrentPriceManager — get_sample_rate / get_update_frequency
# ═══════════════════════════════════════════════════════════════════════════

class TestCacheCurrentPriceFrequency(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        fname = os.path.join(self.tmp, "cp.json")
        self.mgr = cm.CacheCurrentPriceManager(
            sync_ts=9999, symbols=["BTCUSDT"],
            filename=fname, ws_manager=None, api_client=mock_bapi,
        )

    def test_fallback_no_data(self):
        self.assertAlmostEqual(self.mgr.get_sample_rate("BTCUSDT", fallback=0.8), 0.8)

    def test_frequency_no_data(self):
        self.assertEqual(self.mgr.get_update_frequency("BTCUSDT"), 0.0)

    def test_sample_rate_two_updates(self):
        self.mgr.on_items_update("BTCUSDT", [60000.0])
        time.sleep(0.15)
        self.mgr.on_items_update("BTCUSDT", [60001.0])
        rate = self.mgr.get_sample_rate("BTCUSDT", fallback=9.9)
        self.assertGreater(rate, 0.0)
        self.assertLess(rate, 1.0)

    def test_frequency_positive_after_updates(self):
        for _ in range(5):
            self.mgr.on_items_update("BTCUSDT", [60000.0])
        self.assertGreater(self.mgr.get_update_frequency("BTCUSDT"), 0.0)

    def test_old_timestamps_trimmed(self):
        old_ts = time.time() - cm.CacheCurrentPriceManager.FREQ_WINDOW_SEC - 10
        self.mgr._update_timestamps["BTCUSDT"].append(old_ts)
        self.mgr.on_items_update("BTCUSDT", [60000.0])
        dq = self.mgr._update_timestamps["BTCUSDT"]
        cutoff = time.time() - cm.CacheCurrentPriceManager.FREQ_WINDOW_SEC - 1
        self.assertTrue(all(t > cutoff for t in dq))

    def test_single_update_returns_fallback(self):
        self.mgr.on_items_update("BTCUSDT", [60000.0])
        self.assertAlmostEqual(self.mgr.get_sample_rate("BTCUSDT", fallback=1.23), 1.23)


# ═══════════════════════════════════════════════════════════════════════════
# PriceWindow — wiring complet Cache24PriceManager → PriceWindow
# ═══════════════════════════════════════════════════════════════════════════

class TestPriceWindowCache24Wiring(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _make_wired(self, entries, symbol="BTCUSDT", window_seconds=None):
        """Creează Cache24PriceManager + PriceWindow abonat la el."""
        mgr = _make_cache24_manager(symbol, entries, self.tmp)
        if window_seconds is None:
            window_seconds = len(entries) * 0.8
        pw = ta.PriceWindow.from_cache24(symbol, window_seconds, mgr)
        return pw, mgr

    def test_subscribed_flag_set_after_from_cache24(self):
        entries = _synthetic_entries(20)
        pw, _ = self._make_wired(entries)
        self.assertTrue(pw._subscribed_to_cache24)

    def test_not_subscribed_by_default(self):
        pw = ta.PriceWindow("BTCUSDT", 50)
        self.assertFalse(pw._subscribed_to_cache24)

    def test_on_price_update_ignored_for_wrong_symbol(self):
        entries = _synthetic_entries(10)
        pw, mgr = self._make_wired(entries, symbol="BTCUSDT")
        n_before = len(pw.prices)
        pw.on_price_update("ETHUSDT", int(time.time() * 1000), 3000.0)
        self.assertEqual(len(pw.prices), n_before)

    def test_on_price_update_adds_price(self):
        entries = _synthetic_entries(10)
        pw, mgr = self._make_wired(entries, symbol="BTCUSDT")
        n_before = len(pw.prices)
        pw.on_price_update("BTCUSDT", int(time.time() * 1000), 99999.0)
        self.assertEqual(len(pw.prices), min(n_before + 1, pw.window_size))

    def test_cache24_notifies_pricewindow(self):
        """Când Cache24PriceManager primește un preț nou, PriceWindow e actualizat automat."""
        entries = _synthetic_entries(10)
        pw, mgr = self._make_wired(entries, symbol="BTCUSDT")
        n_before = len(pw.prices)
        ts_ms = int(time.time() * 1000)
        mgr.on_price_update("BTCUSDT", ts_ms, 77777.0)
        self.assertEqual(len(pw.prices), min(n_before + 1, pw.window_size))
        self.assertIn(77777.0, pw.prices)

    def test_unsubscribe_stops_updates(self):
        entries = _synthetic_entries(10)
        pw, mgr = self._make_wired(entries, symbol="BTCUSDT")
        pw.unsubscribe_from_cache24(mgr)
        self.assertFalse(pw._subscribed_to_cache24)
        n_before = len(pw.prices)
        mgr.on_price_update("BTCUSDT", int(time.time() * 1000), 55555.0)
        self.assertEqual(len(pw.prices), n_before)  # nu s-a actualizat

    def test_multiple_windows_same_cache24(self):
        """Două ferestre (mică și mare) pot fi abonate la același Cache24."""
        entries = _synthetic_entries(50)
        mgr = _make_cache24_manager("BTCUSDT", entries, self.tmp)
        pw_small = ta.PriceWindow.from_cache24("BTCUSDT", 20 * 0.8, mgr)
        pw_big   = ta.PriceWindow.from_cache24("BTCUSDT", 50 * 0.8, mgr)

        ts_ms = int(time.time() * 1000)
        mgr.on_price_update("BTCUSDT", ts_ms, 12345.0)

        self.assertIn(12345.0, pw_small.prices)
        self.assertIn(12345.0, pw_big.prices)

    def test_subscribe_to_cache24_method_directly(self):
        entries = _synthetic_entries(10)
        mgr = _make_cache24_manager("BTCUSDT", entries, self.tmp)
        pw = ta.PriceWindow("BTCUSDT", 20)
        self.assertFalse(pw._subscribed_to_cache24)
        pw.subscribe_to_cache24(mgr)
        self.assertTrue(pw._subscribed_to_cache24)
        mgr.on_price_update("BTCUSDT", int(time.time() * 1000), 88888.0)
        self.assertIn(88888.0, pw.prices)


if __name__ == "__main__":
    unittest.main(verbosity=2)
