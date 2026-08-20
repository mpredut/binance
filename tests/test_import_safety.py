"""Caracterizare: importurile runtime nu pornesc rețea/cache-uri în fundal."""

import os
import subprocess
import sys
import textwrap
import unittest


class RuntimeImportSafetyTest(unittest.TestCase):
    def test_provider_layer_imports_without_binance_key_module(self):
        code = textwrap.dedent(
            """
            import sys
            import types
            import importlib

            # Simuleaza checkout-ul CI: pachetul keys poate exista, dar fisierul
            # secret keys/apikeys.py nu este versionat.
            keys = types.ModuleType("keys")
            keys.__path__ = []
            sys.modules["keys"] = keys
            sys.modules.pop("keys.apikeys", None)

            from providers.t212_provider import T212Provider
            from providers import market_api
            importlib.import_module("212trading.t212_client")

            if market_api._bapi is not None:
                raise AssertionError("Binance bapi a fost importat eager")
            T212Provider()  # constructie lazy, fara chei T212/Binance
            if market_api.api.provider_by_name("t212") is None:
                raise AssertionError("T212 lipseste din registry dupa import direct")

            from binance_api import bapi_ws
            if bapi_ws.api_key_ws:
                raise AssertionError("testul a incarcat o cheie privata")
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=dict(os.environ, BINANCE_AUTO_START_WEBSOCKETS="0", BINANCE_API_KEY_WS=""),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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
