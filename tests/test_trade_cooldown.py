import os, sys, time, threading, tempfile, unittest
import multiprocessing as mp
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lock import trade_cooldown as tc


def _proc_attempt(state_file, lock_file, q):
    # Runs in a spawned child and uses the same state/lock files.
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
        # Ceas controlat, nu sleep real: evită flake la ajustări de wall-clock/NTP
        # și testează exact semantica pragului (permis după expirare).
        with mock.patch("lock.cooldown.time.time") as now:
            now.return_value = 1_000.0
            self.assertTrue(tc.reserve_trade("BUY", "BTCUSDC", cooldown_sec=1)[0])
            self.assertFalse(tc.reserve_trade("SELL", "BTCUSDC", cooldown_sec=1)[0])
            now.return_value = 1_001.1
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
        # Spawn avoids forking the suite's background threads on Python 3.14 while
        # preserving the cross-process flock contract under test.
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        procs = [ctx.Process(target=_proc_attempt, args=(tc.STATE_FILE, tc.LOCK_FILE, q))
                 for _ in range(10)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=10)
        results = [q.get() for _ in range(10)]
        q.close()
        q.join_thread()
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

    # ─── pereche atomica BUY + SELL ──────────────────────────────────────────
    def test_pair_allows_one_buy_and_one_sell_inside_same_cooldown(self):
        with tc.trade_slot("BUY", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-1") as buy:
            self.assertTrue(buy.allowed)
            buy.commit(101)
        with tc.trade_slot("SELL", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-1") as sell:
            self.assertTrue(sell.allowed)
            sell.commit(102)

        state = tc._cooldown().get("TAOUSDC")
        self.assertCountEqual(state["group_committed"], ["BUY", "SELL"])
        self.assertEqual(state["group_results"]["BUY"]["binance_order_id"], 101)
        self.assertEqual(state["group_results"]["SELL"]["binance_order_id"], 102)

    def test_pair_blocks_duplicate_side_and_unrelated_group(self):
        with tc.trade_slot("BUY", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-1") as buy:
            self.assertTrue(buy.allowed)
            buy.commit(101)

        with tc.trade_slot("BUY", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-1") as duplicate:
            self.assertFalse(duplicate.allowed)
        with tc.trade_slot("SELL", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-2") as outsider:
            self.assertFalse(outsider.allowed)
        self.assertFalse(
            tc.reserve_trade("SELL", "TAOUSDC", cooldown_sec=180)[0],
            "o ordine fara pair_id nu trebuie sa ocoleasca perechea activa")

    def test_failed_second_leg_rolls_back_only_that_leg(self):
        with tc.trade_slot("BUY", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-1") as buy:
            buy.commit(101)
        with tc.trade_slot("SELL", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-1") as sell:
            self.assertTrue(sell.allowed)
            # fara commit -> rollback numai SELL

        state = tc._cooldown().get("TAOUSDC")
        self.assertEqual(state["group_members"], ["BUY"])
        self.assertEqual(state["group_committed"], ["BUY"])
        with tc.trade_slot("SELL", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-1") as retry:
            self.assertTrue(retry.allowed)

    def test_legacy_reservation_still_blocks_pair(self):
        self.assertTrue(tc.reserve_trade("BUY", "TAOUSDC", cooldown_sec=180)[0])
        with tc.trade_slot("SELL", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-1") as leg:
            self.assertFalse(leg.allowed)

    def test_canceled_pair_leg_can_be_replaced_without_opening_other_groups(self):
        with tc.trade_slot("SELL", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-1") as sell:
            sell.commit(102)
        self.assertTrue(tc.release_pair_leg("TAOUSDC", "pair-1", "SELL"))

        with tc.trade_slot("SELL", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-2") as outsider:
            self.assertFalse(outsider.allowed)
        with tc.trade_slot("SELL", "TAOUSDC", cooldown_sec=180,
                           pair_id="pair-1") as replacement:
            self.assertTrue(replacement.allowed)
            replacement.commit(103)


if __name__ == "__main__":
    unittest.main()
