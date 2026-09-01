"""Contractul auditului observational al StrategyExecutor."""
import json
import os
import tempfile
import unittest
from unittest import mock

import providers.execution_audit as execution_audit_module
from order_retry import StrategyExecutorLifecycleApi

from providers.execution_audit import (
    AuditedStrategyExecutor,
    ExecutionAudit,
    intent_client_order_id,
)
from providers.strategy_executor import (
    OrderReconciliationCapabilities,
    OrderStatus,
    PairPrecision,
    ProviderError,
    StrategyExecutor,
)


class _Executor:
    name = "TEST"

    def __init__(self):
        self.calls = []
        self.status = OrderStatus("open", 0.25, 25.0, 0.05)
        self.submit_error = None

    def get_current_price(self, symbol):
        return 100.0

    def reconciliation_capabilities(self):
        return OrderReconciliationCapabilities(True, True, True, False)

    def submit_order(self, symbol, side, qty, price=None, *, market=False, kind=None,
                     client_order_id=None):
        self.calls.append((
            "submit", symbol, side, qty, price, market, kind, client_order_id,
        ))
        if self.submit_error:
            raise self.submit_error
        return "OID-7"

    def order_status(self, symbol, order_id):
        self.calls.append(("status", symbol, order_id))
        return self.status

    def order_by_client_id(self, symbol, client_order_id):
        self.calls.append(("lookup", symbol, client_order_id))
        return {"orderId": "OID-7", "status": "open"}

    def cancel_order(self, symbol, order_id):
        self.calls.append(("cancel", symbol, order_id))

    def pair_precision(self, symbol):
        return PairPrecision(2, 2, 0.01, symbol)

    def free_balance(self, asset):
        return 3.0

    def ohlc_closes(self, symbol, interval_min):
        return [99.0, 100.0]


class ExecutionAuditTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.executor = _Executor()
        self.audit = ExecutionAudit(self.tmp.name, clock=lambda: 1_700_000_000.0)
        self.wrapped = AuditedStrategyExecutor(self.executor, self.audit, venue="Kraken")

    def tearDown(self):
        self.tmp.cleanup()

    def _events(self):
        paths = [os.path.join(self.tmp.name, name) for name in os.listdir(self.tmp.name)]
        rows = []
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                rows.extend(json.loads(line) for line in handle if line.strip())
        return rows

    def test_lifecycle_uses_one_intent_and_suppresses_identical_status_polls(self):
        self.assertIsInstance(self.wrapped, StrategyExecutor)
        oid = self.wrapped.submit_order_with_intent(
            "intent-fixed", "ABC", "buy", 0.25, 100.0, kind="ENTRY",
        )
        self.wrapped.order_status_with_intent("intent-fixed", "ABC", oid)
        self.wrapped.order_status_with_intent("intent-fixed", "ABC", oid)
        self.wrapped.cancel_order_with_intent("intent-fixed", "ABC", oid)

        rows = self._events()
        self.assertEqual(
            [row["event"] for row in rows],
            ["submit_requested", "submit_accepted", "order_status",
             "cancel_requested", "cancel_accepted"],
        )
        self.assertEqual({row["intent_id"] for row in rows}, {"intent-fixed"})
        self.assertEqual(rows[2]["filled_qty"], 0.25)
        client_id = intent_client_order_id("Kraken", "intent-fixed")
        self.assertEqual(rows[0]["client_order_id"], client_id)
        self.assertEqual(self.executor.calls[0][-1], client_id)

    def test_cache_urile_de_corelare_sunt_lru_plafonate(self):
        with mock.patch.object(execution_audit_module, "_CACHE_MAX", 2):
            for index in range(3):
                order_id = f"OID-{index}"
                self.wrapped._remember("ABC", order_id, f"intent-{index}")
                self.wrapped.order_status_with_intent(
                    f"intent-{index}", "ABC", order_id)
        self.assertEqual(len(self.wrapped._intent_by_order), 2)
        self.assertEqual(len(self.wrapped._last_status), 2)
        self.assertNotIn(("ABC", "OID-0"), self.wrapped._intent_by_order)
        self.assertNotIn(("ABC", "OID-0"), self.wrapped._last_status)

    def test_submit_error_is_audited_as_unknown_and_compatibility_raises(self):
        self.executor.submit_error = ProviderError("venue down")
        with self.assertRaisesRegex(ProviderError, "venue down"):
            self.wrapped.submit_order_with_intent(
                "intent-error", "ABC", "sell", 1.0, market=True, kind="STOP",
                reference_price=99.5,
            )
        rows = self._events()
        self.assertEqual([row["event"] for row in rows],
                         ["submit_requested", "submit_unknown"])
        self.assertEqual(rows[-1]["submission_state"], "unknown")
        self.assertIn("ProviderError: venue down", rows[-1]["reason"])
        self.assertTrue(rows[-1]["market"])
        self.assertEqual(rows[-1]["reference_price"], 99.5)
        submit = [call for call in self.executor.calls if call[0] == "submit"]
        self.assertEqual(len(submit), 1)
        self.assertEqual(submit[0][1:7], ("ABC", "sell", 1.0, None, True, "STOP"))
        self.assertEqual(
            submit[0][-1], intent_client_order_id("Kraken", "intent-error"),
        )

    def test_client_order_id_formats_preserve_full_intent_uuid(self):
        intent = "kraken-HYPEUSD-entry-0123456789abcdef0123456789abcdef"
        self.assertEqual(
            intent_client_order_id("Kraken", intent),
            "0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(
            intent_client_order_id("Binance", intent),
            "SD_0123456789abcdef0123456789abcdef",
        )
        self.assertEqual(
            intent_client_order_id("Hyperliquid", intent),
            "0x0123456789abcdef0123456789abcdef",
        )
        self.assertIsNone(intent_client_order_id("T212", intent))

    def test_order_by_client_id_is_delegated(self):
        result = self.wrapped.order_by_client_id("ABC", "client-7")
        self.assertEqual(result["orderId"], "OID-7")
        self.assertEqual(self.executor.calls[-1], ("lookup", "ABC", "client-7"))

    def test_reconciliation_capabilities_survive_audit_decoration(self):
        self.assertEqual(
            self.wrapped.reconciliation_capabilities(),
            self.executor.reconciliation_capabilities(),
        )
        lifecycle_api = StrategyExecutorLifecycleApi(self.wrapped)
        self.assertEqual(
            lifecycle_api.reconciliation_capabilities(),
            self.executor.reconciliation_capabilities(),
        )


if __name__ == "__main__":
    unittest.main()
