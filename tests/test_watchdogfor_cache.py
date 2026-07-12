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
        with patch.object(wd, "_send_ntfy") as ntfy, patch.object(wd, "_send_email") as email:
            self.assertFalse(wd.check_once(now=now))
            ntfy.assert_not_called()
            email.assert_not_called()

    def test_stale_cache_alerts(self):
        now = time.time()
        old = now - 60 * 60   # acum o oră → stale
        _write_cache(self.cache, int(old * 1000), mtime_sec=old)
        with patch.object(wd, "_send_ntfy", return_value=True) as ntfy, \
             patch.object(wd, "_send_email", return_value=True) as email:
            self.assertTrue(wd.check_once(now=now))
            ntfy.assert_called_once()
            email.assert_called_once()

    def test_cooldown_suppresses_second_alert(self):
        now = time.time()
        _write_cache(self.cache, int((now - 3600) * 1000), mtime_sec=now - 3600)
        with patch.object(wd, "_send_ntfy", return_value=True), \
             patch.object(wd, "_send_email", return_value=True):
            self.assertTrue(wd.check_once(now=now))            # prima → alertă
            self.assertFalse(wd.check_once(now=now + 60))      # în cooldown → nu
            self.assertTrue(wd.check_once(now=now + 3700))     # după cooldown → da

    def test_missing_cache_is_stale(self):
        now = time.time()   # fișier inexistent
        with patch.object(wd, "_send_ntfy", return_value=True) as ntfy, \
             patch.object(wd, "_send_email", return_value=True):
            self.assertTrue(wd.check_once(now=now))
            ntfy.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
