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
    def test_dev_and_live_log_classification(self):
        cases = (
            (True, "logs/backtest_cycle.log"),
            (True, "logs/refresh_dev.log"),
            (True, "logs/trigger_backtest_dev.log"),
            (True, "logs/backtest_pilot.log"),
            (False, "logs/monitortrades.log"),
            (False, "logs/tradeall.log"),
            (False, "kraken/kraken_bot.log"),
            (False, "hyperliquid/dn_bot.log"),
        )
        for expected, path in cases:
            with self.subTest(path=path):
                self.assertEqual(w._is_dev_log(path), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
