"""Kraken executor-contract tests with an injected offline fake client."""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import instrument as instrument_module  # noqa: E402
import order_guard  # noqa: E402
from instrument import Instrument  # noqa: E402
from providers import kraken_provider  # noqa: E402
from providers.kraken_provider import (  # noqa: E402
    KrakenProvider,
    kraken_strategy_dry_run,
)
from providers.market_api import MarketApi  # noqa: E402
from providers.strategy_executor import (  # noqa: E402
    StrategyExecutor, OrderStatus, PairPrecision, ProviderError)


class FakeClient:
    def __init__(self):
        self.calls = []

    def add_order(self, pair, side, volume, price=None, ordertype="limit", validate=False,
                  cl_ord_id=None):
        self.calls.append((
            "add_order", pair, side, volume, price, ordertype, validate, cl_ord_id,
        ))
        return {"txid": ["OABC-123"], "descr": {}}

    def query_orders(self, txids):
        return {txids: {"status": "closed", "vol_exec": "2.5", "cost": "150.0", "fee": "0.39"}}

    def open_orders(self):
        return {
            "OPEN-1": {
                "cl_ord_id": "0123456789abcdef0123456789abcdef",
                "status": "open", "vol": "2.5", "vol_exec": "0.5",
                "descr": {
                    "pair": "HYPEUSD", "type": "buy", "price": "60.25",
                },
            },
            "OTHER-1": {
                "cl_ord_id": "11111111111111111111111111111111",
                "status": "open", "vol": "1", "vol_exec": "0",
                "descr": {
                    "pair": "ADAUSD", "type": "sell", "price": "1.25",
                },
            },
        }

    def closed_orders(self):
        return {
            "CLOSED-1": {
                "cl_ord_id": "fedcba9876543210fedcba9876543210",
                "status": "closed", "descr": {"pair": "HYPEUSD"},
            },
        }

    def cancel_order(self, txid):
        self.calls.append(("cancel_order", txid))
        return {"count": 1}

    def pair_info(self, pair):
        return {
            "pair_decimals": 2, "lot_decimals": 8,
            "ordermin": "0.1", "base": "HYPE",
        }

    def balance(self):
        return {"HYPE": "2.5", "ZUSD": "1000"}

    def ohlc_closes(self, pair, interval):
        return [10.0, 11.0, 12.0]


def _provider(fake):
    p = KrakenProvider()
    p._cli = fake                    # It short-circuits _client() (no keys, no network).
    return p


class KrakenExecutorContractTest(unittest.TestCase):
    def setUp(self):
        self.previous_live = os.environ.get("KRAKEN_LIVE_ORDERS")
        os.environ["KRAKEN_LIVE_ORDERS"] = "true"
        self.fake = FakeClient()
        self.p = _provider(self.fake)

    def tearDown(self):
        if self.previous_live is None:
            os.environ.pop("KRAKEN_LIVE_ORDERS", None)
        else:
            os.environ["KRAKEN_LIVE_ORDERS"] = self.previous_live

    @staticmethod
    def _slot_context():
        class AllowedSlot:
            allowed = True
            info = {}

            def commit(self, _order_id=None):
                return None

        class SlotContext:
            def __enter__(self):
                return AllowedSlot()

            def __exit__(self, *_args):
                return False

        return SlotContext()

    def _instrument_place(self, *, force=False, client_order_id=None):
        os.environ["KRAKEN_LIVE_ORDERS"] = "true"
        instrument = Instrument(
            "HYPE_KRAKEN", "HYPEUSD", "Kraken", base="HYPE", quote="USD",
            api=MarketApi([self.p]))
        with (
            patch.object(order_guard, "daily_limit_guard",
                         return_value=(True, None)),
            patch("instrument.trade_cooldown.trade_slot",
                  return_value=self._slot_context()),
            patch.object(instrument_module._outcomes_log, "log_order_outcome"),
        ):
            return instrument.place(
                "BUY", 60.0, 1.0, smart=False, wait_for_trend=False,
                caller_owns_retry=True, bypass_profit_guard=True,
                force=force, client_order_id=client_order_id)

    def test_satisfies_protocol(self):
        self.assertIsInstance(self.p, StrategyExecutor)

    def test_limit_submit_returns_order_id(self):
        oid = self.p.submit_order("HYPEUSD", "buy", 2.5, price=60.0)
        self.assertEqual(oid, "OABC-123")
        # it delegated correctly: limit, the price passed through, validate=False
        self.assertEqual(self.fake.calls[-1],
                         ("add_order", "HYPEUSD", "buy", 2.5, 60.0, "limit", False, None))

    def test_submit_propagates_client_order_id(self):
        client_id = "0123456789abcdef0123456789abcdef"
        self.p.submit_order(
            "HYPEUSD", "buy", 2.5, price=60.0, client_order_id=client_id,
        )
        self.assertEqual(self.fake.calls[-1][-1], client_id)

    def test_live_instrument_path_forwards_the_deterministic_client_id(self):
        raw_client_id = "OR_0123456789abcdef01234567_0"
        expected = kraken_provider._kraken_client_order_id(raw_client_id)

        order = self._instrument_place(client_order_id=raw_client_id)

        self.assertEqual(order["txid"], ["OABC-123"])
        self.assertEqual(self.fake.calls[-1][-1], expected)
        self.p.submit_order(
            "HYPEUSD", "buy", 1.0, price=60.0,
            client_order_id=raw_client_id)
        self.assertEqual(self.fake.calls[-1][-1], expected)

    def test_live_instrument_force_path_places_a_market_order_without_price(self):
        order = self._instrument_place(force=True)

        self.assertEqual(order["txid"], ["OABC-123"])
        call = self.fake.calls[-1]
        self.assertEqual(call[5], "market")
        self.assertIsNone(call[4])
        self.assertFalse(call[6])

    def test_client_id_lookup_is_not_advertised_as_authoritative(self):
        self.assertFalse(
            self.p.reconciliation_capabilities().lookup_by_client_order_id)

    def test_submit_order_market_without_a_price(self):
        self.p.submit_order("HYPEUSD", "sell", 1.0, price=59.0, market=True)
        c = self.fake.calls[-1]
        self.assertEqual(c[5], "market")     # ordertype
        self.assertIsNone(c[4])              # The price is None on a market order.

    def test_raw_submit_refuses_when_live_execution_is_disabled(self):
        os.environ["KRAKEN_LIVE_ORDERS"] = "false"

        with self.assertRaisesRegex(ProviderError, "real execution is disabled"):
            self.p.submit_order("HYPEUSD", "buy", 1.0, price=60.0)

        self.assertEqual(self.fake.calls, [])

    def test_strategy_orchestration_requires_both_execution_flags(self):
        for strategy_execute, provider_execute, expected_dry in (
                (False, False, True),
                (False, True, True),
                (True, False, True),
                (True, True, False)):
            with self.subTest(
                    strategy_execute=strategy_execute,
                    provider_execute=provider_execute):
                os.environ["KRAKEN_LIVE_ORDERS"] = (
                    "true" if provider_execute else "false")
                self.assertEqual(
                    kraken_strategy_dry_run(False, strategy_execute),
                    expected_dry)
                self.assertTrue(
                    kraken_strategy_dry_run(True, strategy_execute))

    def test_final_submit_gate_observes_a_switch_flip(self):
        states = iter((True, False))
        original_preflight = self.p.preflight_order

        def preflight_then_disable(*args, **kwargs):
            result = original_preflight(*args, **kwargs)
            self.assertTrue(next(states))
            os.environ["KRAKEN_LIVE_ORDERS"] = "false"
            return result

        self.p.preflight_order = preflight_then_disable
        self.p.preflight_order(
            "HYPEUSD", "buy", 1.0, price=60.0, market=False, kind="DCA")
        self.assertFalse(next(states))

        with self.assertRaisesRegex(ProviderError, "real execution is disabled"):
            self.p.submit_order("HYPEUSD", "buy", 1.0, price=60.0)
        self.assertEqual(self.fake.calls, [])

    def test_submit_order_without_a_txid_raises(self):
        self.fake.add_order = lambda *a, **k: {"descr": {}}   # A response without a txid.
        with self.assertRaises(ProviderError):
            self.p.submit_order("HYPEUSD", "buy", 1.0, price=60.0)

    def test_venue_submit_error_becomes_provider_error(self):
        def boom(*a, **k):
            raise RuntimeError("Kraken: Insufficient funds")
        self.fake.add_order = boom
        with self.assertRaises(ProviderError):
            self.p.submit_order("HYPEUSD", "buy", 1.0, price=60.0)

    def test_sell_preflight_refuses_quantity_above_balance(self):
        with self.assertRaisesRegex(ProviderError, "insufficient funds SELL"):
            self.p.preflight_order(
                "HYPEUSD", "sell", 2.50000001, price=60.0,
            )
        self.assertFalse(any(call[0] == "add_order" for call in self.fake.calls))

    def test_sell_preflight_accepts_reconciled_balance(self):
        self.p.preflight_order("HYPEUSD", "sell", 2.5, price=60.0)

    def test_buy_preflight_leaves_fee_and_slippage_to_venue(self):
        self.fake.balance = lambda: (_ for _ in ()).throw(
            AssertionError("BUY preflight must not read balance"))
        self.p.preflight_order("HYPEUSD", "buy", 100.0, price=60.0)

    def test_order_status_mapping(self):
        st = self.p.order_status("HYPEUSD", "OABC-123")
        self.assertIsInstance(st, OrderStatus)
        self.assertEqual(st.status, "closed")
        self.assertEqual(st.filled_qty, 2.5)
        self.assertEqual(st.cost, 150.0)
        self.assertEqual(st.fee, 0.39)

    def test_a_missing_order_status_raises(self):
        self.fake.query_orders = lambda txids: {}      # The order does not appear.
        with self.assertRaises(ProviderError):
            self.p.order_status("HYPEUSD", "NOPE")

    def test_order_by_client_id_searches_open_and_closed(self):
        self.assertEqual(
            self.p.order_by_client_id(
                "HYPEUSD", "0123456789abcdef0123456789abcdef"),
            {"orderId": "OPEN-1", "status": "open"},
        )
        self.assertEqual(
            self.p.order_by_client_id(
                "HYPEUSD", "fedcba9876543210fedcba9876543210"),
            {"orderId": "CLOSED-1", "status": "closed"},
        )
        self.assertIsNone(
            self.p.order_by_client_id("HYPEUSD", "0" * 32))

    def test_open_orders_normalises_and_filters_by_symbol(self):
        self.assertEqual(self.p.open_orders("HYPEUSD"), [{
            "orderId": "OPEN-1",
            "clientOrderId": "0123456789abcdef0123456789abcdef",
            "side": "BUY",
            "price": 60.25,
            "origQty": 2.5,
            "executedQty": 0.5,
            "status": "OPEN",
        }])

    def test_ambiguous_open_orders_payload_fails_closed(self):
        self.fake.open_orders = lambda: {
            "BROKEN": {"status": "open", "vol": "1", "vol_exec": "0"},
        }
        with self.assertRaisesRegex(ProviderError, "without a pair"):
            self.p.open_orders("HYPEUSD")

    def test_cancel_delegates(self):
        self.p.cancel_order("HYPEUSD", "OABC-123")
        self.assertEqual(self.fake.calls[-1], ("cancel_order", "OABC-123"))

    def test_cancel_is_idempotent_on_an_unknown_order(self):
        def boom(txid):
            raise RuntimeError("EOrder:Unknown order")
        self.fake.cancel_order = boom
        self.p.cancel_order("HYPEUSD", "GONE")         # must NOT raise (idempotent)

    def test_unconfirmed_cancel_raises(self):
        self.fake.cancel_order = lambda txid: {"count": 0}
        with self.assertRaises(ProviderError):
            self.p.cancel_order("HYPEUSD", "OABC-123")

    def test_pair_precision_mapping(self):
        pp = self.p.pair_precision("HYPEUSD")
        self.assertEqual(pp, PairPrecision(
            price_decimals=2, volume_decimals=8,
            order_min=0.1, base_asset="HYPE",
        ))

    def test_unlisted_pair_precision_returns_none(self):
        self.fake.pair_info = lambda pair: None
        self.assertIsNone(self.p.pair_precision("NEWX"))

    def test_ohlc_closes_delegates(self):
        self.assertEqual(self.p.ohlc_closes("HYPEUSD", 240), [10.0, 11.0, 12.0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
