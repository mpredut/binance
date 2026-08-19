"""watchdogfor_anomaly: logurile DEV/backtest (backtest_cycle.log, refresh_dev.log,
backtest*.log) sunt excluse din scanare — tracebacks de acolo sunt de pe masina de
test (pilot backtest pe runner.py), NU probleme de flota LIVE. Fara excludere,
anomaly-watchdog alerta fals 'Verifica botii afectati' pentru esecuri de pe dev."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "verify_tools"))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import watchdogfor_anomaly as w


class DevLogExclusionTest(unittest.TestCase):
    def test_dev_logs_excluded(self):
        for p in ("logs/backtest_cycle.log", "logs/refresh_dev.log",
                  "logs/trigger_backtest_dev.log", "logs/backtest_pilot.log"):
            self.assertTrue(w._is_dev_log(p), p)

    def test_live_logs_kept(self):
        for p in ("logs/monitortrades.log", "logs/tradeall.log",
                  "kraken/kraken_bot.log", "hyperliquid/dn_bot.log"):
            self.assertFalse(w._is_dev_log(p), p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
