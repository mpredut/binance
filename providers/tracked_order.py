"""Persistent, provider-neutral lifecycle for one strategy-owned order intent.

``MarketApi.place`` remains the synchronous policy/mechanics compatibility call.
This module owns the asynchronous boundary around it: persist before submit,
recover an ambiguous response by deterministic client ID, reconcile venue status,
and issue at most one TTL cancel.  Strategy code still decides whether an intent is
valid and how a terminal fill changes its financial state.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable, Dict, Optional

from .strategy_executor import OrderStatus


PersistPending = Callable[[Optional[dict]], None]
SubmitIntent = Callable[[], object]


@dataclass(frozen=True)
class TrackedOrderResult:
    """One lifecycle observation without equating submit acceptance with a fill."""

    outcome: str  # active, terminal, absent, retryable
    intent: dict
    status: Optional[OrderStatus] = None

    def __post_init__(self):
        if self.outcome not in {"active", "terminal", "absent", "retryable"}:
            raise ValueError(f"invalid tracked-order outcome: {self.outcome!r}")
        if self.outcome == "terminal" and self.status is None:
            raise ValueError("terminal tracked-order result requires status")

    @property
    def order_known(self) -> bool:
        return bool(self.intent.get("order_id"))


def _finite(raw, *, positive=False):
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def _terminal_payload(status: OrderStatus, observed_at: float) -> dict:
    return {
        "status": status.status,
        "filled_qty": status.filled_qty,
        "cost": status.cost,
        "fee": status.fee,
        "observed_at": observed_at,
    }


def _status_from_terminal_payload(payload) -> Optional[OrderStatus]:
    if not isinstance(payload, dict):
        return None
    try:
        status = OrderStatus(
            str(payload["status"]),
            payload["filled_qty"],
            payload["cost"],
            payload["fee"],
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return status if status.terminal else None


class TrackedOrderLifecycle:
    """State machine for a single persisted order owned by one strategy.

    The caller supplies a persistence callback because campaign state belongs to
    the strategy.  The callback must durably replace the pending intent; raising
    before submit prevents the external side effect.  Terminal venue truth stays
    persisted in ``terminal_status`` until the strategy atomically applies the fill
    and removes pending from its own state.
    """

    def __init__(self, market_api, *, provider_name: str, venue: Optional[str] = None,
                 missing_confirmations: int = 2,
                 retry_on_lookup_error: bool = False,
                 max_age_seconds: Optional[float] = None,
                 audit=None, clock: Callable[[], float] = time.time):
        if not str(provider_name or "").strip():
            raise ValueError("provider_name is required")
        if int(missing_confirmations) <= 0:
            raise ValueError("missing_confirmations must be positive")
        if max_age_seconds is not None:
            parsed_age = _finite(max_age_seconds, positive=True)
            if parsed_age is None:
                raise ValueError("max_age_seconds must be finite and positive")
            max_age_seconds = parsed_age
        self.market_api = market_api
        self.provider_name = str(provider_name)
        self.venue = str(venue or provider_name)
        self.missing_confirmations = int(missing_confirmations)
        self.retry_on_lookup_error = bool(retry_on_lookup_error)
        self.max_age_seconds = max_age_seconds
        self.audit = audit
        self.clock = clock

    def _now(self) -> float:
        now = _finite(self.clock(), positive=True)
        if now is None:
            raise ValueError("tracked-order clock returned invalid time")
        return now

    def _audit(self, event: str, intent: dict, **fields) -> None:
        if self.audit is None:
            return
        base = {
            "intent_id": intent.get("intent_id"),
            "venue": self.venue,
            "symbol": intent.get("symbol"),
            "side": intent.get("side"),
            "kind": intent.get("kind"),
            "client_order_id": intent.get("client_order_id"),
            "order_id": intent.get("order_id"),
        }
        base.update(fields)
        self.audit.record(event, **base)

    @staticmethod
    def _validate_identity(intent: dict) -> None:
        for key in ("intent_id", "client_order_id", "symbol", "side"):
            if not str(intent.get(key) or "").strip():
                raise ValueError(f"tracked intent missing {key}")
        if str(intent["side"]).upper() not in {"BUY", "SELL"}:
            raise ValueError("tracked intent side must be BUY or SELL")
        if _finite(intent.get("requested_qty"), positive=True) is None:
            raise ValueError("tracked intent requested_qty must be positive")
        price = intent.get("requested_price")
        if price is not None and _finite(price, positive=True) is None:
            raise ValueError("tracked intent requested_price must be positive")

    def new_intent(self, *, intent_id: str, client_order_id: str, symbol: str,
                   side: str, requested_qty: float,
                   requested_price: Optional[float] = None,
                   kind: Optional[str] = None, attempt: int = 1,
                   metadata: Optional[Dict] = None) -> dict:
        now = self._now()
        intent = {
            "intent_id": str(intent_id),
            "client_order_id": str(client_order_id),
            "symbol": str(symbol),
            "side": str(side).upper(),
            "kind": None if kind is None else str(kind),
            "requested_qty": float(requested_qty),
            "requested_price": (None if requested_price is None
                                else float(requested_price)),
            "attempt": int(attempt),
            "created_at": now,
            "lookup_misses": 0,
        }
        reserved = set(intent) | {
            "order_id", "last_status", "filled_qty", "terminal_status",
            "cancel_attempted_at",
        }
        for key, value in dict(metadata or {}).items():
            if key in reserved:
                raise ValueError(f"tracked intent metadata uses reserved key: {key}")
            intent[str(key)] = value
        self._validate_identity(intent)
        if intent["attempt"] <= 0:
            raise ValueError("tracked intent attempt must be positive")
        return intent

    @staticmethod
    def _order_fields(response) -> dict:
        if isinstance(response, dict):
            order_id = (response.get("orderId") or response.get("order_id")
                        or response.get("id") or response.get("txid"))
            fields = {}
            if order_id is not None:
                fields["order_id"] = str(order_id)
            status = response.get("status")
            if status is not None:
                fields["submit_status"] = str(status)
            submitted_qty = _finite(
                response.get("origQty", response.get("qty")), positive=True)
            if submitted_qty is not None:
                fields["submitted_qty"] = submitted_qty
            return fields
        if (not isinstance(response, bool)
                and isinstance(response, (str, int)) and str(response).strip()):
            return {"order_id": str(response)}
        return {}

    def submit(self, intent: dict, *, persist: PersistPending,
               submit: SubmitIntent) -> TrackedOrderResult:
        """Persist and submit exactly once; status is reconciled by a later tick.

        This method never polls, sleeps, waits for a fill, or requests cancellation.
        It performs only the caller's bounded submit call after the durability write.
        """
        pending = dict(intent)
        self._validate_identity(pending)
        # Durability boundary comes before submit.  If this fails, submit is never
        # called; after a later failure the deterministic client ID enables lookup.
        persist(dict(pending))
        self._audit(
            "submit_requested", pending,
            qty=pending["requested_qty"], price=pending.get("requested_price"),
        )
        try:
            response = submit()
        except Exception as exc:  # The venue may still have accepted the request.
            pending["submit_error"] = f"{exc.__class__.__name__}: {exc}"
            persist(dict(pending))
            self._audit(
                "submit_ambiguous", pending,
                error_type=exc.__class__.__name__, error=str(exc),
            )
            return TrackedOrderResult("active", pending)

        fields = self._order_fields(response)
        pending.update(fields)
        persist(dict(pending))
        if pending.get("order_id"):
            self._audit(
                "submit_accepted", pending,
                qty=pending["requested_qty"],
                submitted_qty=pending.get("submitted_qty"),
                status=pending.get("submit_status"),
            )
        else:
            self._audit("submit_ambiguous", pending, error="response_without_order_id")
        return TrackedOrderResult("active", pending)

    def _persist_terminal(self, pending: dict, status: OrderStatus,
                          persist: PersistPending) -> TrackedOrderResult:
        pending = dict(pending)
        pending["last_status"] = status.status
        pending["filled_qty"] = status.filled_qty
        pending["terminal_status"] = _terminal_payload(status, self._now())
        persist(dict(pending))
        self._audit(
            "order_terminal", pending, status=status.status,
            filled_qty=status.filled_qty, cost=status.cost, fee=status.fee,
        )
        return TrackedOrderResult("terminal", pending, status)

    def reconcile(self, intent: dict, *, persist: PersistPending) -> TrackedOrderResult:
        """Reconcile one pending intent against venue truth; never submit here."""
        pending = dict(intent)
        self._validate_identity(pending)

        cached_terminal = _status_from_terminal_payload(
            pending.get("terminal_status"))
        if cached_terminal is not None:
            return TrackedOrderResult("terminal", pending, cached_terminal)

        symbol = pending["symbol"]
        client_order_id = pending["client_order_id"]
        order_id = pending.get("order_id")
        if not order_id:
            try:
                native = self.market_api.order_by_client_id(
                    symbol, client_order_id, provider_name=self.provider_name)
            except Exception as exc:
                pending["lookup_error"] = f"{exc.__class__.__name__}: {exc}"
                persist(dict(pending))
                self._audit(
                    "lookup_error", pending,
                    error_type=exc.__class__.__name__, error=str(exc),
                )
                if self.retry_on_lookup_error:
                    # At-least-once mode deliberately favors a repeated submit over
                    # silently losing the intent.  Strategy code must revalidate the
                    # signal and should reuse this deterministic client ID so venues
                    # with idempotency can reject/deduplicate a duplicate request.
                    persist(None)
                    self._audit("lookup_retryable", pending)
                    return TrackedOrderResult("retryable", pending)
                return TrackedOrderResult("active", pending)
            fields = self._order_fields(native)
            if not fields.get("order_id"):
                misses = int(pending.get("lookup_misses") or 0) + 1
                pending["lookup_misses"] = misses
                persist(dict(pending))
                self._audit(
                    "order_missing", pending, missing_confirmation=misses,
                    missing_confirmations_required=self.missing_confirmations,
                )
                if misses < self.missing_confirmations:
                    return TrackedOrderResult("active", pending)
                persist(None)
                return TrackedOrderResult("absent", pending)
            pending.update(fields)
            pending["lookup_misses"] = 0
            pending.pop("lookup_error", None)
            persist(dict(pending))
            order_id = pending["order_id"]
            self._audit("order_recovered", pending)

        try:
            status = self.market_api.order_status(
                symbol, str(order_id), provider_name=self.provider_name)
        except Exception as exc:
            pending["status_error"] = f"{exc.__class__.__name__}: {exc}"
            persist(dict(pending))
            self._audit(
                "status_error", pending,
                error_type=exc.__class__.__name__, error=str(exc),
            )
            return TrackedOrderResult("active", pending)

        pending.pop("status_error", None)
        if status.terminal:
            return self._persist_terminal(pending, status, persist)

        pending["last_status"] = status.status
        pending["filled_qty"] = status.filled_qty
        persist(dict(pending))
        self._audit(
            "order_status", pending, status=status.status,
            filled_qty=status.filled_qty, cost=status.cost, fee=status.fee,
        )

        created_at = _finite(pending.get("created_at"), positive=True)
        age_seconds = (max(0.0, self._now() - created_at)
                       if created_at is not None else None)
        cancel_attempted = _finite(
            pending.get("cancel_attempted_at"), positive=True)
        if (self.max_age_seconds is None or age_seconds is None
                or age_seconds < self.max_age_seconds or cancel_attempted is not None):
            return TrackedOrderResult("active", pending, status)

        # Persist one-way cancel intent before the external side effect.  A timeout
        # can mean accepted cancel, so never issue another request for this intent.
        pending["cancel_attempted_at"] = self._now()
        persist(dict(pending))
        self._audit("cancel_requested", pending, age_seconds=age_seconds)
        try:
            self.market_api.cancel_order(
                symbol, str(order_id), provider_name=self.provider_name)
        except Exception as exc:
            pending["cancel_error"] = f"{exc.__class__.__name__}: {exc}"
            persist(dict(pending))
            self._audit(
                "cancel_ambiguous", pending,
                error_type=exc.__class__.__name__, error=str(exc),
            )
            return TrackedOrderResult("active", pending, status)

        pending.pop("cancel_error", None)
        persist(dict(pending))
        self._audit("cancel_accepted", pending)
        try:
            post_cancel = self.market_api.order_status(
                symbol, str(order_id), provider_name=self.provider_name)
        except Exception as exc:
            pending["status_error"] = f"{exc.__class__.__name__}: {exc}"
            persist(dict(pending))
            self._audit(
                "post_cancel_status_error", pending,
                error_type=exc.__class__.__name__, error=str(exc),
            )
            return TrackedOrderResult("active", pending, status)
        if post_cancel.terminal:
            return self._persist_terminal(pending, post_cancel, persist)
        pending["last_status"] = post_cancel.status
        pending["filled_qty"] = post_cancel.filled_qty
        persist(dict(pending))
        self._audit(
            "cancel_pending", pending, status=post_cancel.status,
            filled_qty=post_cancel.filled_qty,
        )
        return TrackedOrderResult("active", pending, post_cancel)
