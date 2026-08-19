"""Session-level cleanup for background services created by integration tests."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _shutdown_runtime_threads_after_suite():
    yield
    import cacheManager as cm

    cm.CacheFactory.shutdown_all()
    if cm._current_price_instance is not None:
        cm._current_price_instance.shutdown()
    if cm._short_trend_instance is not None:
        cm._short_trend_instance.shutdown()
    if cm._ws_bridge is not None:
        cm._ws_bridge.stop()

    from binance_api import bapi_ws
    bapi_ws.bapi_ws_manager.stop()
