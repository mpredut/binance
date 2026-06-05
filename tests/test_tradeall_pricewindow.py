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

    def test_set_sample_rate_no_resize_without_window_seconds(self):
        pw = ta.PriceWindow("BTCUSDT", 50)   # window_seconds=None
        pw.set_sample_rate(2.0)
        self.assertAlmostEqual(pw.sample_rate_sec, 2.0)
        self.assertEqual(pw.window_size, 50)   # neschimbat

    def test_set_sample_rate_resizes_to_target_duration(self):
        # țintă 60s; la rate 1s → ~60 sample, la rate 2s → ~30 sample
        pw = ta.PriceWindow("BTCUSDT", 60, sample_rate_sec=1.0, window_seconds=60.0)
        pw.set_sample_rate(2.0)
        self.assertEqual(pw.window_size, 30)
        self.assertEqual(pw.prices.maxlen, 30)

    def test_set_sample_rate_resize_keeps_recent_prices(self):
        pw = ta.PriceWindow("BTCUSDT", 100, sample_rate_sec=1.0, window_seconds=100.0)
        for p in range(100):
            pw.process_price(float(p))
        pw.set_sample_rate(4.0)   # 100/4 = 25 sample
        self.assertEqual(pw.window_size, 25)
        self.assertEqual(len(pw.prices), 25)
        self.assertIn(99.0, pw.prices)        # cele mai recente păstrate
        self.assertEqual(len(pw.sorted_prices), len(pw.prices))

    def test_set_sample_rate_ignores_invalid(self):
        pw = ta.PriceWindow("BTCUSDT", 50, window_seconds=60.0)
        pw.set_sample_rate(0)
        pw.set_sample_rate(None)
        self.assertEqual(pw.window_size, 50)

    def test_from_cache24_stores_window_seconds(self):
        import tempfile
        tmp = tempfile.mkdtemp()
        mgr = _make_cache24_manager("BTCUSDT", _synthetic_entries(30), tmp)
        pw = ta.PriceWindow.from_cache24("BTCUSDT", 24.0, mgr)
        self.assertAlmostEqual(pw.window_seconds, 24.0)


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
        an = ta.WindowAnalyzer(pw)
        min_p, max_p = an.calculate_proximities(150)
        self.assertAlmostEqual(min_p, 0.5, places=5)
        self.assertAlmostEqual(max_p, 0.5, places=5)

    def test_proximities_at_min(self):
        pw = _window([100, 200])
        an = ta.WindowAnalyzer(pw)
        min_p, max_p = an.calculate_proximities(100)
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
        # Construcția (fișier lipsă → fetch init) poate înregistra un timestamp.
        # Pornim măsurătoarea frecvenței curată pentru a testa mecanismul izolat.
        self.mgr._update_timestamps.clear()

    def test_fallback_no_data(self):
        self.assertAlmostEqual(self.mgr.get_sample_rate("BTCUSDT", fallback=0.8), 0.8)

    def test_frequency_no_data(self):
        self.assertEqual(self.mgr.get_update_frequency("BTCUSDT"), 0.0)

    def test_sample_rate_two_updates(self):
        t0 = time.time()
        self.mgr.on_items_update("BTCUSDT", [60000.0])
        time.sleep(0.15)
        self.mgr.on_items_update("BTCUSDT", [60001.0])
        elapsed = time.time() - t0
        rate = self.mgr.get_sample_rate("BTCUSDT", fallback=9.9)
        # rata măsurată ≈ intervalul real dintre cele 2 update-uri (nu fallback-ul)
        self.assertGreater(rate, 0.0)
        self.assertLess(rate, 9.9)                  # nu e fallback-ul
        self.assertLessEqual(rate, elapsed + 0.5)   # robust la jitter de scheduling

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
# Subscriber pattern moștenit din CacheManagerInterface + atașare WS
# ═══════════════════════════════════════════════════════════════════════════

class _FakeWSManager:
    """Mimează BinanceWebSocketManager: subscribe(sub) + push() → on_items_update."""
    def __init__(self):
        self._subs = []
    def subscribe(self, sub):
        if sub not in self._subs:
            self._subs.append(sub)
    def push(self, symbol, price):
        for s in list(self._subs):
            s.on_items_update(symbol, [price])


class _RecordingSubscriber:
    def __init__(self):
        self.events = []
    def on_price_update(self, symbol, ts_ms, price):
        self.events.append((symbol, price))


class TestSubscriberPatternInheritance(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _cache24(self, symbol="BTCUSDT"):
        return _make_cache24_manager(symbol, _synthetic_entries(5), self.tmp)

    def test_cache24_inherits_subscribe_price(self):
        # subscribe_price nu mai e definit în Cache24PriceManager — vine din base
        self.assertIs(
            type(self._cache24()).subscribe_price,
            cm.CacheManagerInterface.subscribe_price,
        )

    def test_currentprice_inherits_subscribe_price(self):
        self.assertIs(
            cm.CacheCurrentPriceManager.subscribe_price,
            cm.CacheManagerInterface.subscribe_price,
        )

    def test_inherited_notify_reaches_subscriber(self):
        mgr = self._cache24("BTCUSDT")
        rec = _RecordingSubscriber()
        mgr.subscribe_price(rec)
        mgr.on_price_update("BTCUSDT", int(time.time() * 1000), 123.0)
        self.assertIn(("BTCUSDT", 123.0), rec.events)

    def test_attach_ws_manager_wires_chain(self):
        """WS tick → CacheCurrentPrice.on_items_update → subscriber.on_price_update."""
        fname = os.path.join(self.tmp, "cp_ws.json")
        mgr = cm.CacheCurrentPriceManager(
            sync_ts=9999, symbols=["BTCUSDT"],
            filename=fname, ws_manager=None, api_client=mock_bapi,
        )
        ws = _FakeWSManager()
        mgr.attach_ws_manager(ws)

        rec = _RecordingSubscriber()
        mgr.subscribe_price(rec)

        ws.push("BTCUSDT", 67000.0)   # simulează un tick WS
        self.assertIn(("BTCUSDT", 67000.0), rec.events)

    def test_attach_ws_manager_idempotent(self):
        fname = os.path.join(self.tmp, "cp_ws2.json")
        mgr = cm.CacheCurrentPriceManager(
            sync_ts=9999, symbols=["BTCUSDT"],
            filename=fname, ws_manager=None, api_client=mock_bapi,
        )
        ws = _FakeWSManager()
        mgr.attach_ws_manager(ws)
        mgr.attach_ws_manager(ws)
        self.assertEqual(ws._subs.count(mgr), 1)

    def test_ws_tick_marks_ws_healthy(self):
        fname = os.path.join(self.tmp, "cp_ws3.json")
        mgr = cm.CacheCurrentPriceManager(
            sync_ts=9999, symbols=["BTCUSDT"],
            filename=fname, ws_manager=None, api_client=mock_bapi,
        )
        self.assertFalse(mgr._ws_is_healthy())   # niciun event încă
        mgr.on_items_update("BTCUSDT", [50000.0])
        self.assertTrue(mgr._ws_is_healthy())    # WS marcat activ


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


# ═══════════════════════════════════════════════════════════════════════════
# WindowAnalyzer — metrici mutate din PriceWindow
# ═══════════════════════════════════════════════════════════════════════════

class TestWindowAnalyzer(unittest.TestCase):

    def test_pricewindow_has_no_analysis_methods(self):
        # PriceWindow trebuie să fie lean — fără metodele de trading
        pw = _window([100, 110, 105])
        self.assertFalse(hasattr(pw, "calculate_proximities"))
        self.assertFalse(hasattr(pw, "calculate_slope_max_min"))
        self.assertFalse(hasattr(pw, "check_price_change"))
        self.assertFalse(hasattr(pw, "evaluate_buy_sell_opportunity"))

    def test_pricewindow_keeps_range_and_trend(self):
        pw = _window([100, 110, 105])
        self.assertTrue(hasattr(pw, "get_min"))
        self.assertTrue(hasattr(pw, "get_max"))
        self.assertTrue(hasattr(pw, "get_instant_trend"))

    def test_get_trend_alias(self):
        pw = _window([100 + i for i in range(20)])
        self.assertEqual(pw.get_trend(), pw.get_instant_trend())

    def test_get_recent_gradient_uptrend(self):
        pw = _window([100 + i for i in range(20)])
        self.assertGreater(pw.get_recent_gradient(), 0)

    def test_get_recent_gradient_downtrend(self):
        pw = _window([200 - i for i in range(20)])
        self.assertLess(pw.get_recent_gradient(), 0)

    def test_get_recent_gradient_insufficient(self):
        pw = ta.PriceWindow("BTCUSDT", 10)
        pw.process_price(100.0)
        self.assertEqual(pw.get_recent_gradient(), 0.0)

    def test_noise_epsilon_zero_for_constant(self):
        pw = _window([100.0] * 20)
        self.assertAlmostEqual(pw.get_noise_epsilon(), 0.0, places=6)

    def test_noise_epsilon_positive_for_volatile(self):
        import random
        random.seed(1)
        pw = _window([100 + random.uniform(-5, 5) for _ in range(30)])
        self.assertGreater(pw.get_noise_epsilon(), 0.0)

    def test_noise_epsilon_scales_with_volatility(self):
        calm = _window([100 + (i % 2) * 0.1 for i in range(30)])
        wild = _window([100 + (i % 2) * 10 for i in range(30)])
        self.assertLess(calm.get_noise_epsilon(), wild.get_noise_epsilon())

    def test_noise_epsilon_insufficient_data(self):
        pw = ta.PriceWindow("BTCUSDT", 10)
        pw.process_price(100.0)
        pw.process_price(101.0)
        self.assertEqual(pw.get_noise_epsilon(), 0.0)

    def test_slope_max_min_uptrend(self):
        pw = _window([100 + i for i in range(20)])
        an = ta.WindowAnalyzer(pw)
        self.assertGreater(an.calculate_slope_max_min(), 0)

    def test_check_price_change_below_threshold(self):
        pw = _window([100.0, 100.05, 100.02])
        an = ta.WindowAnalyzer(pw)
        slope, pos = an.check_price_change(threshold=5.0)
        self.assertEqual(slope, 0)

    def test_check_price_change_above_threshold(self):
        pw = _window([100.0, 100.0, 110.0])
        an = ta.WindowAnalyzer(pw)
        slope, pos = an.check_price_change(threshold=1.0)
        self.assertNotEqual(slope, 0)

    def test_evaluate_buy_sell_returns_action(self):
        pw = _window([100 + i for i in range(20)])
        an = ta.WindowAnalyzer(pw)
        action, price, pct, slope = an.evaluate_buy_sell_opportunity(120.0)
        self.assertIn(action, ("BUY", "SELL", "HOLD"))

    def test_evaluate_buy_sell_hold_below_threshold(self):
        # variație minusculă → sub threshold_percent → HOLD
        pw = _window([100.0, 100.01, 100.02])
        an = ta.WindowAnalyzer(pw)
        action, price, pct, slope = an.evaluate_buy_sell_opportunity(
            100.02, threshold_percent=5.0)
        self.assertEqual(action, "HOLD")

    def test_calculate_positions_returns_fractions(self):
        pw = _window([100 + i for i in range(10)])
        an = ta.WindowAnalyzer(pw)
        min_pos, max_pos = an.calculate_positions()
        self.assertIsNotNone(min_pos)
        self.assertIsNotNone(max_pos)

    def test_slope_max_min_zero_when_constant(self):
        pw = _window([100.0] * 10)
        an = ta.WindowAnalyzer(pw)
        self.assertEqual(an.calculate_slope_max_min(), 0)

    def test_check_price_change_insufficient_data(self):
        pw = ta.PriceWindow("BTCUSDT", 10)
        pw.process_price(100.0)
        an = ta.WindowAnalyzer(pw)
        slope, pos = an.check_price_change(threshold=1.0)
        self.assertEqual((slope, pos), (0, 1))

    def test_analyze_price_movement_returns_tuple(self):
        # logica complicată restaurată — trebuie să întoarcă (slope, price_diff)
        pw = _window([100 + i for i in range(20)])
        an = ta.WindowAnalyzer(pw)
        result = an._analyze_price_movement(100, 0, 119, 19, 119, 19, 19.0)
        self.assertEqual(len(result), 2)

    def test_analyzer_shares_window_mutation(self):
        # compoziție: analyzer vede modificările ferestrei (același obiect)
        pw = _window([100, 101, 102])
        an = ta.WindowAnalyzer(pw)
        before = pw.get_max()
        pw.process_price(200.0)
        self.assertGreater(pw.get_max(), before)
        self.assertIs(an.window, pw)


# ═══════════════════════════════════════════════════════════════════════════
# TrendCoordinator — event-driven + heartbeat + cache
# ═══════════════════════════════════════════════════════════════════════════

class TestTrendCoordinator(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        entries = _synthetic_entries(60, interval_ms=800)
        base = entries[0][0]
        now_ms = int(time.time() * 1000)
        entries = [[now_ms + (e[0] - base), e[1]] for e in entries]
        self.cache24 = _make_cache24_manager("BTCUSDT", entries, self.tmp)

        fname = os.path.join(self.tmp, "cp.json")
        self.cpm = cm.CacheCurrentPriceManager(
            sync_ts=9999, symbols=["BTCUSDT"],
            filename=fname, ws_manager=None, api_client=mock_bapi,
        )
        self.cpm.on_items_update("BTCUSDT", [60000.0])

        # Managerul deține ferestrele + calc + cache cross-process
        self.mgr = cm.CacheInstantTrendManager(["BTCUSDT"], os.path.join(self.tmp, "trend.json"))
        self.mgr.start_computation({"BTCUSDT": self.cache24}, self.cpm)

    def _make_coord(self):
        return ta.TrendCoordinator(
            symbols=["BTCUSDT"],
            instant_mgr=self.mgr,
            current_price_mgr=self.cpm,
            cache24_managers={"BTCUSDT": self.cache24},
            min_interval=2.0, max_interval=30.0,
        )

    def test_manager_owns_windows(self):
        self.assertIsNotNone(self.mgr.get_window("BTCUSDT"))
        self.assertIsNotNone(self.mgr.get_analyzer("BTCUSDT"))
        self.assertGreater(len(self.mgr.get_window("BTCUSDT").prices), 0)

    def test_dirty_set_on_price_update(self):
        coord = self._make_coord()
        coord._dirty["BTCUSDT"] = False
        coord.on_price_update("BTCUSDT", int(time.time() * 1000), 60001.0)
        self.assertTrue(coord._dirty["BTCUSDT"])
        self.assertTrue(coord._event.is_set())

    def test_is_due_floor(self):
        coord = self._make_coord()
        now = time.time()
        coord._last_eval["BTCUSDT"] = now
        coord._dirty["BTCUSDT"] = True
        self.assertFalse(coord._is_due("BTCUSDT", now + 0.5))
        self.assertTrue(coord._is_due("BTCUSDT", now + 2.5))

    def test_is_due_heartbeat(self):
        coord = self._make_coord()
        now = time.time()
        coord._last_eval["BTCUSDT"] = now
        coord._dirty["BTCUSDT"] = False
        self.assertTrue(coord._is_due("BTCUSDT", now + 31.0))
        self.assertFalse(coord._is_due("BTCUSDT", now + 5.0))

    def test_evaluate_populates_cache(self):
        coord = self._make_coord()
        snap = coord.evaluate("BTCUSDT")
        self.assertIsNotNone(snap)
        cached = coord.get_cached_trend("BTCUSDT")
        self.assertIn("final_trend", cached)
        self.assertIn("slope_full", cached)
        self.assertFalse(coord._dirty["BTCUSDT"])

    def test_evaluate_publishes_to_manager_store(self):
        coord = self._make_coord()
        coord.evaluate("BTCUSDT")
        snap = self.mgr.get_snapshot("BTCUSDT")
        self.assertIsNotNone(snap)
        self.assertIn("slope_big", snap)

    def test_manager_tick_publishes_instant_gradient(self):
        # canalul rapid e în MANAGER: on_price_update publică gradientul
        self.mgr.on_price_update("BTCUSDT", int(time.time() * 1000), 60500.0)
        snap = self.mgr.get_snapshot("BTCUSDT")
        self.assertIsNotNone(snap)
        self.assertIn("gradient_recent", snap)
        self.assertIn("epsilon", snap)
        self.assertEqual(snap["current_price"], 60500.0)

    def test_get_cached_trend_none_before_eval(self):
        coord = self._make_coord()
        self.assertIsNone(coord.get_cached_trend("BTCUSDT"))

    def test_get_all_cached_trends(self):
        coord = self._make_coord()
        self.assertEqual(coord.get_all_cached_trends(), {})
        coord.evaluate("BTCUSDT")
        self.assertIn("BTCUSDT", coord.get_all_cached_trends())

    def test_coordinator_subscribed_to_cache24(self):
        coord = self._make_coord()
        self.assertIn(coord, self.cache24._price_subscribers)

    def test_windows_subscribed_to_cache24(self):
        self.assertTrue(self.mgr.get_window("BTCUSDT")._subscribed_to_cache24)
        self.assertTrue(self.mgr.get_window("BTCUSDT", self.mgr.window_big_sec)._subscribed_to_cache24)

    def test_tick_updates_window_and_marks_dirty(self):
        coord = self._make_coord()
        coord._dirty["BTCUSDT"] = False
        win = self.mgr.get_window("BTCUSDT")
        self.cache24.on_price_update("BTCUSDT", int(time.time() * 1000), 61234.0)
        self.assertTrue(coord._dirty["BTCUSDT"])
        self.assertIn(61234.0, win.prices)

    def test_concurrent_update_and_read_no_crash(self):
        """WS thread actualizează fereastra în timp ce evaluarea citește."""
        import threading as _t
        coord = self._make_coord()
        stop = _t.Event()
        errors = []

        def writer():
            i = 0
            while not stop.is_set():
                try:
                    self.cache24.on_price_update("BTCUSDT", int(time.time() * 1000), 60000.0 + (i % 50))
                except Exception as e:
                    errors.append(("writer", e))
                i += 1

        def reader():
            while not stop.is_set():
                try:
                    coord.evaluate("BTCUSDT")
                    self.mgr.get_instant_trend("BTCUSDT")
                    self.mgr.get_analyzer("BTCUSDT").calculate_slope_max_min()
                except Exception as e:
                    errors.append(("reader", e))

        threads = [
            _t.Thread(target=writer, name="writer"),
            _t.Thread(target=reader, name="reader_1"),
            _t.Thread(target=reader, name="reader_2"),
        ]
        for th in threads:
            th.start()
        time.sleep(1.0)
        stop.set()
        for th in threads:
            th.join(timeout=5)
        self.assertEqual(errors, [], f"Erori de concurență: {errors[:3]}")

    def test_snapshot_has_all_fields(self):
        coord = self._make_coord()
        snap = coord.evaluate("BTCUSDT")
        for key in ("final_trend", "growth_coefficient", "slope_full",
                    "gradient_recent", "slope_small", "slope_big",
                    "slope_max_min", "pos", "current_price", "ts"):
            self.assertIn(key, snap)


if __name__ == "__main__":
    unittest.main(verbosity=2)
