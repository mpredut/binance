import os
import unittest

os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

from bapi_ws import BinanceWebSocketManager


class BapiWsShutdownHandlingTests(unittest.TestCase):
    def test_shutdown_errors_are_detected(self):
        manager = BinanceWebSocketManager()

        self.assertTrue(
            manager._is_shutdown_error(
                RuntimeError("cannot schedule new futures after shutdown")
            )
        )
        self.assertFalse(manager._is_shutdown_error(RuntimeError("boom")))


if __name__ == "__main__":
    unittest.main()
