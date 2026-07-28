import os, sys, json, time, tempfile, unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "verify_tools"))
import watchdogfor_cache as wd


def _write_cache(path, fetchtime_ms, mtime_sec=None):
    json.dump({"items": {"HYPE": [[fetchtime_ms, 65.0]]},
               "fetchtime": {"HYPE": fetchtime_ms}}, open(path, "w"))
    if mtime_sec is not None:
        os.utime(path, (mtime_sec, mtime_sec))


class TestWatchdog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cache = os.path.join(self.tmp, "cache_prices_multi.json")
        wd._CACHE_DIR = Path(self.tmp)
        wd.STATE_FILE = os.path.join(self.tmp, ".state.json")
        wd.STALE_MINUTES = 20
        wd.COOLDOWN_MINUTES = 60

    def test_normalize_ts(self):
        self.assertAlmostEqual(wd._normalize_ts_seconds(1779829664000), 1779829664.0)  # ms
        self.assertAlmostEqual(wd._normalize_ts_seconds(1779829664), 1779829664.0)      # sec
        self.assertEqual(wd._normalize_ts_seconds(0), 0.0)

    def test_fresh_cache_no_alert(self):
        now = time.time()
        _write_cache(self.cache, int(now * 1000))   # proaspăt
        with patch.object(wd.wc, "send_ntfy") as ntfy, patch.object(wd.wc, "send_email") as email:
            self.assertFalse(wd.check_once(now=now))
            ntfy.assert_not_called()
            email.assert_not_called()

    def test_stale_cache_alerts(self):
        now = time.time()
        old = now - 60 * 60   # acum o oră → stale
        _write_cache(self.cache, int(old * 1000), mtime_sec=old)
        with patch.object(wd.wc, "send_ntfy", return_value=True) as ntfy, \
             patch.object(wd.wc, "send_email", return_value=True) as email:
            self.assertTrue(wd.check_once(now=now))
            ntfy.assert_called_once()
            email.assert_called_once()

    def test_cooldown_suppresses_second_alert(self):
        now = time.time()
        _write_cache(self.cache, int((now - 3600) * 1000), mtime_sec=now - 3600)
        with patch.object(wd.wc, "send_ntfy", return_value=True), \
             patch.object(wd.wc, "send_email", return_value=True):
            self.assertTrue(wd.check_once(now=now))            # prima → alertă
            self.assertFalse(wd.check_once(now=now + 60))      # în cooldown → nu
            self.assertTrue(wd.check_once(now=now + 3700))     # după cooldown → da

    def test_missing_cache_is_stale(self):
        now = time.time()   # fișier inexistent
        with patch.object(wd.wc, "send_ntfy", return_value=True) as ntfy, \
             patch.object(wd.wc, "send_email", return_value=True):
            self.assertTrue(wd.check_once(now=now))
            ntfy.assert_called_once()


class TestEventDrivenGating(unittest.TestCase):
    """Gating flota-vie pt cache-urile event-driven (order/trade) — 28 iul.
    Un cache de fill stale NU trebuie sa alarmeze cat timp flota e demonstrabil
    vie (un cache de pret rapid e proaspat)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        wd._CACHE_DIR = Path(self.tmp)
        wd.STATE_FILE = os.path.join(self.tmp, ".state.json")
        wd.STALE_MINUTES = 20
        wd.COOLDOWN_MINUTES = 60
        self.price = os.path.join(self.tmp, "cache_prices_multi.json")   # fleet-alive
        self.order = os.path.join(self.tmp, "cache_order.json")          # event-driven

    def test_fill_cache_stale_but_fleet_alive_no_alarm(self):
        now = time.time()
        _write_cache(self.price, int(now * 1000))                        # pret PROASPAT -> flota vie
        _write_cache(self.order, int((now - 90 * 3600) * 1000),          # fill vechi de 90h (>72h prag)
                     mtime_sec=now - 90 * 3600)
        with patch.object(wd.wc, "send_ntfy") as ntfy, patch.object(wd.wc, "send_email") as email:
            self.assertFalse(wd.check_once(now=now), "flota vie -> fill stale benign, fara alarma")
            ntfy.assert_not_called()
            email.assert_not_called()

    def test_fill_cache_stale_and_fleet_dead_alarms(self):
        now = time.time()
        # AMBELE stale: pret vechi (flota moarta) + fill vechi -> alarma (fail-safe)
        _write_cache(self.price, int((now - 3600) * 1000), mtime_sec=now - 3600)
        _write_cache(self.order, int((now - 90 * 3600) * 1000), mtime_sec=now - 90 * 3600)
        with patch.object(wd.wc, "send_ntfy", return_value=True) as ntfy, \
             patch.object(wd.wc, "send_email", return_value=True):
            self.assertTrue(wd.check_once(now=now), "flota moarta -> alarma trece")
            ntfy.assert_called_once()

    def test_fill_cache_beyond_hard_ceiling_alarms_even_if_fleet_alive(self):
        now = time.time()
        _write_cache(self.price, int(now * 1000))                        # flota vie
        # fill mai vechi decat plafonul dur (30 zile) -> alarma oricum
        old = now - (wd._EVENT_DRIVEN_HARD_CEILING_MIN + 60) * 60
        _write_cache(self.order, int(old * 1000), mtime_sec=old)
        with patch.object(wd.wc, "send_ntfy", return_value=True) as ntfy, \
             patch.object(wd.wc, "send_email", return_value=True):
            self.assertTrue(wd.check_once(now=now), "peste plafonul dur -> alarma chiar cu flota vie")
            ntfy.assert_called_once()


class TestFastPriceThreshold(unittest.TestCase):
    """Prag dedicat strans (5min) + eligibilitate restart DOAR pt cache-urile
    de pret rapide (~1s). Arhiva sparse .jsonl ramane pe pragul general si NU
    declanseaza restart (28 iul)."""

    def test_fast_price_caches_classified(self):
        for n in ("cache_currentprice.json", "cache_prices_multi.json",
                  "cache_instant_trend.json", "cache_24price_HYPEUSD.json",
                  "cache_24price_BTCUSDC.json"):
            self.assertTrue(wd._is_fast_price_cache(n), n)
            self.assertEqual(wd._threshold_for(n), wd._FAST_PRICE_THRESHOLD_MIN, n)

    def test_sparse_and_slow_not_fast(self):
        # .jsonl sparse / arhivator + slow/event-driven NU sunt fast (nu restart)
        for n in ("cache_price_BTCUSDC.jsonl", "cache_24price_long_BTCUSDC.jsonl",
                  "cache_price_long_trend.json", "cache_order.json", "cache_asset_value.json"):
            self.assertFalse(wd._is_fast_price_cache(n), n)

    def test_sparse_archive_stall_does_not_restart(self):
        """Un cache sparse .jsonl stale (nu fast) -> alarma, dar NU restart."""
        import tempfile
        tmp = tempfile.mkdtemp()
        wd._CACHE_DIR = Path(tmp)
        wd.STATE_FILE = os.path.join(tmp, ".state.json")
        wd.AUTO_RESTART = True
        now = time.time()
        jf = os.path.join(tmp, "cache_price_BTCUSDC.jsonl")
        with open(jf, "w") as f:
            f.write(json.dumps({"s": "BTCUSDC", "i": [int((now - 3600) * 1000), 65000.0]}) + "\n")
        os.utime(jf, (now - 3600, now - 3600))
        with patch.object(wd, "_do_restart", return_value=True) as restart, \
             patch.object(wd.wc, "send_ntfy", return_value=True), \
             patch.object(wd.wc, "send_email", return_value=True):
            wd.check_once(now=now)
            restart.assert_not_called()


class TestAutoRestart(unittest.TestCase):
    """Auto-restart pe stall REAL de pret (28 iul). _do_restart e mock-uit —
    NU se atinge niciun proces real in teste."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        wd._CACHE_DIR = Path(self.tmp)
        wd.STATE_FILE = os.path.join(self.tmp, ".state.json")
        wd.STALE_MINUTES = 20
        wd.COOLDOWN_MINUTES = 60
        wd.AUTO_RESTART = True
        wd.AUTO_RESTART_COOLDOWN_MIN = 15
        wd.AUTO_RESTART_MAX = 3
        wd.AUTO_RESTART_WINDOW_H = 6
        self.price = os.path.join(self.tmp, "cache_prices_multi.json")   # cache RAPID
        self.order = os.path.join(self.tmp, "cache_order.json")          # event-driven (in overrides)

    def _stale_price(self, now):
        _write_cache(self.price, int((now - 3600) * 1000), mtime_sec=now - 3600)  # 1h stale

    def test_fast_cache_stall_triggers_restart(self):
        now = time.time()
        self._stale_price(now)
        with patch.object(wd, "_do_restart", return_value=True) as restart, \
             patch.object(wd.wc, "send_ntfy", return_value=True), \
             patch.object(wd.wc, "send_email", return_value=True):
            self.assertTrue(wd.check_once(now=now))
            restart.assert_called_once()

    def test_disabled_flag_no_restart(self):
        now = time.time()
        wd.AUTO_RESTART = False
        self._stale_price(now)
        with patch.object(wd, "_do_restart", return_value=True) as restart, \
             patch.object(wd.wc, "send_ntfy", return_value=True), \
             patch.object(wd.wc, "send_email", return_value=True):
            wd.check_once(now=now)
            restart.assert_not_called()

    def test_slow_cache_stall_does_not_restart(self):
        """Un cache din _STALE_OVERRIDES (event-driven/slow) stale, dar FLOTA MOARTA
        (niciun pret proaspat) -> alarma, dar NU restart (staleness de fill nu
        justifica repornirea procesului critic)."""
        now = time.time()
        _write_cache(self.order, int((now - 100 * 3600) * 1000), mtime_sec=now - 100 * 3600)
        with patch.object(wd, "_do_restart", return_value=True) as restart, \
             patch.object(wd.wc, "send_ntfy", return_value=True), \
             patch.object(wd.wc, "send_email", return_value=True):
            wd.check_once(now=now)
            restart.assert_not_called()

    def test_cooldown_blocks_second_restart(self):
        now = time.time()
        self._stale_price(now)
        with patch.object(wd, "_do_restart", return_value=True) as restart, \
             patch.object(wd.wc, "send_ntfy", return_value=True), \
             patch.object(wd.wc, "send_email", return_value=True):
            wd.check_once(now=now)                       # restart 1
            wd.check_once(now=now + 5 * 60)              # +5min < cooldown 15min -> NU
            self.assertEqual(restart.call_count, 1, "cooldown trebuie sa blocheze al 2-lea restart")
            wd.check_once(now=now + 20 * 60)             # +20min > cooldown -> restart 2
            self.assertEqual(restart.call_count, 2)

    def test_max_per_window_then_escalates(self):
        now = time.time()
        self._stale_price(now)
        with patch.object(wd, "_do_restart", return_value=True) as restart, \
             patch.object(wd.wc, "send_ntfy", return_value=True), \
             patch.object(wd.wc, "send_email", return_value=True):
            wd.check_once(now=now)                   # 1
            wd.check_once(now=now + 16 * 60)         # 2 (peste cooldown)
            wd.check_once(now=now + 32 * 60)         # 3
            wd.check_once(now=now + 48 * 60)         # al 4-lea: PLAFON -> NU repornim
            self.assertEqual(restart.call_count, 3, "plafonul de 3/fereastra trebuie respectat")


if __name__ == "__main__":
    unittest.main(verbosity=2)
