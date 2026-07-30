"""Teste pt order_retry.py — store-ul outbox al cozii de re-plasare (enqueue/load/rewrite
+ logica is_due/is_expired). Fisiere izolate in tmp (nu atinge cachedb/ real)."""
import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import order_retry as oq


class OrderRetryStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        oq.QUEUE_FILE = os.path.join(self.tmp, "order_retry_queue.jsonl")
        oq.LOCK_FILE = os.path.join(self.tmp, "order_retry_queue.lock")
        oq.RETRY_ENABLED = True
        oq.RETRY_INTERVAL_SEC = 300.0
        oq.RETRY_TTL_SEC = 86400.0
        oq.RETRY_MAX_ATTEMPTS = 0

    def test_enqueue_and_load(self):
        i = oq.enqueue("BTCUSDC", "BUY", None,
                       {"safeback_seconds": 999, "force": False, "smart": False}, now=1000.0)
        self.assertIsNotNone(i)
        items = oq.load_all()
        self.assertEqual(len(items), 1)
        r = items[0]
        self.assertEqual(r["symbol"], "BTCUSDC")
        self.assertEqual(r["side"], "BUY")
        self.assertIsNone(r["qty"])
        self.assertEqual(r["place_kwargs"]["safeback_seconds"], 999)
        self.assertEqual(r["attempts"], 0)
        self.assertNotIn("price", r)   # pretul NU se salveaza (recalculat la retry)

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
        oq.rewrite([items[1]])   # pastreaza doar al doilea
        rem = oq.load_all()
        self.assertEqual(len(rem), 1)
        self.assertEqual(rem[0]["symbol"], "TAOUSDC")

    def test_is_due(self):
        rec = {"last_attempt_ts": 1000.0}
        self.assertFalse(oq.is_due(rec, now=1000.0 + 100))   # < interval 300
        self.assertTrue(oq.is_due(rec, now=1000.0 + 300))    # >= interval

    def test_is_expired_ttl(self):
        rec = {"created_ts": 1000.0, "attempts": 0}
        self.assertFalse(oq.is_expired(rec, now=1000.0 + 86400 - 1))
        self.assertTrue(oq.is_expired(rec, now=1000.0 + 86400 + 1))

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
