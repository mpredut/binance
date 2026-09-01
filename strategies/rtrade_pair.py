"""Deterministic coordinator for a pair of rtrade quotes.

The module imports no live APIs. Its venue is injected so the same state machine
can be characterized with synthetic fills and then used by the live entry point.
One instance owns both order IDs in a round; the outer scheduler may run multiple
independent instances concurrently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import time
import uuid
from typing import Callable, Optional, Protocol


@dataclass
class OrderTicket:
    order_id: str
    side: str
    price: float
    qty: float
    active: bool = True
    pair_id: Optional[str] = None

    def __post_init__(self):
        self.side = str(self.side).upper()
        self.price = float(self.price)
        self.qty = float(self.qty)
        if (self.side not in {"BUY", "SELL"} or not math.isfinite(self.price)
                or self.price <= 0 or not math.isfinite(self.qty) or self.qty <= 0):
            raise ValueError("ticket rtrade invalid")


@dataclass(frozen=True)
class OrderSnapshot:
    status: str = "open"       # open|closed|canceled|expired
    filled_qty: float = 0.0
    cost: float = 0.0
    fee: float = 0.0

    def __post_init__(self):
        if self.status not in {"open", "closed", "canceled", "expired"}:
            raise ValueError(f"status ordin rtrade invalid: {self.status}")
        if any(not math.isfinite(float(value)) or float(value) < 0
               for value in (self.filled_qty, self.cost, self.fee)):
            raise ValueError("snapshot financiar rtrade invalid")


@dataclass(frozen=True)
class PairPolicy:
    adjustment_fraction: float
    quote_ttl_sec: float = 32.0
    poll_sec: float = 1.0
    fast_fill_ratio: float = 0.25
    min_edge_fraction: float = 0.0115
    shock_hard_stop_fraction: float = 0.04
    hard_stop_fraction: float = 0.08
    price_decimals: int = 4

    def __post_init__(self):
        numeric = (
            self.adjustment_fraction, self.quote_ttl_sec, self.poll_sec,
            self.fast_fill_ratio, self.min_edge_fraction,
            self.shock_hard_stop_fraction, self.hard_stop_fraction,
        )
        if any(not math.isfinite(float(value)) for value in numeric):
            raise ValueError("politica rtrade trebuie sa contina valori finite")
        if not 0 < self.adjustment_fraction < 1:
            raise ValueError("adjustment_fraction trebuie sa fie in (0, 1)")
        if self.quote_ttl_sec <= 0 or self.poll_sec <= 0:
            raise ValueError("quote_ttl_sec and poll_sec must be positive")
        if not 0 < self.fast_fill_ratio <= 1:
            raise ValueError("fast_fill_ratio trebuie sa fie in (0, 1]")
        if not 0 <= self.min_edge_fraction < 1:
            raise ValueError("min_edge_fraction trebuie sa fie in [0, 1)")
        if not 0 < self.shock_hard_stop_fraction < 1:
            raise ValueError("shock_hard_stop_fraction trebuie sa fie in (0, 1)")
        if not self.shock_hard_stop_fraction <= self.hard_stop_fraction < 1:
            raise ValueError(
                "hard_stop_fraction must be >= shock_hard_stop_fraction and < 1")


@dataclass(frozen=True)
class PairOutcome:
    phase: str
    pair_id: str
    terminal: bool
    shock: bool = False
    first_fill_side: Optional[str] = None
    fill_latency_sec: Optional[float] = None
    net_qty: float = 0.0
    buy_qty: float = 0.0
    sell_qty: float = 0.0
    buy_avg: Optional[float] = None
    sell_avg: Optional[float] = None
    gross_pnl: float = 0.0
    fees: float = 0.0
    reason: Optional[str] = None


class PairVenue(Protocol):
    def current_price(self) -> Optional[float]: ...
    def place_limit(self, side: str, price: float, qty: float,
                    pair_id: str) -> Optional[OrderTicket]: ...
    def place_market_exit(self, side: str, qty: float,
                          reason: str,
                          pair_id: Optional[str] = None) -> Optional[OrderTicket]: ...
    def market_exit_allowed(self, exposure_side: str, loss_fraction: float,
                            reason: str) -> bool: ...
    def order_status(self, order_id: str) -> OrderSnapshot: ...
    def cancel(self, order_id: str) -> bool: ...


def _place_failure_reason(venue: PairVenue, side: str) -> str:
    detail = getattr(venue, "last_place_failure_reason", None)
    if callable(detail):
        reason = detail(side)
        if reason:
            return str(reason)
    return f"{side.lower()}_place_failed"


def quote_prices(mid: float, adjustment_fraction: float,
                 decimals: int = 4) -> tuple[float, float]:
    """Return bid/ask quotes symmetric around the same market snapshot."""
    if mid <= 0:
        raise ValueError("mid trebuie sa fie pozitiv")
    return (
        round(mid * (1 - adjustment_fraction), decimals),
        round(mid * (1 + adjustment_fraction), decimals),
    )


def anchored_exit_price(exit_side: str, fill_price: float, current_price: float,
                        adjustment_fraction: float, min_edge_fraction: float,
                        decimals: int = 4) -> float:
    """Anchor the target so it cannot fall below cost plus edge or chase a loss.

    The edge formula matches the financial guard:
      SELL: (sell-buy)/sell >= edge  -> sell >= buy/(1-edge)
      BUY:  (sell-buy)/sell >= edge  -> buy <= sell*(1-edge)
    """
    side = exit_side.upper()
    if fill_price <= 0 or current_price <= 0:
        raise ValueError("preturile trebuie sa fie pozitive")
    if side == "SELL":
        market_target = current_price * (1 + adjustment_fraction)
        floor = fill_price / (1 - min_edge_fraction)
        return round(max(market_target, floor), decimals)
    if side == "BUY":
        market_target = current_price * (1 - adjustment_fraction)
        ceiling = fill_price * (1 - min_edge_fraction)
        return round(min(market_target, ceiling), decimals)
    raise ValueError("exit_side must be BUY or SELL")


class PairCoordinator:
    """State machine for one BUY+SELL round in either direction."""

    def __init__(self, venue: PairVenue, qty: float, policy: PairPolicy, *,
                 start_side: str = "BUY",
                 clock: Callable[[], float] = time.monotonic,
                 sleeper: Callable[[float], None] = time.sleep,
                 pair_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex):
        self.venue = venue
        self.qty = float(qty)
        if not math.isfinite(self.qty) or self.qty <= 0:
            raise ValueError("rtrade qty must be finite and positive")
        self.policy = policy
        self.start_side = start_side.upper()
        if self.start_side not in {"BUY", "SELL"}:
            raise ValueError("start_side must be BUY or SELL")
        self.clock = clock
        self.sleeper = sleeper
        self.pair_id_factory = pair_id_factory
        self.pair_id = ""
        self.started_at = 0.0
        self.first_fill_at: Optional[float] = None
        self.first_fill_side: Optional[str] = None
        self.shock = False
        self.phase = "idle"
        self.reason: Optional[str] = None
        self.tickets: list[OrderTicket] = []
        self.snapshots: dict[str, OrderSnapshot] = {}
        self.stop_ticket: Optional[OrderTicket] = None

    def start(self, mid: Optional[float] = None,
              pair_id: Optional[str] = None) -> PairOutcome:
        if self.phase not in {"idle", "complete", "expired", "failed", "hard_stop"}:
            raise RuntimeError("the existing round is not terminal")
        mid = float(mid if mid is not None else (self.venue.current_price() or 0.0))
        if not math.isfinite(mid):
            raise ValueError("mid rtrade trebuie sa fie finit")
        buy_price, sell_price = quote_prices(
            mid, self.policy.adjustment_fraction, self.policy.price_decimals)
        self.pair_id = pair_id or self.pair_id_factory()
        self.started_at = self.clock()
        self.first_fill_at = None
        self.first_fill_side = None
        self.shock = False
        self.reason = None
        self.tickets = []
        self.snapshots = {}
        self.stop_ticket = None

        prices = {"BUY": buy_price, "SELL": sell_price}
        first_side = self.start_side
        second_side = "SELL" if first_side == "BUY" else "BUY"
        first = self.venue.place_limit(
            first_side, prices[first_side], self.qty, self.pair_id)
        if first is None:
            self.phase = "failed"
            self.reason = _place_failure_reason(self.venue, first_side)
            return self.outcome()
        self.tickets.append(first)

        second = self.venue.place_limit(
            second_side, prices[second_side], self.qty, self.pair_id)
        if second is None:
            self._cancel(first)
            self.phase = "failed"
            self.reason = _place_failure_reason(self.venue, second_side)
            return self.outcome()
        self.tickets.append(second)
        self.phase = "quoting"
        return self.outcome()

    def export_state(self) -> dict:
        """Return a JSON-safe checkpoint sufficient for adoption after restart."""
        return {
            "pair_id": self.pair_id, "qty": self.qty,
            "start_side": self.start_side, "phase": self.phase,
            "reason": self.reason, "shock": self.shock,
            "elapsed_sec": max(0.0, self.clock() - self.started_at),
            "first_fill_elapsed_sec": (
                None if self.first_fill_at is None
                else max(0.0, self.first_fill_at - self.started_at)),
            "first_fill_side": self.first_fill_side,
            "tickets": [vars(ticket).copy() for ticket in self.tickets],
            "snapshots": {
                str(order_id): vars(snapshot).copy()
                for order_id, snapshot in self.snapshots.items()
            },
        }

    @classmethod
    def from_state(cls, venue, policy, state, *,
                   clock=time.monotonic, sleeper=time.sleep):
        required = {"pair_id", "qty", "start_side", "phase", "tickets"}
        if not required.issubset(state or {}):
            raise ValueError("checkpoint rtrade incomplet")
        obj = cls(venue, state["qty"], policy, start_side=state["start_side"],
                  clock=clock, sleeper=sleeper)
        obj.pair_id = str(state["pair_id"])
        obj.phase = str(state["phase"])
        obj.reason = state.get("reason")
        obj.shock = bool(state.get("shock", False))
        obj.started_at = clock() - max(0.0, float(state.get("elapsed_sec", 0.0)))
        ff_elapsed = state.get("first_fill_elapsed_sec")
        obj.first_fill_at = (None if ff_elapsed is None
                             else obj.started_at + max(0.0, float(ff_elapsed)))
        obj.first_fill_side = state.get("first_fill_side")
        obj.tickets = [OrderTicket(**ticket) for ticket in state.get("tickets", [])]
        obj.snapshots = {
            str(order_id): OrderSnapshot(**snapshot)
            for order_id, snapshot in state.get("snapshots", {}).items()
        }
        return obj

    def _cancel(self, ticket: OrderTicket) -> bool:
        if not ticket.active:
            return True
        ok = bool(self.venue.cancel(ticket.order_id))
        if ok:
            ticket.active = False
        return ok

    def _refresh(self) -> None:
        for ticket in self.tickets:
            snap = self.venue.order_status(ticket.order_id)
            self.snapshots[ticket.order_id] = snap
            if snap.status in {"closed", "canceled", "expired"}:
                ticket.active = False
        self._compact_zero_fill_history()

    def _compact_zero_fill_history(self) -> None:
        """Remove terminal orders that contributed no inventory or P&L.

        Repricing exposure can create many canceled orders. Retain active orders and
        every filled order required for accounting, but prevent zero-fill cancellations
        from growing memory and checkpoints without bound.
        """
        kept = []
        for ticket in self.tickets:
            snap = self.snapshots.get(ticket.order_id, OrderSnapshot())
            if ticket.active or snap.filled_qty > 0:
                kept.append(ticket)
            else:
                self.snapshots.pop(ticket.order_id, None)
        self.tickets = kept

    def _side_totals(self, side: str) -> tuple[float, float, float]:
        qty = cost = fee = 0.0
        for ticket in self.tickets:
            if ticket.side.upper() != side.upper():
                continue
            snap = self.snapshots.get(ticket.order_id, OrderSnapshot())
            qty += max(0.0, snap.filled_qty)
            cost += max(0.0, snap.cost)
            fee += max(0.0, snap.fee)
        return qty, cost, fee

    def _totals(self):
        bq, bc, bf = self._side_totals("BUY")
        sq, sc, sf = self._side_totals("SELL")
        return bq, bc, bf, sq, sc, sf

    def _record_first_fill(self, now: float, buy_qty: float, sell_qty: float) -> None:
        if self.first_fill_at is not None or (buy_qty <= 0 and sell_qty <= 0):
            return
        self.first_fill_at = now
        if buy_qty > 0 and sell_qty <= 0:
            self.first_fill_side = "BUY"
        elif sell_qty > 0 and buy_qty <= 0:
            self.first_fill_side = "SELL"
        else:
            self.first_fill_side = "BOTH"
        latency = max(0.0, now - self.started_at)
        self.shock = latency <= self.policy.quote_ttl_sec * self.policy.fast_fill_ratio

    def _active_ticket(self, side: str) -> Optional[OrderTicket]:
        for ticket in reversed(self.tickets):
            if ticket.side.upper() == side.upper() and ticket.active:
                return ticket
        return None

    def _cancel_entry_remainder(self, exposure_side: str) -> None:
        # Once exposure exists, prevent the same side from increasing it further.
        entry_side = "BUY" if exposure_side == "LONG" else "SELL"
        for ticket in self.tickets:
            if ticket.side.upper() == entry_side and ticket.active:
                self._cancel(ticket)

    def _has_active_tickets(self) -> bool:
        return any(ticket.active for ticket in self.tickets)

    def _ensure_anchored_exit(self, exposure_side: str, net_qty: float,
                              entry_avg: float, current: float) -> bool:
        exit_side = "SELL" if exposure_side == "LONG" else "BUY"
        target = anchored_exit_price(
            exit_side, entry_avg, current,
            self.policy.adjustment_fraction,
            self.policy.min_edge_fraction,
            self.policy.price_decimals,
        )
        ticket = self._active_ticket(exit_side)
        remaining = 0.0
        if ticket is not None:
            filled = self.snapshots.get(ticket.order_id, OrderSnapshot()).filled_qty
            remaining = max(0.0, ticket.qty - filled)
        adequate_price = bool(ticket and (
            (exit_side == "SELL" and ticket.price >= target) or
            (exit_side == "BUY" and ticket.price <= target)))
        adequate_qty = ticket is not None and math.isclose(
            remaining, abs(net_qty), rel_tol=1e-6, abs_tol=1e-8)
        if adequate_price and adequate_qty:
            return True
        if ticket is not None and not self._cancel(ticket):
            self.reason = "exit_cancel_failed"
            return False
        replacement = self.venue.place_limit(
            exit_side, target, abs(net_qty), self.pair_id)
        if replacement is None:
            self.reason = "exit_place_failed"
            return False
        self.tickets.append(replacement)
        return True

    def _submit_hard_stop(self, exposure_side: str, net_qty: float,
                          entry_avg: float, current: float) -> bool:
        if abs(net_qty) <= 1e-8:
            return False
        stop_fraction = (self.policy.shock_hard_stop_fraction if self.shock
                         else self.policy.hard_stop_fraction)
        breached = (
            current <= entry_avg * (1 - stop_fraction)
            if exposure_side == "LONG"
            else current >= entry_avg * (1 + stop_fraction)
        )
        if not breached:
            return False
        loss_fraction = (
            max(0.0, (entry_avg - current) / entry_avg)
            if exposure_side == "LONG"
            else max(0.0, (current - entry_avg) / entry_avg)
        )
        reason = "fast_fill_hard_stop" if self.shock else "inventory_hard_stop"
        allow = getattr(self.venue, "market_exit_allowed", None)
        if callable(allow) and not allow(exposure_side, loss_fraction, reason):
            self.reason = "market_exit_waiting_for_trend"
            return False
        exit_side = "SELL" if exposure_side == "LONG" else "BUY"
        exit_ticket = self._active_ticket(exit_side)
        if exit_ticket is not None and not self._cancel(exit_ticket):
            self.reason = "hard_stop_cancel_failed"
            return False
        market = self.venue.place_market_exit(
            exit_side, abs(net_qty),
            reason=reason,
            pair_id=self.pair_id)
        if market is None:
            self.reason = "hard_stop_place_failed"
            return False
        self.tickets.append(market)
        self.stop_ticket = market
        self.phase = "stopping"
        return True

    def step(self, now: Optional[float] = None) -> PairOutcome:
        if self.phase in {"complete", "expired", "failed", "hard_stop"}:
            return self.outcome()
        if self.phase == "idle":
            raise RuntimeError("start() trebuie apelat inainte de step()")
        now = self.clock() if now is None else float(now)
        self._refresh()
        bq, bc, _bf, sq, sc, _sf = self._totals()
        self._record_first_fill(now, bq, sq)
        net = bq - sq

        if self.phase == "stopping":
            if abs(net) <= 1e-8:
                self.phase = "hard_stop"
                self.reason = ("fast_fill_hard_stop" if self.shock
                               else "inventory_hard_stop")
            return self.outcome()

        if bq > 0 or sq > 0:
            if abs(net) <= 1e-8:
                for ticket in self.tickets:
                    self._cancel(ticket)
                # Cancellation may race with a fill or fail. Confirm venue state
                # before declaring the round terminal.
                self._refresh()
                bq, bc, _bf, sq, sc, _sf = self._totals()
                net = bq - sq
                if abs(net) <= 1e-8:
                    if self._has_active_tickets():
                        self.phase = "closing"
                        self.reason = "balanced_cancel_pending"
                        return self.outcome()
                    self.phase = "complete"
                    self.reason = None
                    return self.outcome()

            exposure_side = "LONG" if net > 0 else "SOLD"
            self.phase = "exposed"
            self._cancel_entry_remainder(exposure_side)
            # Reconcile after cancellation because an order may fill during the race.
            self._refresh()
            bq, bc, _bf, sq, sc, _sf = self._totals()
            net = bq - sq
            if abs(net) <= 1e-8:
                self.phase = "complete"
                return self.outcome()
            exposure_side = "LONG" if net > 0 else "SOLD"
            entry_avg = (bc / bq) if exposure_side == "LONG" else (sc / sq)
            current = float(self.venue.current_price() or 0.0)
            if current <= 0:
                self.reason = "current_price_unavailable"
                return self.outcome()
            if self._submit_hard_stop(exposure_side, net, entry_avg, current):
                return self.outcome()
            self._ensure_anchored_exit(exposure_side, net, entry_avg, current)
            return self.outcome()

        if now - self.started_at >= self.policy.quote_ttl_sec:
            for ticket in self.tickets:
                self._cancel(ticket)
            # The final read resolves the fill-versus-cancel race.
            self._refresh()
            bq, _bc, _bf, sq, _sc, _sf = self._totals()
            if bq > 0 or sq > 0:
                return self.step(now=now)
            if self._has_active_tickets():
                self.reason = "quote_cancel_pending"
                return self.outcome()
            self.phase = "expired"
            self.reason = "quote_ttl"
        return self.outcome()

    def run_cycle(self, mid: Optional[float] = None) -> PairOutcome:
        outcome = self.start(mid)
        while not outcome.terminal:
            self.sleeper(self.policy.poll_sec)
            outcome = self.step()
        return outcome

    def outcome(self) -> PairOutcome:
        bq, bc, bf, sq, sc, sf = self._totals()
        buy_avg = bc / bq if bq > 0 else None
        sell_avg = sc / sq if sq > 0 else None
        latency = (self.first_fill_at - self.started_at
                   if self.first_fill_at is not None else None)
        return PairOutcome(
            phase=self.phase,
            pair_id=self.pair_id,
            terminal=self.phase in {"complete", "expired", "failed", "hard_stop"},
            shock=self.shock,
            first_fill_side=self.first_fill_side,
            fill_latency_sec=latency,
            net_qty=bq - sq,
            buy_qty=bq,
            sell_qty=sq,
            buy_avg=buy_avg,
            sell_avg=sell_avg,
            gross_pnl=sc - bc,
            fees=bf + sf,
            reason=self.reason,
        )
