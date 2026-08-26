import unittest
import time

from providers.base import MarketDataProvider
from providers.market_api import MarketApi
from providers.strategy_executor import (
    OrderReconciliationCapabilities,
    OrderStatus,
    ProviderError,
)


class FakeProvider(MarketDataProvider):
    name = "Fake"

    def supports_symbol(self, symbol):
        return symbol == "ABCUSD"

    def get_current_price(self, symbol):
        return 10.0

    def reconciliation_capabilities(self):
        return OrderReconciliationCapabilities(
            status_by_order_id=True,
            cancel_by_order_id=True,
        )

    def order_status(self, symbol, order_id):
        return OrderStatus("closed", 2.0, 20.0, 0.02)

    def cancel_order(self, symbol, order_id):
        self.canceled = (symbol, order_id)

    def get_trades(self, symbol, since_s):
        return [
            {"side": "BUY", "price": 9, "qty": 2, "timestamp": 1},
            {"side": "SELL", "price": 11, "qty": 2,
             "timestamp": time.time() * 1000},
        ]

    def ohlc_closes(self, symbol, interval_min):
        return list(range(10, 50))


class MarketApiLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.provider = FakeProvider()
        self.api = MarketApi([self.provider])

    def test_status_properties_are_provider_neutral(self):
        status = self.api.order_status("ABCUSD", "7")
        self.assertTrue(status.terminal)
        self.assertTrue(status.fully_filled)
        self.assertTrue(status.has_fill)
        self.assertFalse(status.partially_filled)

        partial_cancel = OrderStatus("canceled", 0.5, 5.0, 0.01)
        self.assertTrue(partial_cancel.terminal)
        self.assertFalse(partial_cancel.fully_filled)
        self.assertTrue(partial_cancel.partially_filled)

        normalized = OrderStatus("open", "0.5", "5", "0.01")
        self.assertEqual(normalized.filled_qty, 0.5)
        self.assertTrue(normalized.has_fill)
        rebate = OrderStatus("closed", 1, 10, -0.01)
        self.assertEqual(rebate.fee, -0.01)

    def test_cancel_and_latest_fill_route_through_common_facade(self):
        self.api.cancel_order("ABCUSD", "8")
        self.assertEqual(self.provider.canceled, ("ABCUSD", "8"))
        self.assertEqual(
            self.api.latest_fill_price("ABCUSD", "SELL", 60), 11.0)

    def test_unsupported_reconciliation_operations_fail_closed(self):
        capabilities = self.api.reconciliation_capabilities("ABCUSD")
        self.assertTrue(capabilities.status_by_order_id)
        self.assertTrue(capabilities.cancel_by_order_id)
        self.assertFalse(capabilities.lookup_by_client_order_id)
        self.assertFalse(capabilities.list_open_orders)

        with self.assertRaisesRegex(ProviderError, "order_by_client_id is unsupported"):
            self.api.order_by_client_id("ABCUSD", "CID-1")
        with self.assertRaisesRegex(ProviderError, "open_orders is unsupported"):
            self.api.open_orders("ABCUSD")

    def test_tracked_lifecycle_factory_keeps_place_synchronous_contract_separate(self):
        lifecycle = self.api.tracked_order_lifecycle(
            provider_name="Fake", max_age_seconds=60,
            retry_on_lookup_error=True)
        self.assertIs(lifecycle.market_api, self.api)
        self.assertEqual(lifecycle.provider_name, "Fake")
        self.assertEqual(lifecycle.max_age_seconds, 60)
        self.assertTrue(lifecycle.retry_on_lookup_error)

    def test_market_regime_routes_to_provider_ohlc(self):
        decision = self.api.market_regime(
            "ABCUSD", interval_min=1, window_seconds=300)
        self.assertEqual(decision.regime, "bull")
        self.assertEqual(decision.horizon, "short")
        self.assertEqual(decision.source, "ohlc:1m")

    def test_composite_market_regime_supports_multiple_crypto_benchmarks(self):
        decision = self.api.composite_market_regime(
            "ABCUSD", benchmarks=("BTCUSD", "ETHUSD"))
        self.assertTrue(decision.actionable)
        self.assertEqual(decision.regime, "bull")
        self.assertEqual(len(decision.components), 6)

    def test_latest_fill_rejects_nonfinite_inputs(self):
        with self.assertRaises(ValueError):
            self.api.latest_fill_price("ABCUSD", "BUY", float("nan"))
        with self.assertRaises(ValueError):
            self.api.latest_fill_price(
                "ABCUSD", "BUY", 60, min_notional=20, max_notional=10)

    def test_invalid_provider_status_is_normalized_to_provider_error(self):
        self.provider.order_status = lambda _symbol, _order_id: OrderStatus(
            "venue-specific", 0, 0, 0)
        with self.assertRaises(ProviderError):
            self.api.order_status("ABCUSD", "9")


if __name__ == "__main__":
    unittest.main()
