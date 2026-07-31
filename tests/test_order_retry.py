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
        oq.RETRY_PRICE_TOL = 0.002
        oq.RETRY_DEDUP = True
        oq.RETRY_DEDUP_PRICE_TOL = 0.003
        oq.RETRY_MAX_QUEUE = 500

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
        self.assertNotIn("price", r)   # pretul NU se salveaza ca valoare de trimis

    def test_enqueue_captures_price_intent(self):
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, ref_price=62950.0,
                   now=1000.0)
        r = oq.load_all()[0]
        self.assertEqual(r["requested_price"], 63000.0)
        self.assertEqual(r["ref_price"], 62950.0)

    def test_dedup_skips_duplicate_intent(self):
        a = oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, now=1000.0)
        # aceeasi intentie (pret in banda 0.3%) -> nu dubleaza, intoarce id-ul existent
        b = oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63100.0, now=1001.0)
        self.assertEqual(a, b)
        self.assertEqual(len(oq.load_all()), 1)

    def test_dedup_different_price_band_kept(self):
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, now=1000.0)
        # pret cerut mult diferit (>0.3%) -> intentie distincta, se pastreaza
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=70000.0, now=1001.0)
        self.assertEqual(len(oq.load_all()), 2)

    def test_dedup_off_appends_always(self):
        oq.RETRY_DEDUP = False
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, now=1000.0)
        oq.enqueue("BTCUSDC", "SELL", 1.0, {}, requested_price=63000.0, now=1001.0)
        self.assertEqual(len(oq.load_all()), 2)

    def test_max_queue_cap(self):
        oq.RETRY_MAX_QUEUE = 2
        oq.RETRY_DEDUP = False   # ca sa nu se comprime prin dedup
        self.assertIsNotNone(oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=1.0))
        self.assertIsNotNone(oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=2.0))
        self.assertIsNone(oq.enqueue("BTCUSDC", "BUY", 1.0, {}, requested_price=3.0))  # plin
        self.assertEqual(len(oq.load_all()), 2)

    def test_price_gate_sell(self):
        rec = {"side": "SELL", "requested_price": 100.0}
        self.assertTrue(oq.price_gate_ok(rec, 100.0))         # egal -> ok
        self.assertTrue(oq.price_gate_ok(rec, 101.0))         # mai sus -> ok (vinzi mai bine)
        self.assertTrue(oq.price_gate_ok(rec, 99.9))          # in toleranta 0.2%
        self.assertFalse(oq.price_gate_ok(rec, 95.0))         # mult sub -> asteapta
        self.assertFalse(oq.price_gate_ok(rec, None))         # fara pret -> nu decidem

    def test_price_gate_buy(self):
        rec = {"side": "BUY", "requested_price": 100.0}
        self.assertTrue(oq.price_gate_ok(rec, 100.0))
        self.assertTrue(oq.price_gate_ok(rec, 99.0))          # mai jos -> ok (cumperi mai ieftin)
        self.assertFalse(oq.price_gate_ok(rec, 105.0))        # mult peste -> asteapta

    def test_price_gate_no_intent_skips(self):
        # fara requested_price capturat (intrare veche/anormala) -> conservator, NU reia orb
        self.assertFalse(oq.price_gate_ok({"side": "SELL"}, 50.0))
        self.assertFalse(oq.price_gate_ok({"side": "BUY", "requested_price": 0}, 50.0))

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
