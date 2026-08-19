"""Caracterizare: importurile runtime nu pornesc rețea/cache-uri în fundal."""

import os
import subprocess
import sys
import textwrap
import unittest


class RuntimeImportSafetyTest(unittest.TestCase):
    def test_cache_manager_import_is_network_and_worker_free(self):
        code = textwrap.dedent(
            """
            import threading
            import binance.client

            class ForbiddenClient:
                def __init__(self, *args, **kwargs):
                    raise AssertionError("Binance Client construit la import")

            binance.client.Client = ForbiddenClient
            import cacheManager
            from binance_api import bapi_trades

            forbidden = {
                "BinanceTimeResync", "CacheTradeManager", "CacheOrderManager",
                "CacheCurrentPriceManager", "NonBinanceTrendPoller",
                "InstantTrendFullEval", "InstantTrendFlush",
            }
            active = {thread.name for thread in threading.enumerate()}
            leaked = sorted(active & forbidden)
            if leaked:
                raise AssertionError(f"thread-uri pornite la import: {leaked}")
            """
        )
        env = dict(os.environ, BINANCE_AUTO_START_WEBSOCKETS="0")
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
