"""Teste pt order_retry.py — store-ul outbox al cozii de re-plasare (enqueue/load/rewrite
plus the is_due/is_expired logic). The files are isolated in tmp (it does not touch the real cachedb/)."""
import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import order_retry as oq
from providers.strategy_executor import OrderStatus


class OrderRetryStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        oq.QUEUE_FILE = os.path.join(self.tmp, "order_retry_queue.jsonl")
        oq.LOCK_FILE = os.path.join(self.tmp, "order_retry_queue.lock")
        oq.RETRY_ENABLED = True
        oq.RETRY_INTERVAL_SEC = 300.0
        oq.RETRY_TTL_SEC = 86400.0
        oq.RETRY_MAX_ATTEMPTS = 0
        oq.RETRY_PRICE_TOL = 0.002
        oq.RETRY_DEDUP = True
        oq.RETRY_MAX_QUEUE = 500

    def test_enqueue_and_load(self):
        i = oq.enqueue("BTCUSDC", "BUY", 1.0,
                       {"safeback_seconds": 999, "force": False, "smart": False}, now=1000.0)
        self.assertIsNotNone(i)
        items = oq.load_all()
        self.assertEqual(len(items), 1)
        r = items[0]
        self.assertEqual(r["symbol"], "BTCUSDC")
        self.assertEqual(r["side"], "BUY")
        self.assertEqual(r["qty"], 1.0)
        self.assertEqual(r["place_kwargs"]["safeback_seconds"], 999)
        self.assertEqual(r["attempts"], 0)
        self.assertNotIn("price", r)   # The price is NOT saved as a value to send.

    def test_enqueue_captures_price_intent(self):
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, ref_price=62950.0,
                   now=1000.0)
        r = oq.load_all()[0]
        self.assertEqual(r["requested_price"], 63000.0)
        self.assertEqual(r["ref_price"], 62950.0)

    def test_dedup_same_side_refreshes_not_duplicates(self):
        a = oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, now=1000.0)
        # the same symbol+side intent -> no duplicate; it refreshes the price, same id
        b = oq.enqueue("BTCUSDC", "SELL", 2.0, {}, requested_price=63100.0, now=1001.0)
        self.assertEqual(a, b)
        items = oq.load_all()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["requested_price"], 63100.0)   # tinta reimprospatata
        self.assertEqual(items[0]["qty"], 2.0)
        self.assertEqual(items[0]["created_ts"], 1000.0)         # vechimea PASTRATA (min)
        self.assertNotEqual(items[0]["place_kwargs"]["client_order_id"],
                            f"OR_{a[:24]}_0")

    def test_legacy_dedup_never_overwrites_an_accepted_tracker(self):
        first = oq.enqueue(
            "BTCUSDC", "SELL", 1.0, {}, requested_price=100.0, now=1000.0)
        oq.mark_accepted(first, {"orderId": 7, "status": "NEW"}, now=1001.0)

        second = oq.enqueue(
            "BTCUSDC", "SELL", 2.0, {}, requested_price=101.0, now=1002.0)

        self.assertNotEqual(first, second)
        rows = oq.load_all()
        self.assertEqual(len(rows), 2)
        accepted = next(row for row in rows if row["id"] == first)
        self.assertEqual(accepted["lifecycle"], "accepted")
        self.assertEqual(accepted["order_id"], "7")

    def test_client_order_id_is_stable_for_one_revision(self):
        rid = oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        rec = oq.load_all()[0]
        self.assertEqual(rec["place_kwargs"]["client_order_id"], f"OR_{rid[:24]}_0")
        claimed = oq.claim([rid], now=1400.0)[0]
        self.assertEqual(claimed["place_kwargs"]["client_order_id"],
                         rec["place_kwargs"]["client_order_id"])

    def test_dedup_collapses_ladder_any_distance(self):
        # even at VERY different prices (the former "ladder") -> still a single intent per side
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, now=1000.0)
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=70000.0, now=1001.0)
        self.assertEqual(len(oq.load_all()), 1)
        # side diferit = intentie distincta
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=62000.0, now=1002.0)
        self.assertEqual(len(oq.load_all()), 2)

    def test_dedup_preserves_attempts_on_refresh(self):
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, now=1000.0, attempts=3)
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63100.0, now=1001.0, attempts=0)
        self.assertEqual(oq.load_all()[0]["attempts"], 3)    # max(attempts) pastrat

    def test_dedup_off_appends_always(self):
        oq.RETRY_DEDUP = False
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, now=1000.0)
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, now=1001.0)
        self.assertEqual(len(oq.load_all()), 2)

    def test_full_queue_replaces_oldest_pending_record(self):
        oq.RETRY_MAX_QUEUE = 2
        oq.RETRY_DEDUP = False   # so it is not collapsed by dedup
        oldest = oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=1.0,
                            now=1000.0, failure_reason="trend_deferred")
        retained = oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=2.0,
                              now=1001.0, failure_reason="trend_deferred")
        newest = oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=3.0,
                            now=1002.0, failure_reason="trend_deferred")
        self.assertIsNotNone(newest)
        ids = {record["id"] for record in oq.load_all()}
        self.assertNotIn(oldest, ids)
        self.assertIn(retained, ids)
        self.assertIn(newest, ids)

    def test_full_queue_never_evicts_accepted_orders(self):
        oq.RETRY_MAX_QUEUE = 1
        oq.RETRY_DEDUP = False
        accepted_id = oq.enqueue(
            "BTCUSDC", "BUY", 1.0, {}, requested_price=1.0, now=1000.0)
        records = oq.load_all()
        records[0]["lifecycle"] = "accepted"
        records[0]["order_id"] = "venue-1"
        oq.rewrite(records)
        self.assertIsNone(oq.enqueue(
            "TAOUSDC", "SELL", 1.0, {}, requested_price=2.0, now=1001.0))
        self.assertEqual([record["id"] for record in oq.load_all()], [accepted_id])

    def test_price_gate_sell(self):
        rec = {"side": "SELL", "requested_price": 100.0}
        self.assertTrue(oq.price_gate_ok(rec, 100.0))         # egal -> ok
        self.assertTrue(oq.price_gate_ok(rec, 101.0))         # mai sus -> ok (vinzi mai bine)
        self.assertTrue(oq.price_gate_ok(rec, 99.9))          # in toleranta 0.2%
        self.assertFalse(oq.price_gate_ok(rec, 95.0))         # Far below -> it waits.
        self.assertFalse(oq.price_gate_ok(rec, None))         # no price -> we do not decide

    def test_price_gate_buy(self):
        rec = {"side": "BUY", "requested_price": 100.0}
        self.assertTrue(oq.price_gate_ok(rec, 100.0))
        self.assertTrue(oq.price_gate_ok(rec, 99.0))          # mai jos -> ok (cumperi mai ieftin)
        self.assertFalse(oq.price_gate_ok(rec, 105.0))        # Far above -> it waits.

    def test_price_gate_no_intent_skips(self):
        # no captured requested_price (old/abnormal entry) -> conservative, do NOT retry blind
        self.assertFalse(oq.price_gate_ok({"side": "SELL"}, 50.0))
        self.assertFalse(oq.price_gate_ok({"side": "BUY", "requested_price": 0}, 50.0))
        self.assertFalse(oq.price_gate_ok({"side": "HOLD", "requested_price": 50}, 50.0))
        self.assertFalse(oq.price_gate_ok(
            {"side": "BUY", "requested_price": 50}, float("nan")))

    def test_enqueue_disabled_returns_none(self):
        oq.RETRY_ENABLED = False
        self.assertIsNone(oq.enqueue("BTCUSDC", "BUY", 1.0, {}))
        self.assertEqual(oq.load_all(), [])

    def test_multiple_appends_preserved(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, now=1000.0)
        oq.enqueue("TAOUSDC", "SELL", 2.0, {}, now=1001.0)
        self.assertEqual(len(oq.load_all()), 2)

    def test_rewrite_removes_items(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, now=1000.0)
        oq.enqueue("TAOUSDC", "SELL", 2.0, {}, now=1001.0)
        items = oq.load_all()
        oq.rewrite([items[1]])   # Keep only the second one.
        rem = oq.load_all()
        self.assertEqual(len(rem), 1)
        self.assertEqual(rem[0]["symbol"], "TAOUSDC")

    def test_exact_record_resolution_does_not_remove_newer_revision(self):
        rid = oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0,
                         now=1000.0)
        first = oq.get(rid)
        first_cid = first["place_kwargs"]["client_order_id"]
        oq.enqueue("BTCUSDC", "BUY", 2.0, {}, requested_price=99.0,
                   now=1001.0)

        self.assertFalse(oq.resolve_record(rid, client_order_id=first_cid))
        self.assertEqual(oq.get(rid)["qty"], 2.0)

    def test_mark_failure_preserves_pre_submit_client_id(self):
        rid = oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0,
                         failure_reason="submit_pending", now=1000.0)
        client_id = oq.get(rid)["place_kwargs"]["client_order_id"]

        self.assertTrue(oq.mark_failure(
            rid, "response_without_order_id", now=1001.0,
            client_order_id=client_id))
        rec = oq.get(rid)
        self.assertEqual(rec["place_kwargs"]["client_order_id"], client_id)
        self.assertEqual(rec["last_failure_reason"], "response_without_order_id")

    def test_mark_accepted_keeps_exact_record_until_terminal(self):
        rid = oq.enqueue(
            "BTCUSDC", "BUY", 1.0, {}, requested_price=100.0,
            provider_name="Binance", intent_id="strategy-cycle-entry",
            kind="ENTRY", now=1000.0)
        client_id = oq.get(rid)["place_kwargs"]["client_order_id"]

        self.assertTrue(oq.mark_accepted(
            rid, {"orderId": 123, "status": "NEW"}, now=1100.0,
            client_order_id=client_id))

        rec = oq.get(rid)
        self.assertEqual(rec["lifecycle"], "accepted")
        self.assertEqual(rec["order_id"], "123")
        self.assertEqual(rec["provider_name"], "Binance")
        self.assertEqual(rec["intent_id"], "strategy-cycle-entry")
        self.assertEqual(rec["kind"], "ENTRY")
        self.assertFalse(oq.is_expired(rec, now=1000.0 + 100 * 86400))
        self.assertFalse(oq.is_due(rec, now=1399.0))
        self.assertTrue(oq.is_due(rec, now=1400.0))

    def test_mark_accepted_rejects_wrong_revision_client_id(self):
        rid = oq.enqueue(
            "BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)

        self.assertFalse(oq.mark_accepted(
            rid, {"orderId": 123}, now=1100.0,
            client_order_id="different"))
        self.assertEqual(oq.get(rid)["lifecycle"], "submit_pending")

    def test_legacy_resolve_record_cannot_remove_accepted_tracker(self):
        rid = oq.enqueue(
            "BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        cid = oq.get(rid)["place_kwargs"]["client_order_id"]
        self.assertTrue(oq.mark_accepted(
            rid, {"orderId": 91, "status": "NEW"}, now=1100.0,
            client_order_id=cid))

        self.assertFalse(oq.resolve_record(rid, client_order_id=cid))
        self.assertEqual(oq.get(rid)["order_id"], "91")

    def test_order_id_parser_handles_provider_shapes(self):
        self.assertEqual(oq.order_id_from_response({"orderId": 1}), "1")
        self.assertEqual(oq.order_id_from_response({"id": "two"}), "two")
        self.assertEqual(oq.order_id_from_response({"txid": ["three"]}), "three")
        self.assertIsNone(oq.order_id_from_response({"status": "NEW"}))

    def test_claim_leases_and_returns_without_removing(self):
        a = oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=1.0, now=1000.0)
        oq.enqueue("TAOUSDC", "SELL", 2.0, {}, requested_price=2.0, now=1001.0)
        claimed = oq.claim([a])
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["id"], a)
        rem = oq.load_all()
        self.assertEqual(len(rem), 2)
        leased = next(r for r in rem if r["id"] == a)
        self.assertTrue(leased["claim_token"])
        self.assertGreater(leased["claim_until"], time.time())

    def test_claim_failure_releases_and_increments(self):
        rid = oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=1.0, now=1000.0)
        claimed = oq.claim([rid], now=1400.0)[0]
        self.assertTrue(oq.complete_claim(claimed, "failure", now=1401.0))
        rec = oq.load_all()[0]
        self.assertEqual(rec["attempts"], 1)
        self.assertEqual(rec["last_attempt_ts"], 1401.0)
        self.assertNotIn("claim_token", rec)

    def _accepted_claim(self, *, qty=1.0, now=1000.0):
        rid = oq.enqueue(
            "BTCUSDC", "BUY", qty, {}, requested_price=100.0, now=now)
        oq.mark_accepted(
            rid, {"orderId": 77, "status": "NEW"}, now=now + 100.0)
        return oq.claim([rid], now=now + 400.0)[0]

    def test_advance_claimed_status_observes_partial_without_resubmit_state(self):
        claimed = self._accepted_claim(qty=2.0)
        status = OrderStatus(
            "open", 0.5, 49.0, 0.01, venue_status="PARTIALLY_FILLED")

        transition = oq.advance_claimed_status(claimed, status, now=1401.0)

        self.assertEqual(transition.action, "observed")
        self.assertTrue(transition.status_changed)
        record = oq.load_all()[0]
        self.assertEqual(record["lifecycle"], "accepted")
        self.assertEqual(record["filled_qty"], 0.5)
        self.assertNotIn("claim_token", record)

    def test_advance_claimed_status_fill_removes_active_record(self):
        claimed = self._accepted_claim()

        transition = oq.advance_claimed_status(
            claimed,
            OrderStatus("closed", 1.0, 100.0, 0.02, venue_status="FILLED"),
            now=1401.0,
        )

        self.assertEqual(transition.action, "filled")
        self.assertEqual(oq.load_all(), [])

    def test_advance_claimed_status_rejected_revises_only_remainder(self):
        claimed = self._accepted_claim(qty=2.0)

        transition = oq.advance_claimed_status(
            claimed,
            OrderStatus(
                "canceled", 0.5, 49.0, 0.01, venue_status="REJECTED"),
            now=1401.0,
        )

        self.assertEqual(transition.action, "retry_terminal")
        self.assertEqual(transition.remaining_qty, 1.5)
        record = oq.load_all()[0]
        self.assertEqual(record["lifecycle"], "submit_pending")
        self.assertEqual(record["qty"], 1.5)
        self.assertEqual(record["revision"], 1)

    def test_advance_claimed_status_cancel_is_terminal_without_revision(self):
        claimed = self._accepted_claim(qty=2.0)

        transition = oq.advance_claimed_status(
            claimed,
            OrderStatus(
                "canceled", 0.5, 49.0, 0.01, venue_status="CANCELED"),
            now=1401.0,
        )

        self.assertEqual(transition.action, "terminal")
        self.assertEqual(transition.remaining_qty, 1.5)
        self.assertEqual(oq.load_all(), [])

    def test_claim_survives_crash_and_becomes_due_after_lease(self):
        rid = oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=1.0, now=1000.0)
        oq.claim([rid], now=1400.0, lease_sec=120.0)
        rec = oq.load_all()[0]
        self.assertFalse(oq.is_due(rec, now=1519.0))
        self.assertTrue(oq.is_due(rec, now=1521.0))

    def test_success_does_not_delete_newer_refresh(self):
        rid = oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        claimed = oq.claim([rid], now=1400.0)[0]
        oq.enqueue("BTCUSDC", "BUY", 2.0, {}, requested_price=90.0, now=1401.0)
        oq.complete_claim(claimed, "success", now=1402.0)
        rec = oq.load_all()[0]
        self.assertEqual(rec["qty"], 2.0)
        self.assertEqual(rec["requested_price"], 90.0)
        self.assertNotIn("claim_token", rec)

    def test_invalid_intent_is_not_enqueued(self):
        self.assertIsNone(oq.enqueue("BTCUSDC", "BUY", None, {}, now=1000.0))
        self.assertIsNone(oq.enqueue("BTCUSDC", "HOLD", 1.0, {}, now=1000.0))
        self.assertIsNone(oq.enqueue("", "BUY", 1.0, {}, now=1000.0))

    def test_claim_migrates_legacy_record_to_deterministic_client_id(self):
        oq.rewrite([{
            "id": "a" * 32, "symbol": "BTCUSDC", "side": "SELL", "qty": 1.0,
            "place_kwargs": {}, "requested_price": 100.0, "created_ts": 1000.0,
            "attempts": 0, "last_attempt_ts": 0.0,
        }])
        rec = oq.claim(["a" * 32], now=1400.0)[0]
        self.assertEqual(rec["place_kwargs"]["client_order_id"],
                         "OR_" + "a" * 24 + "_0")
        self.assertEqual(oq.load_all()[0]["place_kwargs"]["client_order_id"],
                         rec["place_kwargs"]["client_order_id"])

    def test_claim_empty_noop(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=1.0, now=1000.0)
        self.assertEqual(oq.claim([]), [])
        self.assertEqual(len(oq.load_all()), 1)

    def test_resolve_removes_only_matching_symbol_and_side(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=1.0, now=1000.0)
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=2.0, now=1001.0)
        oq.enqueue("TAOUSDC", "BUY", 1.0, {}, requested_price=3.0, now=1002.0)

        self.assertEqual(oq.resolve("BTCUSDC", "buy"), 1)
        self.assertEqual(oq.resolve("BTCUSDC", "BUY"), 0)  # idempotent
        remaining = {(r["symbol"], r["side"]) for r in oq.load_all()}
        self.assertEqual(remaining, {("BTCUSDC", "SELL"), ("TAOUSDC", "BUY")})

    def test_reenqueue_preserves_created_ts(self):
        # the worker re-adds a failure preserving the age -> the TTL does not reset on every failure
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=1.0,
                   now=5000.0, created_ts=1000.0, attempts=2)
        r = oq.load_all()[0]
        self.assertEqual(r["created_ts"], 1000.0)
        self.assertEqual(r["attempts"], 2)

    def test_is_due(self):
        rec = {"last_attempt_ts": 1000.0}
        self.assertFalse(oq.is_due(rec, now=1000.0 + 100))   # < interval 300
        self.assertTrue(oq.is_due(rec, now=1000.0 + 300))    # >= interval

    def test_is_expired_ttl(self):
        rec = {"created_ts": 1000.0, "attempts": 0}
        self.assertFalse(oq.is_expired(rec, now=1000.0 + 86400 - 1))
        self.assertTrue(oq.is_expired(rec, now=1000.0 + 86400 + 1))

    def test_trend_deferred_record_never_consumes_ttl_or_attempts(self):
        rid = oq.enqueue(
            "BTCUSDC", "BUY", 1.0, {}, requested_price=100.0,
            failure_reason="trend_deferred", now=1000.0)
        rec = oq.load_all()[0]
        self.assertFalse(oq.is_expired(rec, now=1000.0 + 10 * 86400))

        claimed = oq.claim([rid], now=1000.0 + 10 * 86400)[0]
        oq.complete_claim(
            claimed, "deferred", now=1000.0 + 10 * 86400,
            failure_reason="trend_deferred")
        rec = oq.load_all()[0]
        self.assertEqual(rec["attempts"], 0)
        self.assertEqual(rec["last_failure_reason"], "trend_deferred")
        self.assertFalse(oq.is_expired(rec, now=1000.0 + 20 * 86400))

    def test_ttl_starts_fresh_after_trend_stops_deferring(self):
        rid = oq.enqueue(
            "BTCUSDC", "BUY", 1.0, {}, requested_price=100.0,
            failure_reason="trend_deferred", now=1000.0)
        transition = 1000.0 + 10 * 86400
        claimed = oq.claim([rid], now=transition)[0]
        oq.complete_claim(
            claimed, "failure", now=transition, failure_reason="cooldown")
        rec = oq.load_all()[0]
        self.assertEqual(rec["ttl_started_ts"], transition)
        self.assertFalse(oq.is_expired(rec, now=transition + 86400 - 1))
        self.assertTrue(oq.is_expired(rec, now=transition + 86400 + 1))

    def test_is_expired_max_attempts(self):
        oq.RETRY_MAX_ATTEMPTS = 3
        self.assertFalse(oq.is_expired({"created_ts": 1e9, "attempts": 2}, now=1e9))
        self.assertTrue(oq.is_expired({"created_ts": 1e9, "attempts": 3}, now=1e9))

    def test_corrupt_line_skipped(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, now=1000.0)
        with open(oq.QUEUE_FILE, "a") as f:
            f.write("{corrupt json\n")
        self.assertEqual(len(oq.load_all()), 1)   # linia corupta sarita


if __name__ == "__main__":
    unittest.main(verbosity=2)
