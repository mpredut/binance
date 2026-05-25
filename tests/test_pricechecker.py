import unittest

from pricechecker import PriceChecker


class DummyPriceManager:
    def __init__(self, latest_price=100.0, history=None):
        self._latest_price = latest_price
        self._history = history or []

    def get_latest_price(self, symbol):
        return self._latest_price

    def get_price_history(self, symbol, limit=1000):
        return self._history

    @property
    def symbols(self):
        return []

    @property
    def original_symbols(self):
        return []


class PriceCheckerTests(unittest.TestCase):
    def test_check_symbol_returns_empty_list_when_history_is_insufficient(self):
        checker = PriceChecker(DummyPriceManager(history=[]))

        alerts = checker.check_symbol("BTCUSDC")

        self.assertEqual(alerts, [])

    def test_dynamic_symbols_use_separate_thresholds(self):
        now_ms = int(__import__("time").time() * 1000)
        history = [
            {"timestamp": now_ms - 2 * 60 * 60 * 1000, "price": 100.0, "timestamp_readable": "2026-05-25T00:00:00"},
            {"timestamp": now_ms - 1 * 60 * 60 * 1000, "price": 105.0, "timestamp_readable": "2026-05-25T01:00:00"},
        ]
        manager = DummyPriceManager(latest_price=105.0, history=history)
        manager.symbol_added_time = {"DYNUSDC": 1.0}

        checker = PriceChecker(manager)

        default_alerts = checker.check_symbol("BTCUSDC")
        dynamic_alerts = checker.check_symbol("DYNUSDC")

        self.assertEqual(len(default_alerts), 1)
        self.assertEqual(default_alerts[0].alert_type, "up")
        self.assertEqual(default_alerts[0].threshold, 4.1)
        self.assertEqual(dynamic_alerts, [])


if __name__ == "__main__":
    unittest.main()
