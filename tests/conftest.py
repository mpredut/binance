"""Global safety and cleanup for the test suite."""

import os
import threading

import pytest


# Setat la import, inainte de colectarea modulelor de test. Astfel nici apelurile
# facute la import si nici subprocess-urile pornite de teste nu pot trimite
# notificari reale catre ntfy/email/desktop.
os.environ["DISABLE_EXTERNAL_NOTIFICATIONS"] = "1"


@pytest.fixture(scope="session", autouse=True)
def _shutdown_runtime_threads_after_suite():
    yield
    import cacheManager as cm

    cm.CacheManagerInterface.shutdown_all_instances()
    cm.CachePriceShortTrendManager.shutdown_all_instances()
    cm.CacheFactory.shutdown_all()
    if cm._current_price_instance is not None:
        cm._current_price_instance.shutdown()
    if cm._short_trend_instance is not None:
        cm._short_trend_instance.shutdown()
    if cm._ws_bridge is not None:
        cm._ws_bridge.stop()

    from binance_api import bapi_client, bapi_ws
    assert bapi_client.stop_periodic_resync(), "BinanceTimeResync nu s-a oprit"
    bapi_ws.bapi_ws_manager.stop()

    forbidden = {
        "BinanceTimeResync", "CacheTradeManager", "CacheOrderManager",
        "CacheCurrentPriceManager", "NonBinanceTrendPoller",
        "InstantTrendFullEval", "InstantTrendFlush",
    }
    leaked = sorted(thread.name for thread in threading.enumerate()
                    if thread.name in forbidden)
    assert not leaked, f"thread-uri runtime rămase după suită: {leaked}"
