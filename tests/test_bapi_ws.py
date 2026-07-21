import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from binance_api import bapi_ws
from binance_api.bapi_ws import BinanceWebSocketManager


class BapiWsShutdownHandlingTests(unittest.TestCase):
    def test_shutdown_errors_are_detected(self):
        manager = BinanceWebSocketManager()

        self.assertTrue(
            manager._is_shutdown_error(
                RuntimeError("cannot schedule new futures after shutdown")
            )
        )
        self.assertFalse(manager._is_shutdown_error(RuntimeError("boom")))


class TestUserDataClassify(unittest.TestCase):
    """_classify decide între ping / răspuns comandă / eveniment real."""
    C = staticmethod(bapi_ws.BinanceUserDataStream._classify)

    def test_ping_response(self):
        self.assertEqual(self.C({"id": "ping", "status": 200})[0], "ping")

    def test_command_response(self):
        self.assertEqual(self.C({"id": "sub", "status": 200})[0], "response")

    def test_wrapped_event_unwrapped(self):
        inner = {"e": "executionReport", "s": "BTCUSDT", "i": 42}
        kind, payload = self.C({"event": inner})
        self.assertEqual(kind, "event")
        self.assertEqual(payload, inner)

    def test_bare_event(self):
        ev = {"e": "executionReport", "s": "BTCUSDT"}
        kind, payload = self.C(ev)
        self.assertEqual(kind, "event")
        self.assertEqual(payload, ev)

    def test_ping_not_routed_to_handler(self):
        self.assertNotEqual(self.C({"id": "ping", "status": 200})[0], "event")


class TestWSBaseAndUserData(unittest.TestCase):
    def test_market_manager_no_autostart_on_import(self):
        # importul NU pornește socket-ul (side-effect eliminat)
        self.assertFalse(bapi_ws.bapi_ws_manager.is_running)

    def test_userdata_callbacks_default_noop(self):
        s = bapi_ws.BinanceUserDataStream(on_event=lambda payload: None)
        s.on_available(True); s.on_healthy(); s.on_unhealthy()   # nu trebuie să arunce

    def test_userdata_health_marks_call_callbacks(self):
        seen = {}
        s = bapi_ws.BinanceUserDataStream(
            on_event=lambda p: None,
            on_available=lambda v: seen.__setitem__("avail", v),
            on_healthy=lambda: seen.__setitem__("healthy", True),
            on_unhealthy=lambda: seen.__setitem__("unhealthy", True))
        s._mark_available(True); s._mark_event(); s._mark_unhealthy()
        self.assertEqual(seen.get("avail"), True)
        self.assertTrue(seen.get("healthy"))
        self.assertTrue(seen.get("unhealthy"))


if __name__ == "__main__":
    unittest.main()
