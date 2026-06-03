"""
Teste pentru cacheManager.CacheInstantTrendManager:
  - store cross-process (file-backed) + merge snapshot
  - gate oportunist (is_favorable_to_wait / wait_for_favorable_entry) + epsilon
  - calc API (windows, get_instant_trend) + canal rapid on_price_update
"""
import os, sys, json, time, tempfile, unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

mock_api = MagicMock()
mock_api.get_current_price = MagicMock(return_value=60000.0)
mock_api.client = MagicMock()
sys.modules.setdefault("bapi", mock_api)
sys.modules.setdefault("bapi_trades", MagicMock())
sys.modules.setdefault("bapi_allorders", MagicMock())

with patch("cacheManager._initialize_once", return_value=None):
    import cacheManager as cm


def _make_cache24(symbol, entries, tmp):
    fname = os.path.join(tmp, f"c24_{symbol}.json")
    with open(fname, "w") as f:
        json.dump({"items": {symbol: entries}, "fetchtime": {}}, f)
    return cm.Cache24PriceManager(sync_ts=9999, symbols=[symbol], filename=fname, api_client=mock_api)


def _entries_now(n=60, interval_ms=800, start=60000.0, delta=10.0):
    now = int(time.time() * 1000)
    start_ts = now - n * interval_ms
    return [[start_ts + i * interval_ms, start + i * delta] for i in range(n)]


def _mgr(tmp, name="trend.json"):
    return cm.CacheInstantTrendManager(["BTCUSDT"], os.path.join(tmp, name))


# ═══════════════════════════════════════════════════════════════════════════
# Store + merge
# ═══════════════════════════════════════════════════════════════════════════
class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.m = _mgr(self.tmp)

    def test_update_and_get(self):
        self.m.update_snapshot("BTCUSDT", gradient_recent=-0.5, current_price=60000.0)
        snap = self.m.get_snapshot("BTCUSDT")
        self.assertEqual(snap["gradient_recent"], -0.5)
        self.assertEqual(snap["symbol"], "BTCUSDT")

    def test_merge_preserves_fields(self):
        self.m.update_snapshot("BTCUSDT", slope_big=5.0, gradient_recent=0.1)
        self.m.update_snapshot("BTCUSDT", gradient_recent=-0.9)
        snap = self.m.get_snapshot("BTCUSDT")
        self.assertEqual(snap["gradient_recent"], -0.9)
        self.assertEqual(snap["slope_big"], 5.0)

    def test_get_unknown_none(self):
        self.assertIsNone(self.m.get_snapshot("NOPE"))

    def test_non_writer_does_not_write_file(self):
        fname = os.path.join(self.tmp, "nw.json")
        m = cm.CacheInstantTrendManager(["BTCUSDT"], fname, writer=False)
        m.update_snapshot("BTCUSDT", gradient_recent=0.5)
        self.assertFalse(os.path.exists(fname))     # non-writer nu scrie
        self.assertIsNotNone(m.get_snapshot("BTCUSDT"))  # dar are in memorie

    def test_writer_writes_file(self):
        fname = os.path.join(self.tmp, "w.json")
        m = cm.CacheInstantTrendManager(["BTCUSDT"], fname, writer=True)
        m.update_snapshot("BTCUSDT", gradient_recent=0.5)
        self.assertTrue(os.path.exists(fname))      # writer scrie

    def test_cross_process_reader_sees_writer(self):
        fname = os.path.join(self.tmp, "shared.json")
        writer = cm.CacheInstantTrendManager(["BTCUSDT"], fname, writer=True)
        reader = cm.CacheInstantTrendManager(["BTCUSDT"], fname)
        writer.update_snapshot("BTCUSDT", gradient_recent=-0.7, current_price=60000.0)
        snap = reader.get_snapshot("BTCUSDT")
        self.assertIsNotNone(snap)
        self.assertEqual(snap["gradient_recent"], -0.7)

    def test_cross_process_rapid_updates(self):
        # două update-uri în aceeași secundă — reader le vede pe ambele (mtime_ns)
        fname = os.path.join(self.tmp, "rapid.json")
        writer = cm.CacheInstantTrendManager(["BTCUSDT"], fname, writer=True)
        reader = cm.CacheInstantTrendManager(["BTCUSDT"], fname)
        writer.update_snapshot("BTCUSDT", gradient_recent=0.1, current_price=60000.0)
        reader.get_snapshot("BTCUSDT")
        writer.update_snapshot("BTCUSDT", gradient_recent=-0.9)
        self.assertEqual(reader.get_snapshot("BTCUSDT")["gradient_recent"], -0.9)


# ═══════════════════════════════════════════════════════════════════════════
# Gate + epsilon
# ═══════════════════════════════════════════════════════════════════════════
class TestGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.m = _mgr(self.tmp)

    def _pub(self, **f):
        f.setdefault("ts", time.time())
        f.setdefault("current_price", 0.0)   # eps=0 → testăm direcția pură
        self.m.update_snapshot("BTCUSDT", **f)

    def test_buy_waits_falling(self):
        self._pub(gradient_recent=-0.5)
        self.assertTrue(self.m.is_favorable_to_wait("BUY", "BTCUSDT"))

    def test_buy_places_rising(self):
        self._pub(gradient_recent=0.5)
        self.assertFalse(self.m.is_favorable_to_wait("BUY", "BTCUSDT"))

    def test_sell_waits_rising(self):
        self._pub(gradient_recent=0.5)
        self.assertTrue(self.m.is_favorable_to_wait("SELL", "BTCUSDT"))

    def test_no_snapshot(self):
        self.assertFalse(self.m.is_favorable_to_wait("BUY", "BTCUSDT"))

    def test_stale(self):
        self._pub(gradient_recent=-0.5, ts=time.time() - cm.CacheInstantTrendManager.TREND_STALE_SEC - 5)
        self.assertFalse(self.m.is_favorable_to_wait("BUY", "BTCUSDT"))

    def test_noise_waits_for_clarity(self):
        self._pub(gradient_recent=0.4, epsilon=1.0)   # sub epsilon → zgomot
        self.assertTrue(self.m.is_favorable_to_wait("BUY", "BTCUSDT"))

    def test_informed_epsilon_clear_up_places(self):
        self._pub(gradient_recent=5.0, epsilon=1.0)   # peste epsilon, urcă clar
        self.assertFalse(self.m.is_favorable_to_wait("BUY", "BTCUSDT"))

    def test_wait_returns_immediately_when_unfavorable(self):
        self._pub(gradient_recent=0.5)   # urcă → BUY nu așteaptă
        calls = []
        waited = self.m.wait_for_favorable_entry("BUY", "BTCUSDT", max_wait_sec=10,
                                                 sleep_fn=lambda s: calls.append(s))
        self.assertEqual(waited, 0.0)
        self.assertEqual(calls, [])

    def test_wait_stops_when_flips(self):
        self._pub(gradient_recent=-0.5, epsilon=0.0)
        st = {"n": 0}
        def fake_sleep(_):
            st["n"] += 1
            if st["n"] >= 2:
                self._pub(gradient_recent=0.5, epsilon=0.0)
        waited = self.m.wait_for_favorable_entry("BUY", "BTCUSDT", max_wait_sec=60,
                                                 poll_sec=1.0, sleep_fn=fake_sleep)
        self.assertGreaterEqual(waited, 2.0)
        self.assertLess(waited, 60.0)


# ═══════════════════════════════════════════════════════════════════════════
# Calc API + canal rapid (start_computation / on_price_update)
# ═══════════════════════════════════════════════════════════════════════════
class TestComputation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache24 = _make_cache24("BTCUSDT", _entries_now(60), self.tmp)
        fname = os.path.join(self.tmp, "cp.json")
        self.cpm = cm.CacheCurrentPriceManager(sync_ts=9999, symbols=["BTCUSDT"],
                                               filename=fname, ws_manager=None, api_client=mock_api)
        self.m = _mgr(self.tmp)
        self.m.start_computation({"BTCUSDT": self.cache24}, self.cpm)

    def test_windows_built(self):
        self.assertIsNotNone(self.m.get_window("BTCUSDT"))
        self.assertIsNotNone(self.m.get_window_big("BTCUSDT"))
        self.assertIsNotNone(self.m.get_analyzer("BTCUSDT"))

    def test_get_instant_trend(self):
        ft, gc, sf, gr = self.m.get_instant_trend("BTCUSDT")
        self.assertIn(ft, (-1, 0, 1))

    def test_on_price_update_publishes_fast(self):
        self.m.on_price_update("BTCUSDT", int(time.time() * 1000), 60500.0)
        snap = self.m.get_snapshot("BTCUSDT")
        self.assertIsNotNone(snap)
        self.assertIn("gradient_recent", snap)
        self.assertIn("epsilon", snap)
        self.assertEqual(snap["current_price"], 60500.0)

    def test_cache24_tick_updates_window_and_snapshot(self):
        win = self.m.get_window("BTCUSDT")
        n_before = len(win.prices)
        self.cache24.on_price_update("BTCUSDT", int(time.time() * 1000), 61234.0)
        self.assertIn(61234.0, win.prices)
        self.assertGreaterEqual(len(win.prices), n_before)
        self.assertIsNotNone(self.m.get_snapshot("BTCUSDT"))

    def test_start_computation_idempotent(self):
        w1 = self.m.get_window("BTCUSDT")
        self.m.start_computation({"BTCUSDT": self.cache24}, self.cpm)
        self.assertIs(self.m.get_window("BTCUSDT"), w1)

    def test_evaluate_full_writes_complete_snapshot(self):
        # calculul complet (fără logică de trading) → snapshot cu toate metricile
        self.m.evaluate_full("BTCUSDT")
        snap = self.m.get_snapshot("BTCUSDT")
        self.assertIsNotNone(snap)
        for key in ("final_trend", "slope_full", "gradient_recent",
                    "slope_small", "slope_big", "slope_max_min", "pos", "epsilon"):
            self.assertIn(key, snap)

    def test_full_eval_loop_thread_started(self):
        m2 = cm.CacheInstantTrendManager(["BTCUSDT"], os.path.join(self.tmp, "t2.json"))
        m2.start_computation({"BTCUSDT": self.cache24}, self.cpm, run_full_eval=True)
        self.assertIsNotNone(m2._full_eval_thread)
        self.assertTrue(m2._full_eval_thread.is_alive())


if __name__ == "__main__":
    unittest.main(verbosity=2)
