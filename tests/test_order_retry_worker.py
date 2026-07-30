"""Teste pt order_retry_worker.process_once — logica pura de golire a cozii, cu un
`mkt` FAKE (fara retea). Coada izolata in tmp."""
import os
import sys
import time
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import order_retry as oq
import order_retry_worker as worker


class FakeMkt:
    def __init__(self, price=100.0, succeed=True):
        self.price = price
        self.succeed = succeed
        self.calls = []
    def get_current_price(self, symbol):
        return self.price
    def place(self, symbol, side, price, qty, **kw):
        self.calls.append({"symbol": symbol, "side": side, "price": price, "qty": qty, "kw": kw})
        return {"orderId": 1} if self.succeed else None


class ProcessOnceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        oq.QUEUE_FILE = os.path.join(self.tmp, "q.jsonl")
        oq.LOCK_FILE = os.path.join(self.tmp, "q.lock")
        oq.RETRY_ENABLED = True
        oq.RETRY_INTERVAL_SEC = 300.0
        oq.RETRY_TTL_SEC = 86400.0
        oq.RETRY_MAX_ATTEMPTS = 0

    def test_success_removes_from_queue(self):
        oq.enqueue("BTCUSDC", "BUY", None, {"safeback_seconds": 9, "smart": False}, now=1000.0)
        mkt = FakeMkt(price=63000.0, succeed=True)
        stats = worker.process_once(mkt, now=1000.0 + 400)   # due (>300s)
        self.assertEqual(stats["succeeded"], 1)
        self.assertEqual(oq.load_all(), [])                  # scos dupa succes
        # a fost reluat cu is_retry=True, la pret CURENT (63000), kwargs pastrate
        self.assertEqual(len(mkt.calls), 1)
        self.assertTrue(mkt.calls[0]["kw"]["is_retry"])
        self.assertEqual(mkt.calls[0]["price"], 63000.0)
        self.assertEqual(mkt.calls[0]["kw"]["smart"], False)

    def test_failure_keeps_and_increments_attempts(self):
        oq.enqueue("BTCUSDC", "BUY", None, {}, now=1000.0)
        mkt = FakeMkt(succeed=False)
        worker.process_once(mkt, now=1000.0 + 400)
        q = oq.load_all()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["attempts"], 1)
        self.assertAlmostEqual(q[0]["last_attempt_ts"], 1000.0 + 400)

    def test_not_due_skipped(self):
        oq.enqueue("BTCUSDC", "BUY", None, {}, now=1000.0)
        mkt = FakeMkt(succeed=True)
        stats = worker.process_once(mkt, now=1000.0 + 100)   # < interval 300 -> nu e scadent
        self.assertEqual(stats["attempted"], 0)
        self.assertEqual(len(oq.load_all()), 1)              # ramane

    def test_expired_dropped_and_alerted(self):
        oq.enqueue("BTCUSDC", "BUY", None, {}, now=1000.0)
        mkt = FakeMkt(succeed=True)
        alerts = []
        orig = worker.alert.notify
        worker.alert.notify = lambda **kw: alerts.append(kw)
        try:
            stats = worker.process_once(mkt, now=1000.0 + 86400 + 10)   # peste TTL
        finally:
            worker.alert.notify = orig
        self.assertEqual(stats["expired"], 1)
        self.assertEqual(oq.load_all(), [])          # scos
        self.assertEqual(len(mkt.calls), 0)          # NU reincercat (expirat)
        self.assertEqual(len(alerts), 1)             # alerta de renuntare

    def test_price_none_leaves_in_queue(self):
        oq.enqueue("BTCUSDC", "BUY", None, {}, now=1000.0)
        mkt = FakeMkt(price=None, succeed=True)
        stats = worker.process_once(mkt, now=1000.0 + 400)
        self.assertEqual(stats["attempted"], 0)
        self.assertEqual(len(oq.load_all()), 1)      # pret indisponibil -> ramane


if __name__ == "__main__":
    unittest.main(verbosity=2)
