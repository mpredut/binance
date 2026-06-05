import os, sys, time, threading, tempfile, unittest
import multiprocessing as mp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import trade_cooldown as tc


def _proc_attempt(state_file, lock_file, q):
    # rulează în proces copil (fork): folosește aceleași fișiere de stare/lock
    tc.STATE_FILE = state_file
    tc.LOCK_FILE = lock_file
    ok, _ = tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)
    q.put(ok)


class TestTradeCooldown(unittest.TestCase):
    def setUp(self):
        # state/lock izolate per test
        self.tmp = tempfile.mkdtemp()
        tc.STATE_FILE = os.path.join(self.tmp, "trade_cooldown.json")
        tc.LOCK_FILE = os.path.join(self.tmp, "trade_cooldown.lock")

    def test_first_allowed_second_blocked(self):
        ok, _ = tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)
        self.assertTrue(ok)
        ok2, last = tc.reserve_trade("SELL", "BTCUSDC", cooldown_sec=180)
        self.assertFalse(ok2)                       # < 3 min → blocat
        self.assertEqual(last["side"], "BUY")

    def test_per_symbol_independent(self):
        self.assertTrue(tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)[0])
        # alt simbol NU e blocat de cooldown-ul BTC
        self.assertTrue(tc.reserve_trade("BUY", "TAOUSDC", cooldown_sec=180)[0])

    def test_allowed_after_cooldown(self):
        self.assertTrue(tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=1)[0])
        self.assertFalse(tc.reserve_trade("SELL", "BTCUSDC", cooldown_sec=1)[0])
        time.sleep(1.1)
        self.assertTrue(tc.reserve_trade("SELL", "BTCUSDC", cooldown_sec=1)[0])

    def test_release_unblocks(self):
        self.assertTrue(tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)[0])
        tc.release_trade("BTCUSDC")                 # ordin eșuat → eliberat
        self.assertTrue(tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)[0])  # din nou permis

    def test_update_binance_order_id(self):
        tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)
        tc.update_binance_order_id("BTCUSDC", 12345)
        self.assertIn("12345", tc.describe_last_trade("BTCUSDC"))

    def test_concurrent_threads_single_winner(self):
        # 20 de thread-uri lansează simultan pe ACELAȘI simbol → exact UNUL trece
        results = []
        barrier = threading.Barrier(20)

        def attempt():
            barrier.wait()                          # pornesc toate odată
            ok, _ = tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)
            results.append(ok)

        threads = [threading.Thread(target=attempt) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(1 for r in results if r), 1)   # un singur câștigător

    def test_concurrent_processes_single_winner(self):
        # 10 PROCESE (fork) lansează simultan pe ACELAȘI simbol → exact UNUL trece.
        # Dovedește mutual-exclusion-ul fcntl.flock cross-PROCES (nu doar cross-thread).
        ctx = mp.get_context("fork")
        q = ctx.Queue()
        procs = [ctx.Process(target=_proc_attempt, args=(tc.STATE_FILE, tc.LOCK_FILE, q))
                 for _ in range(10)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=10)
        results = [q.get() for _ in range(10)]
        self.assertEqual(sum(1 for r in results if r), 1)   # un singur câștigător

    # ─── RAII / trade_slot ────────────────────────────────────────────────────
    def test_slot_commit_keeps_reservation(self):
        with tc.trade_slot("BUY", "BTCUSDC", cooldown_sec=180) as slot:
            self.assertTrue(slot.allowed)
            slot.commit(999)                         # ordin plasat
        # commit → rezervarea RĂMÂNE → al doilea e blocat
        self.assertFalse(tc.reserve_trade("SELL", "BTCUSDC", cooldown_sec=180)[0])

    def test_slot_no_commit_auto_releases(self):
        with tc.trade_slot("BUY", "BTCUSDC", cooldown_sec=180) as slot:
            self.assertTrue(slot.allowed)
            # NU facem commit (ca și cum ordinul a eșuat / am uitat)
        # auto-release la ieșire → din nou permis
        self.assertTrue(tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)[0])

    def test_slot_exception_auto_releases(self):
        with self.assertRaises(RuntimeError):
            with tc.trade_slot("BUY", "BTCUSDC", cooldown_sec=180) as slot:
                self.assertTrue(slot.allowed)
                raise RuntimeError("plasare a crăpat")   # excepție → rollback automat
        self.assertTrue(tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)[0])

    def test_slot_blocked_does_not_release_existing(self):
        # primul rezervă; al doilea slot e blocked → la ieșire NU trebuie să șteargă
        # rezervarea primului
        self.assertTrue(tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)[0])
        with tc.trade_slot("SELL", "BTCUSDC", cooldown_sec=180) as slot:
            self.assertFalse(slot.allowed)
        # rezervarea inițială încă activă
        self.assertFalse(tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)[0])

    def test_get_last_trade_age(self):
        self.assertIsNone(tc.get_last_trade_age("BTCUSDC"))
        tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=180)
        age = tc.get_last_trade_age("BTCUSDC")
        self.assertIsNotNone(age)
        self.assertLess(age, 5)


if __name__ == "__main__":
    unittest.main()
