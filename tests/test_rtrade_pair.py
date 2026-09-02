import unittest

from strategies.rtrade_pair import (
    OrderSnapshot,
    OrderTicket,
    PairCoordinator,
    PairPolicy,
    anchored_exit_price,
    quote_prices,
)


class FakeVenue:
    def __init__(self, current=100.0, fail_side=None):
        self.current = current
        self.fail_side = fail_side
        self.orders = []
        self.statuses = {}
        self.canceled = []
        self.market_calls = []
        self.fill_on_cancel = {}
        self.cancel_fail = set()
        self.failure_reasons = {}
        self.allow_market = True

    def current_price(self):
        return self.current

    def place_limit(self, side, price, qty, pair_id):
        if side.upper() == self.fail_side:
            return None
        ticket = OrderTicket(
            order_id=f"L{len(self.orders) + 1}", side=side.upper(),
            price=price, qty=qty)
        self.orders.append((ticket, pair_id))
        self.statuses[ticket.order_id] = OrderSnapshot()
        return ticket

    def last_place_failure_reason(self, side):
        return self.failure_reasons.get(side.upper())

    def place_market_exit(self, side, qty, reason, pair_id=None):
        ticket = OrderTicket(
            order_id=f"M{len(self.orders) + 1}", side=side.upper(),
            price=self.current, qty=qty)
        self.orders.append((ticket, pair_id or "market"))
        self.market_calls.append((side.upper(), qty, reason))
        self.statuses[ticket.order_id] = OrderSnapshot(
            status="closed", filled_qty=qty, cost=qty * self.current, fee=0.1)
        return ticket

    def market_exit_allowed(self, exposure_side, loss_fraction, reason):
        return self.allow_market

    def order_status(self, order_id):
        return self.statuses[order_id]

    def cancel(self, order_id):
        self.canceled.append(order_id)
        if order_id in self.cancel_fail:
            return False
        if order_id in self.fill_on_cancel:
            qty, price = self.fill_on_cancel.pop(order_id)
            self.statuses[order_id] = OrderSnapshot(
                status="closed", filled_qty=qty, cost=qty * price, fee=0.1)
            return True
        old = self.statuses[order_id]
        self.statuses[order_id] = OrderSnapshot(
            status="canceled", filled_qty=old.filled_qty,
            cost=old.cost, fee=old.fee)
        return True

    def fill(self, order_id, qty, price, *, status="closed", fee=0.1):
        self.statuses[order_id] = OrderSnapshot(
            status=status, filled_qty=qty, cost=qty * price, fee=fee)


def _coordinator(venue, *, start_side="BUY", **policy_overrides):
    policy = PairPolicy(adjustment_fraction=0.0064, **policy_overrides)
    return PairCoordinator(
        venue, qty=1.0, policy=policy, start_side=start_side,
        clock=lambda: 0.0, sleeper=lambda _seconds: None,
        pair_id_factory=lambda: "pair-1")


class PricePolicyTest(unittest.TestCase):
    def test_quotes_share_one_midpoint(self):
        self.assertEqual(quote_prices(100.0, 0.0064), (99.36, 100.64))

    def test_long_exit_never_follows_market_below_cost_plus_edge(self):
        target = anchored_exit_price("SELL", 100.0, 90.0, 0.0064, 0.0115)
        self.assertEqual(target, 101.1634)
        self.assertGreater(target, 100.0)

    def test_sold_exit_never_chases_market_above_profitable_buyback(self):
        target = anchored_exit_price("BUY", 100.0, 110.0, 0.0064, 0.0115)
        self.assertEqual(target, 98.85)
        self.assertLess(target, 100.0)

    def test_nonfinite_policy_and_quantity_fail_closed(self):
        with self.assertRaises(ValueError):
            PairPolicy(adjustment_fraction=float("nan"))
        with self.assertRaises(ValueError):
            PairCoordinator(FakeVenue(), qty=float("inf"),
                            policy=PairPolicy(adjustment_fraction=0.0064))


class PairCoordinatorTest(unittest.TestCase):
    def test_places_both_legs_under_same_pair_id(self):
        venue = FakeVenue()
        outcome = _coordinator(venue).start(mid=100.0)

        self.assertEqual(outcome.phase, "quoting")
        self.assertEqual(
            [(t.side, t.price, pair) for t, pair in venue.orders],
            [("BUY", 99.36, "pair-1"), ("SELL", 100.64, "pair-1")])

    def test_second_leg_failure_cancels_first_and_fails_closed(self):
        venue = FakeVenue(fail_side="SELL")
        outcome = _coordinator(venue).start(mid=100.0)

        self.assertTrue(outcome.terminal)
        self.assertEqual(outcome.reason, "sell_place_failed")
        self.assertEqual(venue.canceled, ["L1"])

    def test_sell_first_places_both_legs_in_reverse_order(self):
        venue = FakeVenue()
        outcome = _coordinator(venue, start_side="SELL").start(mid=100.0)

        self.assertEqual(outcome.phase, "quoting")
        self.assertEqual(
            [(t.side, t.price, pair) for t, pair in venue.orders],
            [("SELL", 100.64, "pair-1"), ("BUY", 99.36, "pair-1")])

    def test_sell_first_failure_does_not_attempt_buy(self):
        venue = FakeVenue(fail_side="SELL")
        outcome = _coordinator(venue, start_side="SELL").start(mid=100.0)

        self.assertTrue(outcome.terminal)
        self.assertEqual(outcome.reason, "sell_place_failed")
        self.assertEqual(venue.orders, [])

    def test_venue_specific_insufficient_funds_reason_is_preserved(self):
        venue = FakeVenue(fail_side="BUY")
        venue.failure_reasons["BUY"] = "buy_insufficient_funds:USDC"

        outcome = _coordinator(venue).start(mid=100.0)

        self.assertEqual(outcome.reason, "buy_insufficient_funds:USDC")

    def test_no_fill_until_ttl_cancels_both(self):
        venue = FakeVenue()
        coordinator = _coordinator(venue, quote_ttl_sec=32)
        coordinator.start(mid=100.0)

        outcome = coordinator.step(now=33.0)

        self.assertEqual(outcome.phase, "expired")
        self.assertTrue(outcome.terminal)
        self.assertCountEqual(venue.canceled, ["L1", "L2"])
        self.assertEqual(coordinator.tickets, [])
        self.assertEqual(coordinator.snapshots, {})

    def test_ttl_does_not_terminalize_while_cancel_is_unconfirmed(self):
        venue = FakeVenue()
        coordinator = _coordinator(venue, quote_ttl_sec=32)
        coordinator.start(mid=100.0)
        venue.cancel_fail.add("L1")

        pending = coordinator.step(now=33.0)

        self.assertFalse(pending.terminal)
        self.assertEqual(pending.reason, "quote_cancel_pending")
        self.assertTrue(any(ticket.order_id == "L1" and ticket.active
                            for ticket in coordinator.tickets))

    def test_balanced_pair_waits_for_cancel_confirmation_before_complete(self):
        venue = FakeVenue()
        coordinator = _coordinator(venue)
        coordinator.start(mid=100.0)
        venue.fill("L1", 0.5, 99.36, status="open")
        venue.fill("L2", 0.5, 100.64, status="open")
        venue.cancel_fail.add("L1")

        pending = coordinator.step(now=5.0)
        self.assertFalse(pending.terminal)
        self.assertEqual(pending.phase, "closing")
        self.assertEqual(pending.reason, "balanced_cancel_pending")

        venue.cancel_fail.clear()
        complete = coordinator.step(now=6.0)
        self.assertTrue(complete.terminal)
        self.assertEqual(complete.phase, "complete")

    def test_fill_during_ttl_cancel_is_reconciled_as_exposure(self):
        venue = FakeVenue(current=99.36)
        coordinator = _coordinator(venue, quote_ttl_sec=32)
        coordinator.start(mid=100.0)
        venue.fill_on_cancel["L1"] = (1.0, 99.36)

        outcome = coordinator.step(now=33.0)

        self.assertEqual(outcome.phase, "exposed")
        self.assertFalse(outcome.terminal)
        self.assertAlmostEqual(outcome.net_qty, 1.0)
        replacement = venue.orders[-1][0]
        self.assertEqual(replacement.side, "SELL")
        self.assertGreater(replacement.price, 99.36)

    def test_both_fills_complete_cycle_and_measure_fast_latency(self):
        venue = FakeVenue()
        coordinator = _coordinator(venue, quote_ttl_sec=32, fast_fill_ratio=0.25)
        coordinator.start(mid=100.0)
        venue.fill("L1", 1.0, 99.36)
        venue.fill("L2", 1.0, 100.64)

        outcome = coordinator.step(now=5.0)

        self.assertEqual(outcome.phase, "complete")
        self.assertTrue(outcome.shock)
        self.assertEqual(outcome.first_fill_side, "BOTH")
        self.assertEqual(outcome.fill_latency_sec, 5.0)
        self.assertAlmostEqual(outcome.gross_pnl, 1.28)

    def test_slow_single_fill_keeps_existing_profitable_opposite_leg(self):
        venue = FakeVenue(current=99.36)
        coordinator = _coordinator(venue, quote_ttl_sec=32, fast_fill_ratio=0.25)
        coordinator.start(mid=100.0)
        venue.fill("L1", 1.0, 99.36)

        outcome = coordinator.step(now=20.0)

        self.assertEqual(outcome.phase, "exposed")
        self.assertFalse(outcome.shock)
        self.assertEqual(outcome.first_fill_side, "BUY")
        self.assertAlmostEqual(outcome.net_qty, 1.0)
        self.assertEqual(venue.canceled, [])
        self.assertEqual(len(venue.orders), 2, "the initial ask is already a profitable exit")

    def test_partial_fill_cancels_entry_remainder_and_resizes_exit(self):
        venue = FakeVenue(current=99.0)
        coordinator = _coordinator(venue)
        coordinator.start(mid=100.0)
        venue.fill("L1", 0.4, 99.36, status="open")

        outcome = coordinator.step(now=12.0)

        self.assertEqual(outcome.phase, "exposed")
        self.assertAlmostEqual(outcome.net_qty, 0.4)
        self.assertCountEqual(venue.canceled, ["L1", "L2"])
        replacement, pair_id = venue.orders[-1]
        self.assertEqual(replacement.side, "SELL")
        self.assertAlmostEqual(replacement.qty, 0.4)
        self.assertEqual(pair_id, "pair-1")
        self.assertGreater(replacement.price, 99.36)

    def test_checkpoint_roundtrip_restores_owned_tickets(self):
        venue = FakeVenue(current=100.0)
        coordinator = _coordinator(venue)
        coordinator.start(mid=100.0, pair_id="durable-pair")
        state = coordinator.export_state()

        restored = PairCoordinator.from_state(venue, coordinator.policy, state)

        self.assertEqual(restored.pair_id, "durable-pair")
        self.assertEqual(restored.phase, "quoting")
        self.assertEqual([t.order_id for t in restored.tickets], ["L1", "L2"])

    def test_fast_buy_fill_hard_stop_exits_market_only_after_threshold(self):
        venue = FakeVenue(current=95.0)
        coordinator = _coordinator(
            venue, quote_ttl_sec=32, fast_fill_ratio=0.25,
            shock_hard_stop_fraction=0.04)
        coordinator.start(mid=100.0)
        venue.fill("L1", 1.0, 99.36)

        submitted = coordinator.step(now=5.0)
        finished = coordinator.step(now=6.0)

        self.assertEqual(submitted.phase, "stopping")
        self.assertEqual(venue.market_calls, [("SELL", 1.0, "fast_fill_hard_stop")])
        self.assertEqual(finished.phase, "hard_stop")
        self.assertTrue(finished.terminal)
        self.assertAlmostEqual(finished.net_qty, 0.0)

    def test_slow_buy_fill_does_not_use_tighter_shock_threshold(self):
        venue = FakeVenue(current=96.0)
        coordinator = _coordinator(
            venue, quote_ttl_sec=32, fast_fill_ratio=0.25,
            shock_hard_stop_fraction=0.04)
        coordinator.start(mid=100.0)
        venue.fill("L1", 1.0, 99.36)

        outcome = coordinator.step(now=20.0)

        self.assertEqual(outcome.phase, "exposed")
        self.assertFalse(outcome.shock)
        self.assertEqual(venue.market_calls, [])

    def test_slow_buy_fill_still_has_wider_inventory_hard_stop(self):
        venue = FakeVenue(current=90.0)
        coordinator = _coordinator(
            venue, quote_ttl_sec=32, fast_fill_ratio=0.25,
            shock_hard_stop_fraction=0.04, hard_stop_fraction=0.08)
        coordinator.start(mid=100.0)
        venue.fill("L1", 1.0, 99.36)

        submitted = coordinator.step(now=20.0)
        finished = coordinator.step(now=21.0)

        self.assertFalse(submitted.shock)
        self.assertEqual(
            venue.market_calls, [("SELL", 1.0, "inventory_hard_stop")])
        self.assertEqual(finished.phase, "hard_stop")
        self.assertEqual(finished.reason, "inventory_hard_stop")

    def test_dynamic_policy_keeps_anchored_limit_when_market_not_justified(self):
        venue = FakeVenue(current=90.0)
        venue.allow_market = False
        coordinator = _coordinator(
            venue, quote_ttl_sec=32, fast_fill_ratio=0.25,
            shock_hard_stop_fraction=0.04, hard_stop_fraction=0.08)
        coordinator.start(mid=100.0)
        venue.fill("L1", 1.0, 99.36)

        outcome = coordinator.step(now=20.0)

        self.assertEqual(outcome.phase, "exposed")
        self.assertEqual(outcome.reason, "market_exit_waiting_for_trend")
        self.assertEqual(venue.market_calls, [])
        self.assertTrue(any(ticket.side == "SELL" and ticket.active
                            for ticket, _pair in venue.orders))

    def test_fast_sell_fill_has_symmetric_market_buy_hard_stop(self):
        venue = FakeVenue(current=105.0)
        coordinator = _coordinator(
            venue, start_side="SELL", quote_ttl_sec=32, fast_fill_ratio=0.25,
            shock_hard_stop_fraction=0.04)
        coordinator.start(mid=100.0)
        venue.fill("L1", 1.0, 100.64)

        submitted = coordinator.step(now=5.0)
        finished = coordinator.step(now=6.0)

        self.assertEqual(submitted.phase, "stopping")
        self.assertEqual(venue.market_calls, [("BUY", 1.0, "fast_fill_hard_stop")])
        self.assertEqual(finished.phase, "hard_stop")
        self.assertAlmostEqual(finished.net_qty, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
