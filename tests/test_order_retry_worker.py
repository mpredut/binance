"""Tests for order_retry_worker.process_once — the pure queue-draining logic, with a
FAKE `mkt` (no network). The queue is isolated in tmp."""
import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import order_retry as oq
import order_retry_worker as worker
from providers.strategy_executor import OrderStatus


class FakeMkt:
    def __init__(self, price=100.0, succeed=True):
        self.price = price
        self.succeed = succeed
        self.calls = []
        self.status = OrderStatus("open", 0.0, 0.0, 0.0, "NEW")
        self.status_calls = []
    def get_current_price(self, symbol):
        return self.price
    def place(self, symbol, side, price, qty, **kw):
        self.calls.append({"symbol": symbol, "side": side, "price": price, "qty": qty, "kw": kw})
        return {"orderId": 1} if self.succeed else None
    def order_status(self, symbol, order_id, provider_name=None):
        self.status_calls.append((symbol, str(order_id), provider_name))
        return self.status


class ProcessOnceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        oq.QUEUE_FILE = os.path.join(self.tmp, "q.jsonl")
        oq.LOCK_FILE = os.path.join(self.tmp, "q.lock")
        oq.RETRY_ENABLED = True
        oq.RETRY_INTERVAL_SEC = 300.0
        oq.RETRY_TTL_SEC = 86400.0
        oq.RETRY_MAX_ATTEMPTS = 0
        oq.RETRY_PRICE_TOL = 0.002
        oq.RETRY_DEDUP = True
        oq.RETRY_MAX_QUEUE = 500

    def test_acceptance_stays_tracked_until_fill(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {"safeback_seconds": 9, "smart": False},
                   requested_price=63000.0, now=1000.0)   # BUY: the current price <= the requested one -> the gate passes.
        mkt = FakeMkt(price=63000.0, succeed=True)
        stats = worker.process_once(mkt, now=1000.0 + 400)   # due (>300s)
        self.assertEqual(stats["succeeded"], 1)
        tracked = oq.load_all()
        self.assertEqual(len(tracked), 1)
        self.assertEqual(tracked[0]["lifecycle"], "accepted")
        self.assertEqual(tracked[0]["order_id"], "1")
        # it was resumed with caller_owns_retry=True, at the CURRENT price, the kwargs preserved
        self.assertEqual(len(mkt.calls), 1)
        self.assertTrue(mkt.calls[0]["kw"]["caller_owns_retry"])
        self.assertEqual(mkt.calls[0]["price"], 63000.0)
        self.assertEqual(mkt.calls[0]["kw"]["smart"], False)

        mkt.status = OrderStatus("closed", 1.0, 63000.0, 1.0, "FILLED")
        terminal = worker.process_once(mkt, now=1700.0)
        self.assertEqual(terminal["filled"], 1)
        self.assertEqual(oq.load_all(), [])

    def test_failure_keeps_and_increments_attempts(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        mkt = FakeMkt(succeed=False)   # price=100 default, BUY gate ok (100<=100)
        worker.process_once(mkt, now=1000.0 + 400)
        q = oq.load_all()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["attempts"], 1)
        self.assertAlmostEqual(q[0]["last_attempt_ts"], 1000.0 + 400)

    def test_trend_refusal_is_retried_without_attempt_or_ttl_consumption(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)

        class TrendDeferredMkt(FakeMkt):
            def place(self, symbol, side, price, qty, **kw):
                self.calls.append({
                    "symbol": symbol, "side": side, "price": price,
                    "qty": qty, "kw": kw,
                })
                kw["_outcome_context"].update(
                    accepted=False, reason="trend_deferred")
                return None

        now = 1000.0 + 400
        stats = worker.process_once(TrendDeferredMkt(), now=now)
        rec = oq.load_all()[0]
        self.assertEqual(stats["attempted"], 1)
        self.assertEqual(stats["succeeded"], 0)
        self.assertEqual(rec["attempts"], 0)
        self.assertEqual(rec["last_failure_reason"], "trend_deferred")
        self.assertFalse(oq.is_expired(rec, now=now + 20 * 86400))

    def test_not_due_skipped(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, now=1000.0)
        mkt = FakeMkt(succeed=True)
        stats = worker.process_once(mkt, now=1000.0 + 100)   # < the interval of 300 -> not due.
        self.assertEqual(stats["attempted"], 0)
        self.assertEqual(len(oq.load_all()), 1)              # ramane

    def test_expired_dropped_and_alerted(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, now=1000.0)
        mkt = FakeMkt(succeed=True)
        alerts = []
        orig = worker.alert.notify
        worker.alert.notify = lambda **kw: alerts.append(kw)
        try:
            stats = worker.process_once(mkt, now=1000.0 + 86400 + 10)   # past the TTL.
        finally:
            worker.alert.notify = orig
        self.assertEqual(stats["expired"], 1)
        self.assertEqual(oq.load_all(), [])          # scos
        self.assertEqual(len(mkt.calls), 0)          # NOT retried (expired).
        self.assertEqual(len(alerts), 1)             # alerta de renuntare

    def test_legacy_invalid_quantity_is_discarded_without_submit(self):
        oq.rewrite([{
            "id": "legacy", "symbol": "BTCUSDC", "side": "BUY", "qty": None,
            "place_kwargs": {}, "requested_price": 100.0, "created_ts": 1000.0,
            "attempts": 2, "last_attempt_ts": 0.0,
        }])
        mkt = FakeMkt(price=100.0)
        stats = worker.process_once(mkt, now=1400.0)
        self.assertEqual(stats["expired"], 1)
        self.assertEqual(mkt.calls, [])
        self.assertEqual(oq.load_all(), [])

    def test_price_none_leaves_in_queue(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, now=1000.0)
        mkt = FakeMkt(price=None, succeed=True)
        stats = worker.process_once(mkt, now=1000.0 + 400)
        self.assertEqual(stats["attempted"], 0)
        self.assertEqual(len(oq.load_all()), 1)      # The price is unavailable -> it stays.

    def test_price_gate_skips_unfavorable(self):
        # SELL requested at 100; current price 90 (below) -> the guard stops it, no attempt
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=100.0, now=1000.0)
        mkt = FakeMkt(price=90.0, succeed=True)
        stats = worker.process_once(mkt, now=1000.0 + 400)
        self.assertEqual(stats["attempted"], 0)
        self.assertEqual(stats["skipped_price"], 1)
        self.assertEqual(len(mkt.calls), 0)          # nothing was placed
        q = oq.load_all()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["attempts"], 0)        # It does not count as an attempt.

    def test_price_gate_allows_favorable(self):
        # A SELL requested at 100; the current price is 101 (above) -> the guard lets it through and it is resumed.
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=100.0, now=1000.0)
        mkt = FakeMkt(price=101.0, succeed=True)
        stats = worker.process_once(mkt, now=1000.0 + 400)
        self.assertEqual(stats["succeeded"], 1)
        self.assertEqual(len(mkt.calls), 1)
        self.assertEqual(mkt.calls[0]["price"], 101.0)   # Resumed at the CURRENT price.
        self.assertEqual(oq.load_all()[0]["lifecycle"], "accepted")

    def test_leased_in_queue_during_place_and_not_due(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        seen = {}

        class CheckMkt(FakeMkt):
            def place(self, symbol, side, price, qty, **kw):
                queued = oq.load_all()
                seen["in_queue_during_place"] = len(queued)
                seen["due_during_place"] = oq.is_due(queued[0], now=1400.0)
                return super().place(symbol, side, price, qty, **kw)

        mkt = CheckMkt(price=100.0, succeed=True)
        worker.process_once(mkt, now=1000.0 + 400)
        self.assertEqual(seen["in_queue_during_place"], 1)
        self.assertFalse(seen["due_during_place"])
        self.assertEqual(oq.load_all()[0]["lifecycle"], "accepted")

    def test_truthy_response_without_order_id_is_failure(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)

        class AmbiguousMkt(FakeMkt):
            def place(self, *args, **kwargs):
                return {"status": "unknown"}

        stats = worker.process_once(AmbiguousMkt(price=100.0), now=1400.0)
        self.assertEqual(stats["succeeded"], 0)
        self.assertEqual(oq.load_all()[0]["attempts"], 1)

    def test_existing_client_order_is_reconciled_without_resubmit(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)

        class ReconcileMkt(FakeMkt):
            def order_by_client_id(self, symbol, client_order_id):
                return {"orderId": 77, "status": "FILLED", "clientOrderId": client_order_id}

        mkt = ReconcileMkt(price=100.0)
        stats = worker.process_once(mkt, now=1400.0)
        self.assertEqual(stats["reconciled"], 1)
        self.assertEqual(stats["succeeded"], 1)
        self.assertEqual(mkt.calls, [])
        self.assertEqual(oq.load_all()[0]["order_id"], "77")

    def test_ambiguous_submit_is_reconciled_after_response_loss(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)

        class LostResponseMkt(FakeMkt):
            def __init__(self):
                super().__init__(price=100.0, succeed=False)
                self.lookups = 0

            def order_by_client_id(self, symbol, client_order_id):
                self.lookups += 1
                if self.lookups == 1:
                    return None
                return {"orderId": 88, "status": "NEW", "clientOrderId": client_order_id}

        mkt = LostResponseMkt()
        stats = worker.process_once(mkt, now=1400.0)
        self.assertEqual(len(mkt.calls), 1)
        self.assertEqual(stats["reconciled"], 1)
        self.assertEqual(oq.load_all()[0]["order_id"], "88")

    def test_open_partial_is_observed_without_resubmit(self):
        rid = oq.enqueue(
            "BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        oq.mark_accepted(rid, {"orderId": 41, "status": "NEW"}, now=1100.0)
        mkt = FakeMkt(price=100.0)
        mkt.status = OrderStatus(
            "open", 0.4, 39.5, 0.02, "PARTIALLY_FILLED")

        stats = worker.process_once(mkt, now=1400.0)

        self.assertEqual(stats["observed"], 1)
        self.assertEqual(mkt.calls, [])
        rec = oq.load_all()[0]
        self.assertEqual(rec["lifecycle"], "accepted")
        self.assertEqual(rec["filled_qty"], 0.4)
        self.assertEqual(rec["last_status"], "open")

    def test_expired_partial_retries_only_remainder_with_new_client_id(self):
        rid = oq.enqueue(
            "BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        first_cid = oq.get(rid)["place_kwargs"]["client_order_id"]
        oq.mark_accepted(rid, {"orderId": 42, "status": "NEW"}, now=1100.0)
        mkt = FakeMkt(price=99.0)
        mkt.status = OrderStatus("expired", 0.4, 39.5, 0.02, "EXPIRED")

        stats = worker.process_once(mkt, now=1400.0)

        self.assertEqual(stats["terminal_retried"], 1)
        self.assertEqual(mkt.calls, [])
        rec = oq.load_all()[0]
        self.assertEqual(rec["lifecycle"], "submit_pending")
        self.assertAlmostEqual(rec["qty"], 0.6)
        self.assertEqual(rec["delivered_qty"], 0.4)
        self.assertEqual(rec["revision"], 1)
        self.assertNotEqual(rec["place_kwargs"]["client_order_id"], first_cid)
        self.assertEqual(rec["order_history"][-1]["venue_status"], "EXPIRED")

        retried = worker.process_once(mkt, now=1700.0)
        self.assertEqual(retried["attempted"], 1)
        self.assertEqual(mkt.calls[0]["qty"], 0.6)

    def test_native_rejected_status_is_retried_but_canceled_is_not(self):
        rejected_id = oq.enqueue(
            "BTCUSDC", "SELL", 2.0, {}, requested_price=100.0, now=1000.0)
        oq.mark_accepted(
            rejected_id, {"orderId": 50, "status": "NEW"}, now=1100.0)
        mkt = FakeMkt(price=101.0)
        mkt.status = OrderStatus("canceled", 0.0, 0.0, 0.0, "REJECTED")

        stats = worker.process_once(mkt, now=1400.0)
        self.assertEqual(stats["terminal_retried"], 1)
        self.assertEqual(oq.load_all()[0]["lifecycle"], "submit_pending")

        # A separate intentional cancel is terminal and removed without submit.
        oq.rewrite([])
        canceled_id = oq.enqueue(
            "BTCUSDC", "SELL", 2.0, {}, requested_price=100.0, now=2000.0)
        oq.mark_accepted(
            canceled_id, {"orderId": 51, "status": "NEW"}, now=2100.0)
        mkt.status = OrderStatus("canceled", 0.5, 50.0, 0.02, "CANCELED")
        alerts = []
        original_notify = worker.alert.notify
        worker.alert.notify = lambda **kwargs: alerts.append(kwargs)
        try:
            canceled = worker.process_once(mkt, now=2400.0)
        finally:
            worker.alert.notify = original_notify

        self.assertEqual(canceled["terminal_failed"], 1)
        self.assertEqual(oq.load_all(), [])
        self.assertEqual(len(mkt.calls), 0)
        self.assertEqual(len(alerts), 1)

    def test_status_error_is_rate_limited_by_observation_interval(self):
        rid = oq.enqueue(
            "BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        oq.mark_accepted(rid, {"orderId": 60}, now=1100.0)

        class BrokenStatusMkt(FakeMkt):
            def order_status(self, symbol, order_id, provider_name=None):
                self.status_calls.append((symbol, str(order_id), provider_name))
                raise RuntimeError("venue unavailable")

        mkt = BrokenStatusMkt()
        first = worker.process_once(mkt, now=1400.0)
        second = worker.process_once(mkt, now=1500.0)

        self.assertEqual(first["observed"], 1)
        self.assertEqual(second["observed"], 0)
        self.assertEqual(len(mkt.status_calls), 1)
        self.assertIn("venue unavailable", oq.load_all()[0]["status_error"])

    def test_failure_reenqueues_preserving_age(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        mkt = FakeMkt(price=100.0, succeed=False)   # The BUY gate passes (100<=100), but it fails.
        worker.process_once(mkt, now=1000.0 + 400)
        q = oq.load_all()
        self.assertEqual(len(q), 1)                  # Re-added after the failure.
        self.assertEqual(q[0]["created_ts"], 1000.0) # The age is PRESERVED (the TTL does not reset).
        self.assertEqual(q[0]["attempts"], 1)        # attempts+1


if __name__ == "__main__":
    unittest.main(verbosity=2)
