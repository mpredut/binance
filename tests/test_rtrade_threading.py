"""Caracterizare si regresii pentru managementul workerilor din rtrade."""
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
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
    bot.pair_store = SimpleNamespace(
        active=lambda _symbol: [],
        begin=lambda *_args, **_kwargs: None,
        checkpoint=lambda *_args, **_kwargs: None,
    )
    return bot


class RTradeThreadingTest(unittest.TestCase):
    def test_feature_flag_routes_to_single_coordinator_path(self):
        bot = _bot()
        with patch.object(rtrade, "RTRADE_PAIR_COORDINATOR_ENABLED", True), \
             patch.object(bot, "_run_coordinator_forever", return_value="coordinated") as run:
            self.assertEqual(bot.run(), "coordinated")
        run.assert_called_once_with()

    def test_coordinator_starts_multiple_independent_rounds_on_same_symbol(self):
        bot = _bot()
        coordinators = []

        class FakeCoordinator:
            def __init__(self, *_args, **kwargs):
                self.pair_id = f"pair-{len(coordinators) + 1}"
                self.start_side = kwargs.get("start_side")
                self.steps = []
                coordinators.append(self)

            def start(self, _price, pair_id=None):
                self.pair_id = pair_id or self.pair_id
                return SimpleNamespace(
                    terminal=False, pair_id=self.pair_id, phase="quoting",
                    reason=None)

            def step(self, now=None):
                self.steps.append(now)
                return SimpleNamespace(terminal=False)

        venue = SimpleNamespace(current_price=lambda: 100.0)
        with patch.object(rtrade, "_LivePairVenue", return_value=venue), \
             patch.object(rtrade, "PairCoordinator", FakeCoordinator), \
             patch.object(rtrade, "RTRADE_PAIR_MAX_ACTIVE_ROUNDS", 2), \
             patch.object(rtrade, "RTRADE_PAIR_START_INTERVAL_SEC", 8), \
             patch.object(rtrade, "RTRADE_PAIR_DIRECTIONS", ("BUY", "SELL")), \
             patch.object(rtrade, "RTRADE_PAIR_POLL_SEC", 1), \
             patch.object(rtrade, "_trend_too_strong", return_value=False), \
             patch.object(rtrade.time, "monotonic", side_effect=[0.0, 8.0, 16.0]), \
             patch.object(rtrade.time, "sleep",
                          side_effect=[None, None, KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                bot._run_coordinator_forever()

        self.assertEqual(len({c.pair_id for c in coordinators}), 2)
        self.assertTrue(all(len(c.pair_id) == 32 for c in coordinators))
        self.assertEqual([c.start_side for c in coordinators], ["BUY", "SELL"])
        self.assertEqual(coordinators[0].steps, [8.0, 16.0])
        self.assertEqual(coordinators[1].steps, [16.0])

    def test_coordinator_backs_off_each_side_after_generic_place_failure(self):
        bot = _bot()
        starts = []

        class FailingCoordinator:
            def __init__(self, *_args, **kwargs):
                self.start_side = kwargs["start_side"]

            def start(self, _price, pair_id=None):
                self.pair_id = pair_id or f"pair-{len(starts) + 1}"
                starts.append(self.start_side)
                return SimpleNamespace(
                    terminal=True,
                    pair_id=f"pair-{len(starts)}",
                    phase="failed",
                    reason=f"{self.start_side.lower()}_place_failed",
                )

        venue = SimpleNamespace(current_price=lambda: 100.0)
        with patch.object(rtrade, "_LivePairVenue", return_value=venue), \
             patch.object(rtrade, "PairCoordinator", FailingCoordinator), \
             patch.object(rtrade, "RTRADE_PAIR_MAX_ACTIVE_ROUNDS", 2), \
             patch.object(rtrade, "RTRADE_PAIR_START_INTERVAL_SEC", 8), \
             patch.object(rtrade, "RTRADE_PAIR_DIRECTIONS", ("BUY", "SELL")), \
             patch.object(rtrade, "RTRADE_PAIR_POLL_SEC", 1), \
             patch.object(rtrade, "RTRADE_PLACE_FAILURE_BACKOFF_SEC", 180), \
             patch.object(rtrade, "_trend_too_strong", return_value=False), \
             patch.object(rtrade.time, "monotonic", side_effect=[0.0, 8.0, 16.0]), \
             patch.object(rtrade.time, "sleep",
                          side_effect=[None, None, KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                bot._run_coordinator_forever()

        self.assertEqual(starts, ["BUY", "SELL"])

    def test_place_failure_backoff_preserves_funds_specific_duration(self):
        with patch.object(rtrade, "RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC", 181), \
             patch.object(rtrade, "RTRADE_PLACE_FAILURE_BACKOFF_SEC", 37):
            self.assertEqual(
                rtrade._place_failure_backoff("buy_insufficient_funds:USDC"),
                ("BUY", 181),
            )
            self.assertEqual(
                rtrade._place_failure_backoff("sell_place_failed"),
                ("SELL", 37),
            )
            self.assertEqual(rtrade._place_failure_backoff("other"), (None, 0.0))

    def test_live_pair_adapter_passes_pair_id_and_owns_retry(self):
        executor = SimpleNamespace(free_balance=lambda _asset: 1000.0)
        order = {"orderId": 7, "price": "99.36", "origQty": "0.8"}
        with patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
             patch.object(rtrade.mkt, "provider_by_name", return_value=executor), \
             patch.object(rtrade.mkt, "place", return_value=order) as place:
            venue = rtrade._LivePairVenue("TAOUSDC")
            ticket = venue.place_limit("BUY", 99.36, 1.0, "pair-1")

        self.assertEqual((ticket.order_id, ticket.qty, ticket.pair_id),
                         ("7", 0.8, "pair-1"))
        kwargs = place.call_args.kwargs
        self.assertEqual(kwargs["cooldown_pair_id"], "pair-1")
        self.assertTrue(kwargs["caller_owns_retry"])
        self.assertFalse(kwargs["force"])
        self.assertFalse(kwargs["smart"])
        self.assertTrue(kwargs["client_order_id"].startswith("SD_"))
        self.assertEqual(len(kwargs["client_order_id"]), 35)

    def test_live_pair_hard_stop_reconciles_and_uses_audited_market_exit(self):
        precision = SimpleNamespace(volume_decimals=3, order_min=0.001)
        executor = SimpleNamespace(
            name="Binance",
            free_balance=lambda _asset: 0.4,
            fee_cap_quantity=lambda *_args: 0.39,
            pair_precision=lambda _symbol: precision,
            preflight_order=lambda *args, **kwargs: None,
            submit_order=lambda *args, **kwargs: "M9")
        with patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
             patch.object(rtrade.mkt, "provider_by_name", return_value=executor), \
             patch.object(rtrade.mkt, "get_current_price", return_value=90.0), \
             patch.object(executor, "submit_order", wraps=executor.submit_order) as submit:
            venue = rtrade._LivePairVenue("TAOUSDC")
            ticket = venue.place_market_exit(
                "SELL", 0.4, "fast_fill_hard_stop", pair_id="pair-9")

        self.assertEqual((ticket.order_id, ticket.qty, ticket.pair_id),
                         ("M9", 0.39, "pair-9"))
        args, kwargs = submit.call_args
        self.assertEqual(args[:4], ("TAOUSDC", "SELL", 0.39, None))
        self.assertTrue(kwargs["market"])
        self.assertIn("rtrade:fast_fill_hard_stop:pair-9", kwargs["kind"])

    def test_live_pair_cancel_releases_only_its_cooldown_leg(self):
        executor = SimpleNamespace(
            cancel_order=lambda *_args: None,
            free_balance=lambda _asset: 1000.0)
        order = {"orderId": 7, "price": "99.36", "origQty": "1"}
        with patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
             patch.object(rtrade.mkt, "provider_by_name", return_value=executor), \
             patch.object(rtrade.mkt, "place", return_value=order), \
             patch("lock.trade_cooldown.release_pair_leg", return_value=True) as release:
            venue = rtrade._LivePairVenue("TAOUSDC")
            venue.place_limit("BUY", 99.36, 1.0, "pair-1")
            self.assertTrue(venue.cancel("7"))

        release.assert_called_once_with("TAOUSDC", "pair-1", "BUY")

    def test_live_pair_adapter_rejects_insufficient_asset_before_submit(self):
        executor = SimpleNamespace(free_balance=lambda _asset: 0.0)
        with patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
             patch.object(rtrade.mkt, "provider_by_name", return_value=executor), \
             patch.object(rtrade.mkt, "place") as place:
            venue = rtrade._LivePairVenue("TAOUSDC")
            buy = venue.place_limit("BUY", 100.0, 1.0, "pair-buy")
            sell = venue.place_limit("SELL", 101.0, 1.0, "pair-sell")

        self.assertIsNone(buy)
        self.assertIsNone(sell)
        self.assertEqual(venue.last_place_failure_reason("BUY"),
                         "buy_insufficient_funds:USDC")
        self.assertEqual(venue.last_place_failure_reason("SELL"),
                         "sell_insufficient_funds:TAO")
        place.assert_not_called()

    def test_live_pair_adapter_leaves_partial_balance_clamp_to_provider(self):
        executor = SimpleNamespace(free_balance=lambda _asset: 10.0)
        adjusted = {"orderId": 8, "price": "100", "origQty": "0.099"}
        with patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
             patch.object(rtrade.mkt, "provider_by_name", return_value=executor), \
             patch.object(rtrade.mkt, "place", return_value=adjusted) as place:
            venue = rtrade._LivePairVenue("TAOUSDC")
            ticket = venue.place_limit("BUY", 100.0, 1.0, "pair-buy")

        self.assertEqual(ticket.qty, 0.099)
        place.assert_called_once()

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
