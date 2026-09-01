"""Characterisation and regressions for worker management in rtrade."""
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import rtrade
from providers.strategy_executor import (
    OrderReconciliationCapabilities,
    OrderStatus,
)
from rtrade_pair_store import RTradePairStore, rtrade_client_order_id


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
        checkpoint_many=lambda *_args, **_kwargs: None,
    )
    return bot


class RTradeThreadingTest(unittest.TestCase):
    def test_startup_anuleaza_automat_ordinul_rtrade_orfan(self):
        bot = _bot()
        canceled = []
        executor = SimpleNamespace(
            open_orders=Mock(side_effect=[[
                {"orderId": "91", "clientOrderId": "RT_orphan"}
            ], []]),
            cancel_order=lambda symbol, order_id: canceled.append(
                (symbol, order_id)),
        )
        venue = SimpleNamespace(current_price=lambda: None, executor=executor)
        with patch.object(rtrade, "_LivePairVenue", return_value=venue), \
             patch.object(rtrade.time, "sleep", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                bot._run_coordinator_forever()

        self.assertEqual(canceled, [("TAOUSDC", "91")])
        self.assertEqual(executor.open_orders.call_count, 2)

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

        venue = SimpleNamespace(
            current_price=lambda: 100.0,
            executor=SimpleNamespace(open_orders=lambda _symbol: []))
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

        venue = SimpleNamespace(
            current_price=lambda: 100.0,
            executor=SimpleNamespace(open_orders=lambda _symbol: []))
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
        self.assertTrue(kwargs["client_order_id"].startswith("RT_"))
        self.assertEqual(len(kwargs["client_order_id"]), 35)

    def test_live_pair_limit_persists_canonical_intent_and_venue_values(self):
        executor = SimpleNamespace(free_balance=lambda _asset: 1000.0)
        order = {"orderId": 7, "price": "99.25", "origQty": "0.8"}
        with tempfile.TemporaryDirectory(prefix="rtrade-store-") as root:
            store = RTradePairStore(os.path.join(root, "pairs.json"))
            with patch.dict(os.environ, {"EXECUTION_AUDIT_DIR": root}), \
                 patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
                 patch.object(rtrade.mkt, "provider_by_name", return_value=executor), \
                 patch.object(rtrade.mkt, "place", return_value=order):
                venue = rtrade._LivePairVenue("TAOUSDC", pair_store=store)
                ticket = venue.place_limit("BUY", 99.36, 1.0, "pair-1")

            intent = store.active("TAOUSDC")[0]["intents"]["limit:BUY"]
        self.assertEqual(ticket.price, 99.25)
        self.assertEqual(ticket.qty, 0.8)
        self.assertEqual(intent["requested_price"], 99.36)
        self.assertEqual(intent["submitted_price"], 99.25)
        self.assertEqual(intent["submitted_qty"], 0.8)
        self.assertEqual(intent["order_id"], "7")

    def test_live_pair_guard_refusal_does_not_run_response_loss_recovery(self):
        class Executor:
            name = "Binance"

            @staticmethod
            def free_balance(_asset):
                return 1000.0

            @staticmethod
            def order_by_client_id(*_args, **_kwargs):
                raise AssertionError("pre-submit refusal must not query venue")

        def refuse(*_args, **kwargs):
            kwargs["_outcome_context"].update(
                accepted=False, reason="trend_deferred")
            return None

        with tempfile.TemporaryDirectory(prefix="rtrade-refusal-") as root:
            store = RTradePairStore(os.path.join(root, "pairs.json"))
            with patch.dict(os.environ, {"EXECUTION_AUDIT_DIR": root}), \
                 patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
                 patch.object(rtrade.mkt, "provider_by_name", return_value=Executor()), \
                 patch.object(rtrade.mkt, "place", side_effect=refuse) as place:
                venue = rtrade._LivePairVenue("TAOUSDC", pair_store=store)
                ticket = venue.place_limit("SELL", 101.0, 1.0, "pair-1")

            intent = store.active("TAOUSDC")[0]["intents"]["limit:SELL"]

        self.assertIsNone(ticket)
        place.assert_called_once()
        self.assertEqual(intent["refusal_reason"], "trend_deferred")
        self.assertEqual(intent["submit_status"], "refused_before_submit")
        self.assertNotIn("order_id", intent)
        self.assertFalse(venue.recovery_blocked)

    def test_live_pair_recovers_lost_submit_response_without_resubmit(self):
        class Executor:
            name = "Binance"

            @staticmethod
            def free_balance(_asset):
                return 1000.0

            @staticmethod
            def reconciliation_capabilities():
                return OrderReconciliationCapabilities(
                    lookup_by_client_order_id=True,
                    status_by_order_id=True,
                    cancel_by_order_id=True,
                    list_open_orders=True,
                )

            @staticmethod
            def order_by_client_id(_symbol, _client_id):
                return {
                    "orderId": "88", "status": "NEW",
                    "price": "99.25", "origQty": "0.8",
                }

            @staticmethod
            def order_status(_symbol, _order_id):
                return OrderStatus("open", 0.0, 0.0, 0.0)

        with tempfile.TemporaryDirectory(prefix="rtrade-recover-") as root:
            store = RTradePairStore(os.path.join(root, "pairs.json"))
            client_id = rtrade_client_order_id("pair-1", "BUY", "limit")
            store.intent(
                "pair-1", "BUY", 99.36, 1.0, client_id,
                kind="limit", symbol="TAOUSDC", start_side="BUY")
            record = store.active("TAOUSDC")[0]
            stored = record["intents"]["limit:BUY"]
            with patch.dict(os.environ, {"EXECUTION_AUDIT_DIR": root}), \
                 patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
                 patch.object(rtrade.mkt, "provider_by_name", return_value=Executor()), \
                 patch.object(rtrade.mkt, "place") as place:
                venue = rtrade._LivePairVenue("TAOUSDC", pair_store=store)
                ticket, snapshot = venue.recover_intent(record, stored)

            canonical = store.active("TAOUSDC")[0]["intents"]["limit:BUY"]
        place.assert_not_called()
        self.assertEqual(ticket.order_id, "88")
        self.assertEqual(snapshot.status, "open")
        self.assertEqual(canonical["order_id"], "88")

    def test_place_limit_closes_live_response_loss_gap_without_poll_loop(self):
        class Executor:
            name = "Binance"

            @staticmethod
            def free_balance(_asset):
                return 1000.0

            @staticmethod
            def reconciliation_capabilities():
                return OrderReconciliationCapabilities(
                    lookup_by_client_order_id=True,
                    status_by_order_id=True,
                    cancel_by_order_id=True,
                    list_open_orders=True,
                )

            @staticmethod
            def order_by_client_id(_symbol, _client_id):
                return {
                    "orderId": "88", "status": "NEW",
                    "price": "99.25", "origQty": "0.8",
                }

            @staticmethod
            def order_status(_symbol, _order_id):
                return OrderStatus("open", 0.0, 0.0, 0.0)

        with tempfile.TemporaryDirectory(prefix="rtrade-live-loss-") as root:
            store = RTradePairStore(os.path.join(root, "pairs.json"))
            with patch.dict(os.environ, {"EXECUTION_AUDIT_DIR": root}), \
                 patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
                 patch.object(rtrade.mkt, "provider_by_name", return_value=Executor()), \
                 patch.object(rtrade.mkt, "place", return_value=None) as place:
                venue = rtrade._LivePairVenue("TAOUSDC", pair_store=store)
                ticket = venue.place_limit("BUY", 99.36, 1.0, "pair-1")

            canonical = store.active("TAOUSDC")[0]["intents"]["limit:BUY"]
        place.assert_called_once()
        self.assertEqual(ticket.order_id, "88")
        self.assertEqual(canonical["order_id"], "88")
        self.assertFalse(venue.recovery_blocked)

    def test_place_limit_uses_at_most_second_idempotent_submit_after_absence(self):
        class Executor:
            name = "Binance"

            @staticmethod
            def free_balance(_asset):
                return 1000.0

            @staticmethod
            def reconciliation_capabilities():
                return OrderReconciliationCapabilities(
                    lookup_by_client_order_id=True,
                    status_by_order_id=True,
                    cancel_by_order_id=True,
                    list_open_orders=True,
                )

            @staticmethod
            def order_by_client_id(_symbol, _client_id):
                return None

            @staticmethod
            def order_status(_symbol, _order_id):
                return OrderStatus("open", 0.0, 0.0, 0.0)

        accepted = {"orderId": "90", "price": "99.2", "origQty": "0.9"}
        with tempfile.TemporaryDirectory(prefix="rtrade-live-retry-") as root:
            store = RTradePairStore(os.path.join(root, "pairs.json"))
            with patch.dict(os.environ, {"EXECUTION_AUDIT_DIR": root}), \
                 patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
                 patch.object(rtrade.mkt, "provider_by_name", return_value=Executor()), \
                 patch.object(rtrade.mkt, "place", side_effect=[None, accepted]) as place:
                venue = rtrade._LivePairVenue("TAOUSDC", pair_store=store)
                ticket = venue.place_limit("BUY", 99.36, 1.0, "pair-1")

            canonical = store.active("TAOUSDC")[0]["intents"]["limit:BUY"]
        self.assertEqual(place.call_count, 2)
        client_ids = [call.kwargs["client_order_id"] for call in place.call_args_list]
        self.assertEqual(client_ids[0], client_ids[1])
        self.assertEqual(ticket.order_id, "90")
        self.assertEqual(canonical["attempt"], 2)

    def test_live_pair_resubmits_once_only_after_confirmed_absence(self):
        class Executor:
            name = "Binance"

            @staticmethod
            def free_balance(_asset):
                return 1000.0

            @staticmethod
            def reconciliation_capabilities():
                return OrderReconciliationCapabilities(
                    lookup_by_client_order_id=True,
                    status_by_order_id=True,
                    cancel_by_order_id=True,
                    list_open_orders=True,
                )

            @staticmethod
            def order_by_client_id(_symbol, _client_id):
                return None

            @staticmethod
            def order_status(_symbol, _order_id):
                return OrderStatus("open", 0.0, 0.0, 0.0)

        with tempfile.TemporaryDirectory(prefix="rtrade-retry-") as root:
            store = RTradePairStore(os.path.join(root, "pairs.json"))
            client_id = rtrade_client_order_id("pair-1", "BUY", "limit")
            store.intent(
                "pair-1", "BUY", 99.36, 1.0, client_id,
                kind="limit", symbol="TAOUSDC", start_side="BUY")
            record = store.active("TAOUSDC")[0]
            stored = record["intents"]["limit:BUY"]
            accepted = {"orderId": "89", "price": "99.3", "origQty": "0.9"}
            with patch.dict(os.environ, {"EXECUTION_AUDIT_DIR": root}), \
                 patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
                 patch.object(rtrade.mkt, "provider_by_name", return_value=Executor()), \
                 patch.object(rtrade.mkt, "place", return_value=accepted) as place:
                venue = rtrade._LivePairVenue("TAOUSDC", pair_store=store)
                ticket, snapshot = venue.recover_intent(record, stored)

            canonical = store.active("TAOUSDC")[0]["intents"]["limit:BUY"]
        place.assert_called_once()
        self.assertEqual(ticket.order_id, "89")
        self.assertEqual(snapshot.status, "open")
        self.assertEqual(canonical["attempt"], 2)
        self.assertEqual(canonical["client_order_id"], client_id)

    def test_live_pair_lookup_error_blocks_recovery_without_submit(self):
        class Executor:
            name = "Binance"

            @staticmethod
            def free_balance(_asset):
                return 1000.0

            @staticmethod
            def reconciliation_capabilities():
                return OrderReconciliationCapabilities(
                    lookup_by_client_order_id=True,
                    status_by_order_id=True,
                    cancel_by_order_id=True,
                    list_open_orders=True,
                )

            @staticmethod
            def order_by_client_id(_symbol, _client_id):
                raise TimeoutError("lookup unavailable")

        with tempfile.TemporaryDirectory(prefix="rtrade-ambiguous-") as root:
            store = RTradePairStore(os.path.join(root, "pairs.json"))
            client_id = rtrade_client_order_id("pair-1", "BUY", "limit")
            store.intent(
                "pair-1", "BUY", 99.36, 1.0, client_id,
                kind="limit", symbol="TAOUSDC", start_side="BUY")
            record = store.active("TAOUSDC")[0]
            stored = record["intents"]["limit:BUY"]
            with patch.dict(os.environ, {"EXECUTION_AUDIT_DIR": root}), \
                 patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
                 patch.object(rtrade.mkt, "provider_by_name", return_value=Executor()), \
                 patch.object(rtrade.mkt, "place") as place:
                venue = rtrade._LivePairVenue("TAOUSDC", pair_store=store)
                with self.assertRaisesRegex(RuntimeError, "ambigua"):
                    venue.recover_intent(record, stored)

            canonical = store.active("TAOUSDC")[0]["intents"]["limit:BUY"]
        place.assert_not_called()
        self.assertIn("lookup_error", canonical)

    def test_place_limit_lookup_error_blocks_new_rounds_and_keeps_intent(self):
        class Executor:
            name = "Binance"

            @staticmethod
            def free_balance(_asset):
                return 1000.0

            @staticmethod
            def reconciliation_capabilities():
                return OrderReconciliationCapabilities(
                    lookup_by_client_order_id=True,
                    status_by_order_id=True,
                    cancel_by_order_id=True,
                    list_open_orders=True,
                )

            @staticmethod
            def order_by_client_id(_symbol, _client_id):
                raise TimeoutError("lookup unavailable")

        with tempfile.TemporaryDirectory(prefix="rtrade-live-ambiguous-") as root:
            store = RTradePairStore(os.path.join(root, "pairs.json"))
            with patch.dict(os.environ, {"EXECUTION_AUDIT_DIR": root}), \
                 patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
                 patch.object(rtrade.mkt, "provider_by_name", return_value=Executor()), \
                 patch.object(rtrade.mkt, "place", return_value=None) as place:
                venue = rtrade._LivePairVenue("TAOUSDC", pair_store=store)
                with self.assertRaisesRegex(RuntimeError, "ambigua"):
                    venue.place_limit("BUY", 99.36, 1.0, "pair-1")

            canonical = store.active("TAOUSDC")[0]["intents"]["limit:BUY"]
        place.assert_called_once()
        self.assertTrue(venue.recovery_blocked)
        self.assertIn("lookup_error", canonical)

    def test_live_pair_hard_stop_reconciles_and_uses_audited_market_exit(self):
        precision = SimpleNamespace(volume_decimals=3, order_min=0.001)
        executor = SimpleNamespace(
            name="Binance",
            free_balance=lambda _asset: 0.4,
            fee_cap_quantity=lambda *_args: 0.39,
            pair_precision=lambda _symbol: precision,
            preflight_order=lambda *args, **kwargs: None,
            submit_order=lambda *args, **kwargs: "M9")
        with tempfile.TemporaryDirectory(prefix="rtrade-test-audit-") as audit_dir:
            with patch.dict(os.environ, {"EXECUTION_AUDIT_DIR": audit_dir}), \
                 patch.object(rtrade.mkt, "provider_name_for", return_value="Binance"), \
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
                "the owner must not start another round while the old SELL is running",
            )
            release_sell.set()
            owner.join(timeout=1.0)

        self.assertFalse(owner.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertRegex(str(errors[0]), "buy worker failed")

    def test_cancel_fill_followup_uses_same_trend_policy(self):
        """The sixth follow-up must not bypass _followup_force."""
        bot = _bot()
        first_order = {"orderId": 7, "price": "101.0"}
        with patch.object(rtrade.api, "get_current_price", return_value=100.0), \
             patch.object(rtrade, "_order_fully_filled", side_effect=[False, True]), \
             patch.object(rtrade.mkt, "latest_fill_price", return_value=None), \
             patch.object(rtrade, "_cancel_order_confirmed", return_value=False), \
             patch.object(rtrade.mkt, "place", side_effect=[first_order, {"orderId": 8}]) as place, \
             patch.object(rtrade, "_followup_force", return_value=False) as policy, \
             patch.object(rtrade.time, "sleep", return_value=None):
            result = bot.repetitive_sell(100.0, 100.0)

        self.assertEqual(result, 101.0)
        policy.assert_called_once_with("TAOUSDC", "BUY")
        self.assertFalse(place.call_args_list[1].kwargs["force"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
