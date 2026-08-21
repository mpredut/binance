"""Caracterizare si regresii pentru managementul workerilor din rtrade."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import rtrade


def _bot():
    bot = object.__new__(rtrade.TradingBot)
    bot.symbol = "TAOUSDC"
    bot.qty = 100.0
    bot.DEFAULT_ADJUSTMENT_PERCENT = 0.0064
    bot.filled_buy_price = 99.0
    bot.filled_sell_price = 101.0
    bot.buy_filled = False
    bot.sell_filled = False
    bot.lock = threading.Lock()
    return bot


class RTradeThreadingTest(unittest.TestCase):
    def test_pair_reuses_exactly_two_workers_between_rounds(self):
        bot = _bot()
        barrier = threading.Barrier(2)
        rounds = []

        def buy(_current, _filled):
            ident = threading.get_ident()
            barrier.wait(timeout=1.0)
            rounds[-1].append(ident)
            return 99.0

        def sell(_current, _filled):
            ident = threading.get_ident()
            barrier.wait(timeout=1.0)
            rounds[-1].append(ident)
            return 101.0

        bot.repetitive_buy = buy
        bot.repetitive_sell = sell
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="rtrade-test") as executor:
            for _ in range(3):
                rounds.append([])
                self.assertEqual(bot._run_pair(executor, 100.0), (99.0, 101.0))

        worker_sets = [set(ids) for ids in rounds]
        self.assertTrue(all(len(ids) == 2 for ids in worker_sets))
        self.assertTrue(all(ids == worker_sets[0] for ids in worker_sets[1:]))

    def test_worker_exception_propagates_to_owner(self):
        bot = _bot()

        def fail(_current, _filled):
            raise RuntimeError("buy worker failed")

        bot.repetitive_buy = fail
        bot.repetitive_sell = lambda _current, _filled: 101.0
        with ThreadPoolExecutor(max_workers=2) as executor:
            with self.assertRaisesRegex(RuntimeError, "buy worker failed"):
                bot._run_pair(executor, 100.0)

    def test_worker_exception_waits_for_other_side_before_propagating(self):
        bot = _bot()
        sell_started = threading.Event()
        release_sell = threading.Event()
        owner_finished = threading.Event()
        errors = []

        def fail(_current, _filled):
            raise RuntimeError("buy worker failed")

        def slow_sell(_current, _filled):
            sell_started.set()
            release_sell.wait(timeout=1.0)
            return 101.0

        def run_owner(executor):
            try:
                bot._run_pair(executor, 100.0)
            except Exception as exc:  # noqa: BLE001 - capturat pentru asertiune
                errors.append(exc)
            finally:
                owner_finished.set()

        bot.repetitive_buy = fail
        bot.repetitive_sell = slow_sell
        with ThreadPoolExecutor(max_workers=2) as executor:
            owner = threading.Thread(target=run_owner, args=(executor,))
            owner.start()
            self.assertTrue(sell_started.wait(timeout=1.0))
            self.assertFalse(
                owner_finished.wait(timeout=0.05),
                "ownerul nu trebuie sa porneasca alta runda cat SELL-ul vechi ruleaza",
            )
            release_sell.set()
            owner.join(timeout=1.0)

        self.assertFalse(owner.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), "buy worker failed")

    def test_cancel_fill_followup_uses_same_trend_policy(self):
        """Al saselea followup nu trebuie sa ocoleasca _followup_force."""
        bot = _bot()
        first_order = {"orderId": 7, "price": "101.0"}
        with patch.object(rtrade.api, "get_current_price", return_value=100.0), \
             patch.object(rtrade.api, "check_order_filled", side_effect=[False, True]), \
             patch.object(rtrade.api, "check_order_filled_by_time", return_value=None), \
             patch.object(rtrade.api, "cancel_order", return_value=False), \
             patch.object(rtrade.mkt, "place", side_effect=[first_order, {"orderId": 8}]) as place, \
             patch.object(rtrade, "_followup_force", return_value=False) as policy, \
             patch.object(rtrade.time, "sleep", return_value=None):
            result = bot.repetitive_sell(100.0, 100.0)

        self.assertEqual(result, 101.0)
        policy.assert_called_once_with("TAOUSDC", "BUY")
        self.assertFalse(place.call_args_list[1].kwargs["force"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
