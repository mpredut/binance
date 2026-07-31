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
        oq.RETRY_PRICE_TOL = 0.002
        oq.RETRY_DEDUP = True
        oq.RETRY_MAX_QUEUE = 500

    def test_success_removes_from_queue(self):
        oq.enqueue("BTCUSDC", "BUY", None, {"safeback_seconds": 9, "smart": False},
                   requested_price=63000.0, now=1000.0)   # BUY: pret curent <= cerut -> gate ok
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
        oq.enqueue("BTCUSDC", "BUY", None, {}, requested_price=100.0, now=1000.0)
        mkt = FakeMkt(succeed=False)   # price=100 default, BUY gate ok (100<=100)
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

    def test_price_gate_skips_unfavorable(self):
        # SELL cerut la 100; pretul curent 90 (sub) -> gardul opreste, ramane fara incercare
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=100.0, now=1000.0)
        mkt = FakeMkt(price=90.0, succeed=True)
        stats = worker.process_once(mkt, now=1000.0 + 400)
        self.assertEqual(stats["attempted"], 0)
        self.assertEqual(stats["skipped_price"], 1)
        self.assertEqual(len(mkt.calls), 0)          # NU s-a plasat nimic
        q = oq.load_all()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["attempts"], 0)        # nu se numara ca incercare

    def test_price_gate_allows_favorable(self):
        # SELL cerut la 100; pretul curent 101 (peste) -> gardul lasa sa treaca, se reia
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=100.0, now=1000.0)
        mkt = FakeMkt(price=101.0, succeed=True)
        stats = worker.process_once(mkt, now=1000.0 + 400)
        self.assertEqual(stats["succeeded"], 1)
        self.assertEqual(len(mkt.calls), 1)
        self.assertEqual(mkt.calls[0]["price"], 101.0)   # reluat la pret CURENT
        self.assertEqual(oq.load_all(), [])

    def test_removed_from_queue_before_place(self):
        # cerinta user: intrarea e SCOASA din coada INAINTE de plasare (nu poate fi reincercata
        # de nimeni cat timp e in curs). Verificam ca in timpul lui place(), coada e goala.
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        seen = {}

        class CheckMkt(FakeMkt):
            def place(self, symbol, side, price, qty, **kw):
                seen["in_queue_during_place"] = len(oq.load_all())
                return super().place(symbol, side, price, qty, **kw)

        mkt = CheckMkt(price=100.0, succeed=True)
        worker.process_once(mkt, now=1000.0 + 400)
        self.assertEqual(seen["in_queue_during_place"], 0)   # scoasa INAINTE de plasare
        self.assertEqual(oq.load_all(), [])                  # succes -> ramane scoasa

    def test_failure_reenqueues_preserving_age(self):
        oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=100.0, now=1000.0)
        mkt = FakeMkt(price=100.0, succeed=False)   # BUY gate ok (100<=100), dar esueaza
        worker.process_once(mkt, now=1000.0 + 400)
        q = oq.load_all()
        self.assertEqual(len(q), 1)                  # re-adaugata dupa esec
        self.assertEqual(q[0]["created_ts"], 1000.0) # vechimea PASTRATA (TTL nu se reseteaza)
        self.assertEqual(q[0]["attempts"], 1)        # attempts+1


if __name__ == "__main__":
    unittest.main(verbosity=2)
