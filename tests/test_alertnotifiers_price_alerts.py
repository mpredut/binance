import time
import unittest

from alertnotifiers import AlertNotifier
from pricechecker import PriceAlert, PriceChecker


class DummyCMCPlatform:
    platform_name = "CoinMarketCap"
    _all_listings = {"TAO": {"slug": "tao"}}


class DummyPriceFactory:
    def __init__(self):
        self._platforms = [DummyCMCPlatform()]


class DummyPriceManager:
    def __init__(self, latest_price=100.0, history=None):
        self._latest_price = latest_price
        self._history = history or []
        self.price_factory = DummyPriceFactory()

    def get_latest_price(self, symbol):
        return self._latest_price

    def get_price_history(self, symbol, limit=1000):
        return self._history


class PriceAlertLinkTests(unittest.TestCase):
    def test_price_checker_alert_includes_coinmarketcap_url(self):
        now_ms = int(time.time() * 1000)
        history = [
            {"timestamp": now_ms - 60 * 60 * 1000, "price": 90.0, "timestamp_readable": "2026-05-25T00:00:00"},
            {"timestamp": now_ms - 30 * 60 * 1000, "price": 95.0, "timestamp_readable": "2026-05-25T00:30:00"},
        ]
        checker = PriceChecker(DummyPriceManager(latest_price=100.0, history=history))

        alerts = checker.check_symbol("TAOUSDC")

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].url, "https://coinmarketcap.com/currencies/tao/")

    def test_calculate_24h_stats_returns_min_and_max_readable_timestamps(self):
        now_ms = int(time.time() * 1000)
        history = [
            {"timestamp": now_ms - 3 * 60 * 60 * 1000, "price": 101.0, "timestamp_readable": "2026-05-25T00:00:00"},
            {"timestamp": now_ms - 2 * 60 * 60 * 1000, "price": 99.0, "timestamp_readable": "2026-05-25T01:00:00"},
            {"timestamp": now_ms - 1 * 60 * 60 * 1000, "price": 105.0, "timestamp_readable": "2026-05-25T02:00:00"},
        ]
        checker = PriceChecker(DummyPriceManager(latest_price=110.0, history=history))

        stats = checker._calculate_24h_stats("TAOUSDC")

        self.assertEqual(stats["min_price"], 99.0)
        self.assertEqual(stats["min_price_timestamp_readable"], "2026-05-25T01:00:00")
        self.assertEqual(stats["max_price"], 105.0)
        self.assertEqual(stats["max_price_timestamp_readable"], "2026-05-25T02:00:00")

    def test_alert_notifier_uses_reference_time_in_batch_message(self):
        alert = PriceAlert(
            symbol="TAOUSDC",
            alert_type="up",
            current_price=110.0,
            reference_price=99.0,
            percent_change=11.11,
            threshold=5.0,
            reference_time="2026-05-25 01:00:00",
        )

        message = AlertNotifier.format_batch_message([alert])

        self.assertIn("(at 2026-05-25 01:00:00)", message)

    def test_alert_notifier_includes_coinmarketcap_url_in_batch_message(self):
        alert = PriceAlert(
            symbol="TAOUSDC",
            alert_type="up",
            current_price=100.0,
            reference_price=90.0,
            percent_change=11.11,
            threshold=5.0,
        )
        alert.url = "https://coinmarketcap.com/currencies/tao/"

        message = AlertNotifier.format_batch_message([alert])

        self.assertIn("Link: https://coinmarketcap.com/currencies/tao/", message)


if __name__ == "__main__":
    unittest.main()
