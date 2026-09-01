import os
import time
import datetime
import math
import uuid
from collections import deque
import threading
from decimal import Decimal, ROUND_DOWN
from concurrent.futures import ThreadPoolExecutor, wait

# Financial policy and operational invariants are documented in docs/RTRADE.md.
# Effective configuration remains in rtrade_config.env.


# my imports
import log
import alertnotifiers as alert
import utils as u
import symbols as sym
from binance_api import bapi as api
from binance_api import bapi_placeorder as po   # Retained for the dead-safe WeightLimitBlock path.
from providers.market_api import api as mkt      # Single guarded Instrument.place proxy.
from providers.execution_audit import AuditedStrategyExecutor
from providers.quantity import decide_quantity
from market_regime import MarketRegimeDecision
from order_retry import (
    OrderSubmissionRefused,
    StrategyExecutorLifecycleApi,
    TrackedOrderLifecycle,
)
from rtrade_pair_store import RTradePairStore, rtrade_client_order_id
from strategies.rtrade_pair import (
    OrderSnapshot as PairOrderSnapshot,
    OrderTicket as PairOrderTicket,
    PairCoordinator,
    PairPolicy,
)


_RTRADE_HEARTBEAT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "cachedb", "rtrade.heartbeat")
_RTRADE_HEARTBEAT_INTERVAL_SEC = 30.0
_rtrade_heartbeat_lock = threading.Lock()
_rtrade_heartbeat_last = float("-inf")


def _touch_rtrade_heartbeat(*, force=False, now=None):
    """Publish actual coordinator progress independently from buffered stdout.

    The healthcheck must not infer liveness from ``rtrade.log``: normal trend and
    placement backoffs can make that log quiet, while redirection can delay writes.
    Touching from the bounded coordinator loop still detects a blocked API call or
    dead loop, unlike a separate heartbeat thread that could outlive the work.
    """
    global _rtrade_heartbeat_last
    now = time.monotonic() if now is None else float(now)
    with _rtrade_heartbeat_lock:
        if not force and now - _rtrade_heartbeat_last < _RTRADE_HEARTBEAT_INTERVAL_SEC:
            return
        try:
            os.makedirs(os.path.dirname(_RTRADE_HEARTBEAT_PATH), exist_ok=True)
            with open(_RTRADE_HEARTBEAT_PATH, "a", encoding="utf-8"):
                pass
            os.utime(_RTRADE_HEARTBEAT_PATH, None)
        except OSError:
            # Trading must not stop because an observational heartbeat cannot be
            # written. The healthcheck will surface the stale/missing file.
            return
        _rtrade_heartbeat_last = now


# Load versioned, non-secret tuning parameters before reading the environment below.
# ``botcore.load_dotenv`` does not overwrite variables already set in the real environment.
from botcore import (load_dotenv as _load_dotenv, required_bool_env,
                     required_env, required_float_env, required_int_env)
_load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "rtrade_config.env"))

# Seconds between cancel-and-recreate attempts.
WAIT_FOR_ORDER = required_float_env("RTRADE_WAIT_FOR_ORDER_SEC")
MIN_adjustment_percent = required_float_env("RTRADE_MIN_ADJUSTMENT_PCT")

# Express each round's budget in quote currency. Calculate TAO quantity once from
# the round's starting price.
RTRADE_NOTIONAL_USDC = required_float_env("RTRADE_NOTIONAL_USDC")

# Initial fractional spread estimate for filled prices before the first real fill.
RTRADE_INITIAL_SPREAD_PCT = required_float_env("RTRADE_INITIAL_SPREAD_PCT")

# Relaxation rate for adjustment_percent after the opposite side fills.
# BUY and SELL are intentionally asymmetric.
RTRADE_BUY_DECAY_PCT = required_float_env("RTRADE_BUY_DECAY_PCT")
RTRADE_SELL_DECAY_PCT = required_float_env("RTRADE_SELL_DECAY_PCT")

# Base hours window, divided by failure_count, for the desperate path after the
# opposite side fills. BUY (0.3) and SELL (0.23) are intentionally asymmetric.
RTRADE_BUY_DESPERATE_HOURS_BASE = required_float_env("RTRADE_BUY_DESPERATE_HOURS_BASE")
RTRADE_SELL_DESPERATE_HOURS_BASE = required_float_env("RTRADE_SELL_DESPERATE_HOURS_BASE")

# Shared BUY/SELL lookback for the desperate path (one hour plus 60 seconds).
RTRADE_DESPERATE_SAFEBACK_SEC = required_float_env("RTRADE_DESPERATE_SAFEBACK_SEC")

# Hours for the normal path before desperation. BUY waits longer than SELL.
RTRADE_BUY_NORMAL_HOURS = required_float_env("RTRADE_BUY_NORMAL_HOURS")
RTRADE_SELL_NORMAL_HOURS = required_float_env("RTRADE_SELL_NORMAL_HOURS")

# Fractional price offset and hours window for the desperate follow-up immediately
# after the opposite side fills. Shared by both directions.
RTRADE_FOLLOWUP_OFFSET_PCT = required_float_env("RTRADE_FOLLOWUP_OFFSET_PCT")
RTRADE_FOLLOWUP_HOURS = required_float_env("RTRADE_FOLLOWUP_HOURS")

# ``are_close`` tolerance for detecting a bad day when price crosses the reference,
# plus the adjustment multiplier used then. Shared by both directions.
RTRADE_BAD_DAY_TOLERANCE_PCT = required_float_env("RTRADE_BAD_DAY_TOLERANCE_PCT")
RTRADE_BAD_DAY_MULTIPLIER = required_float_env("RTRADE_BAD_DAY_MULTIPLIER")

# Epsilon preventing division by zero in profit/loss ratio calculations.
RTRADE_ZERO_EPSILON = required_float_env("RTRADE_ZERO_EPSILON")

# Maximum failures accepted before abandoning a BUY or SELL order.
RTRADE_MAX_FAILURES = required_int_env("RTRADE_MAX_FAILURES")
# Trend filter: this spread bot suffers adverse selection in a clear trend. It stays
# idle when ``|gradient_recent| > K * epsilon`` over a short window, where epsilon is
# the volatility-calibrated cacheManager noise floor. Controlled by a kill switch and threshold.
RTRADE_TREND_FILTER_ENABLED = required_bool_env("RTRADE_TREND_FILTER_ENABLED")
RTRADE_TREND_FILTER_K = required_float_env("RTRADE_TREND_FILTER_K")
RTRADE_TREND_WINDOW_SEC = required_float_env("RTRADE_TREND_WINDOW_SEC")
RTRADE_TREND_OHLC_FALLBACK_ENABLED = required_bool_env("RTRADE_TREND_OHLC_FALLBACK_ENABLED")
RTRADE_DYNAMIC_MARKET_EXIT_MODE = required_env("RTRADE_DYNAMIC_MARKET_EXIT_MODE").lower()
RTRADE_EMERGENCY_HARD_STOP_PCT = required_float_env("RTRADE_EMERGENCY_HARD_STOP_PCT")

# The new coordinator remains disabled until replay/walk-forward validation. When enabled,
# one owner manages the pair and exposure; the legacy two-worker path remains behind the switch.
RTRADE_PAIR_COORDINATOR_ENABLED = required_bool_env("RTRADE_PAIR_COORDINATOR_ENABLED")
RTRADE_PAIR_POLL_SEC = required_float_env("RTRADE_PAIR_POLL_SEC")
RTRADE_PAIR_MAX_ACTIVE_ROUNDS = required_int_env("RTRADE_PAIR_MAX_ACTIVE_ROUNDS")
RTRADE_PAIR_START_INTERVAL_SEC = required_float_env("RTRADE_PAIR_START_INTERVAL_SEC")
RTRADE_PAIR_DIRECTIONS = tuple(
    side.strip().upper()
    for side in required_env("RTRADE_PAIR_DIRECTIONS").split(",")
    if side.strip()
)
RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC = required_float_env("RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC")
RTRADE_PLACE_FAILURE_BACKOFF_SEC = required_float_env("RTRADE_PLACE_FAILURE_BACKOFF_SEC")
RTRADE_FAST_FILL_RATIO = required_float_env("RTRADE_FAST_FILL_RATIO")
RTRADE_MIN_EDGE_PCT = required_float_env("RTRADE_MIN_EDGE_PCT")
RTRADE_SHOCK_HARD_STOP_PCT = required_float_env("RTRADE_SHOCK_HARD_STOP_PCT")
RTRADE_HARD_STOP_PCT = required_float_env("RTRADE_HARD_STOP_PCT")


class _LivePairVenue:
    """Thin adapter between the pure coordinator and the current Binance venue."""

    def __init__(self, symbol, pair_store=None):
        self.symbol = symbol
        # Used only to release cooldown on cancellation. Rounds retain their own
        # financial state, so a small LRU is sufficient here.
        self._known_tickets = deque(maxlen=max(32, RTRADE_PAIR_MAX_ACTIVE_ROUNDS * 8))
        self._last_place_failures = {}
        self.recovery_blocked = False
        provider_name = mkt.provider_name_for(symbol)
        self.provider_name = provider_name
        self.executor = mkt.provider_by_name(provider_name)
        if self.executor is None:
            raise RuntimeError(f"provider executor indisponibil pentru {symbol}")
        self.audited_executor = AuditedStrategyExecutor(
            self.executor, venue=provider_name)
        self.pair_store = pair_store
        # A Binance client ID is deterministic for one pair leg.  The old recovery
        # retried after one confirmed absence, so keep that behaviour while moving
        # the persistence/lookup state machine into the shared lifecycle facade.
        self.order_lifecycle = TrackedOrderLifecycle(
            StrategyExecutorLifecycleApi(self.executor),
            provider_name=provider_name, venue=provider_name,
            missing_confirmations=1, retry_on_lookup_error=False,
            audit=(self.audited_executor.audit
                   if self.pair_store is not None else None),
        )

    def current_price(self):
        return mkt.get_current_price(self.symbol)

    @staticmethod
    def _intent_id(pair_id, side, kind):
        return f"rtrade:{pair_id}:{kind}:{str(side).lower()}"

    def _persist_callback(self, pair_id, side, kind, requested_qty):
        if self.pair_store is None:
            return lambda _pending: None

        def persist(pending):
            self.pair_store.persist_intent(
                pair_id, side, kind, pending,
                symbol=self.symbol, start_side=side, qty=requested_qty)
        return persist

    def _remember_ticket(self, ticket):
        if ticket is None:
            return None
        if not any(existing.order_id == ticket.order_id
                   for existing in self._known_tickets):
            self._known_tickets.append(ticket)
        return ticket

    def _ticket_from_pending(self, pending, pair_id):
        order_id = pending.get("order_id")
        if not order_id:
            return None
        ticket = PairOrderTicket(
            order_id=str(order_id), side=str(pending["side"]).upper(),
            price=float(
                pending.get("submitted_price")
                or pending.get("requested_price") or 0.0),
            qty=float(
                pending.get("submitted_qty")
                or pending.get("requested_qty")),
            pair_id=pair_id,
        )
        return self._remember_ticket(ticket)

    def _submit_limit_intent(self, side, price, qty, pair_id, *, attempt=1):
        side = side.upper()
        hours = (RTRADE_BUY_NORMAL_HOURS if side == "BUY"
                 else RTRADE_SELL_NORMAL_HOURS)
        client_id = rtrade_client_order_id(pair_id, side, "limit")
        intent = self.order_lifecycle.new_intent(
            intent_id=self._intent_id(pair_id, side, "limit"),
            client_order_id=client_id, symbol=self.symbol, side=side,
            requested_qty=qty, requested_price=price, kind="limit",
            attempt=attempt, metadata={"pair_id": pair_id},
        )
        persist = self._persist_callback(
            pair_id, side, "limit", requested_qty=qty)
        outcome_context = {}

        def submit_once():
            response = mkt.place(
                self.symbol, side, price, qty,
                force=False, cancelorders=False, hours=hours, smart=False,
                cooldown_pair_id=pair_id,
                # The coordinator owns retry/reconciliation. The global outbox
                # must not later recreate a leg from an expired pair.
                caller_owns_retry=True, motivation="rtrade_pair_quote",
                client_order_id=client_id,
                _outcome_context=outcome_context,
            )
            reason = str(outcome_context.get("reason") or "").strip()
            if response is None and reason and reason != "response_without_order_id":
                raise OrderSubmissionRefused(reason)
            return response

        result = self.order_lifecycle.submit(
            intent, persist=persist,
            submit=submit_once,
        )
        return self._ticket_from_pending(result.intent, pair_id), result.intent

    def _canonical_intent(self, record, stored):
        """Upgrade an old rtrade intent in memory without losing observations."""
        pending = dict(stored)
        side = str(pending.get("side") or record["start_side"]).upper()
        kind = str(pending.get("kind") or "limit")
        requested_qty = pending.get("requested_qty", pending.get("qty"))
        requested_price = pending.get("requested_price", pending.get("price"))
        pending.update({
            "intent_id": pending.get("intent_id")
                         or self._intent_id(record["pair_id"], side, kind),
            "client_order_id": pending["client_order_id"],
            "symbol": pending.get("symbol") or record["symbol"],
            "side": side,
            "kind": kind,
            "requested_qty": float(requested_qty),
            "requested_price": (None if requested_price is None
                                else float(requested_price)),
            "attempt": int(pending.get("attempt") or 1),
            "created_at": float(
                pending.get("created_at") or record.get("created_ts")
                or time.time()),
            "lookup_misses": int(pending.get("lookup_misses") or 0),
            "pair_id": pending.get("pair_id") or record["pair_id"],
        })
        return pending

    def recover_intent(self, record, stored):
        """Recover one fsynced pair intent without guessing venue state.

        A lookup/status error leaves the round active and blocks startup.  Only a
        confirmed absence permits one idempotent resubmission with the same
        deterministic client order ID.
        """
        pending = self._canonical_intent(record, stored)
        side = pending["side"]
        kind = pending["kind"]
        pair_id = record["pair_id"]
        persist = self._persist_callback(
            pair_id, side, kind, requested_qty=pending["requested_qty"])
        persist(pending)
        result = self.order_lifecycle.reconcile(pending, persist=persist)

        if result.outcome == "absent":
            if kind != "limit":
                raise RuntimeError(
                    f"intentie {kind} absenta; retransmiterea automata este blocata")
            _, submitted = self._submit_limit_intent(
                side, pending["requested_price"], pending["requested_qty"],
                pair_id, attempt=int(pending.get("attempt") or 1) + 1)
            result = self.order_lifecycle.reconcile(submitted, persist=persist)
            if result.outcome == "absent":
                return None

        if not result.intent.get("order_id"):
            raise RuntimeError(
                f"intentie {kind}:{side} ambigua; lookup/status indisponibil")
        if result.status is None:
            raise RuntimeError(
                f"intentie {kind}:{side} are order_id dar status indisponibil")

        ticket = self._ticket_from_pending(result.intent, pair_id)
        snapshot = PairOrderSnapshot(
            status=result.status.status,
            filled_qty=result.status.filled_qty,
            cost=result.status.cost,
            fee=result.status.fee,
        )
        return ticket, snapshot

    def place_limit(self, side, price, qty, pair_id):
        side = side.upper()
        self._last_place_failures.pop(side, None)
        from providers.quantity import balance_cap_quantity
        available_qty, required_asset = balance_cap_quantity(
            self.executor.free_balance, self.symbol, side, price)
        if required_asset:
            # Do not compare against requested quantity: QuantityDecision and venue
            # mechanics already cap it to available balance. Back off only when the
            # required asset has no usable free balance.
            if available_qty is not None and float(available_qty) <= 1e-12:
                self._last_place_failures[side] = (
                    f"{side.lower()}_insufficient_funds:{required_asset}")
                print(
                    f"[{self.symbol}] {side} fonduri insuficiente: "
                    f"disponibil=0.00000000 {required_asset}")
                return None
        ticket, _pending = self._submit_limit_intent(
            side, price, qty, pair_id)
        if ticket is None and _pending.get("refusal_reason"):
            # A guard refusal happened before any provider submit. It is safe to
            # finish this round and let the strategy backoff create a fresh intent;
            # venue response-loss recovery would be both false and duplicative.
            return None
        if ticket is None and self.pair_store is not None:
            # One bounded recovery closes the live response-loss gap without a
            # sleep/poll loop.  A confirmed absence may cause one second submit
            # with the same deterministic client ID; lookup ambiguity blocks new
            # rounds and leaves the fsynced intent available for restart recovery.
            record = next(
                (candidate for candidate in self.pair_store.active(self.symbol)
                 if candidate.get("pair_id") == pair_id),
                None,
            )
            if record is None:
                self.recovery_blocked = True
                raise RuntimeError(
                    f"pair={pair_id}: intentia persistata nu poate fi recitita")
            stored = record.get("intents", {}).get(f"limit:{side}")
            if stored is None:
                self.recovery_blocked = True
                raise RuntimeError(
                    f"pair={pair_id}: intentia {side} lipseste dupa submit")
            try:
                recovered = self.recover_intent(record, stored)
            except Exception:
                self.recovery_blocked = True
                raise
            if recovered is not None:
                ticket, _snapshot = recovered
        return ticket

    def last_place_failure_reason(self, side):
        return self._last_place_failures.get(side.upper())

    def market_exit_allowed(self, exposure_side, loss_fraction, reason):
        """Decide whether a normal hard stop has trend confirmation for MARKET.

        ``shadow`` only observes; ``live`` applies the decision. The emergency threshold
        remains mandatory regardless of trend so a stale signal cannot leave unlimited risk.
        """
        emergency = loss_fraction >= RTRADE_EMERGENCY_HARD_STOP_PCT
        decision = _market_regime_decision(self.symbol)
        adverse = decision.adverse_to(exposure_side)
        justified = adverse or emergency
        print(
            f"[{self.symbol}] dynamic-market-exit mode={RTRADE_DYNAMIC_MARKET_EXIT_MODE} "
            f"exposure={exposure_side} regime={decision.regime} "
            f"strength={decision.strength} signal_reason={decision.reason} "
            f"loss={loss_fraction:.4%} emergency={emergency} "
            f"would_market={justified} reason={reason}")
        if RTRADE_DYNAMIC_MARKET_EXIT_MODE in {"off", "shadow"}:
            return True
        return justified

    def place_market_exit(self, side, qty, reason, pair_id=None):
        """Place a spot market exit reserved for reducing hard-stop exposure.

        Normal orders remain on ``mkt.place``/``place_safe_order``. This risk exit
        bypasses profit, cooldown, and weight checks, but never bypasses balance,
        fee-cap, precision, preflight, or client-order-ID auditing.
        """
        side = side.upper()
        price = float(self.current_price() or 0.0)
        if price <= 0:
            print(f"[{self.symbol}] {side} hard-stop BLOCAT: pret indisponibil")
            return None
        decision = decide_quantity(
            self.executor, self.symbol, side, price, qty, apply_policy=False)
        final_qty = float(decision.final_qty)
        if final_qty <= 0:
            print(f"[{self.symbol}] {side} hard-stop BLOCAT: "
                  f"{decision.refuse_reason} asset={decision.balance_asset}")
            return None
        precision = self.audited_executor.pair_precision(self.symbol)
        if precision is not None:
            quantum = Decimal(1).scaleb(-int(precision.volume_decimals))
            final_qty = float(Decimal(str(final_qty)).quantize(quantum, rounding=ROUND_DOWN))
            if final_qty <= 0 or final_qty < float(precision.order_min or 0.0):
                print(f"[{self.symbol}] {side} hard-stop BLOCAT: qty {final_qty} "
                      f"sub minim {precision.order_min}")
                return None
        kind = f"rtrade:{reason}:{pair_id or 'unknown-pair'}"
        intent_id = f"rtrade-{pair_id or 'unknown'}-{reason}-{side.lower()}"
        client_id = rtrade_client_order_id(
            pair_id or "unknown", side, "hard_stop")
        if self.pair_store is not None and pair_id:
            self.pair_store.intent(
                pair_id, side, None, final_qty, client_id, kind="hard_stop",
                symbol=self.symbol)
        self.audited_executor.preflight_order(
            self.symbol, side, final_qty, price=None, market=True, kind=kind)
        order_id = self.audited_executor.submit_order_with_intent(
            intent_id, self.symbol, side, final_qty, price=None,
            market=True, kind=kind, reference_price=price,
            client_order_id=client_id)
        ticket = PairOrderTicket(
            order_id=str(order_id), side=side, price=price, qty=final_qty,
            pair_id=pair_id)
        self._known_tickets.append(ticket)
        if self.pair_store is not None and pair_id:
            self.pair_store.accepted(
                pair_id, side, ticket.order_id, kind="hard_stop")
        return ticket

    def order_status(self, order_id):
        status = self.executor.order_status(self.symbol, str(order_id))
        return PairOrderSnapshot(
            status=status.status, filled_qty=status.filled_qty,
            cost=status.cost, fee=status.fee)

    def cancel(self, order_id):
        try:
            self.executor.cancel_order(self.symbol, str(order_id))
            for ticket in getattr(self, "_known_tickets", []):
                if ticket.order_id == str(order_id) and ticket.pair_id:
                    from lock import trade_cooldown
                    trade_cooldown.release_pair_leg(
                        self.symbol, ticket.pair_id, ticket.side)
                    break
            return True
        except Exception as exc:  # noqa: BLE001 — the coordinator decides how to fail closed.
            print(f"[{self.symbol}] pair cancel {order_id} esuat: {exc}")
            return False


def _trend_too_strong(symbol):
    """Return whether a clear trend should keep the spread bot idle.

    A clear trend means ``|gradient_recent| > K * epsilon`` and risks adverse selection.
    Missing or failed trend data fails open, matching the pipeline's trend wait.
    """
    if not RTRADE_TREND_FILTER_ENABLED:
        return False
    decision = _market_regime_decision(symbol)
    if decision.directional:
        print(f"[{symbol}] rtrade STA DEOPARTE: regim {decision.regime} "
              f"strength={decision.strength} reason={decision.reason} "
              f"fereastra={RTRADE_TREND_WINDOW_SEC:.0f}s")
    return decision.directional


def _market_regime_decision(symbol) -> MarketRegimeDecision:
    """Adapt the existing cache source to the provider-neutral regime evaluator."""
    if not RTRADE_TREND_FILTER_ENABLED:
        return mkt.market_regime(
            symbol, horizon="short", snapshot={},
            strength_threshold=RTRADE_TREND_FILTER_K, allow_fallback=False)
    try:
        import cacheManager as cm
        dyn = cm.get_short_trend_manager().get_instant_trend_for_window(
            symbol, RTRADE_TREND_WINDOW_SEC)
    except Exception as exc:
        return mkt.market_regime(
            symbol, horizon="short", snapshot={},
            strength_threshold=RTRADE_TREND_FILTER_K,
            allow_fallback=RTRADE_TREND_OHLC_FALLBACK_ENABLED)
    return mkt.market_regime(
        symbol, horizon="short", snapshot=dyn or {},
        strength_threshold=RTRADE_TREND_FILTER_K,
        allow_fallback=RTRADE_TREND_OHLC_FALLBACK_ENABLED)


def _order_fully_filled(symbol, order_id):
    """Support the legacy loop through provider-neutral order status."""
    if not order_id:
        return False
    try:
        return mkt.order_status(symbol, str(order_id)).fully_filled
    except Exception as exc:
        print(f"[{symbol}] status ordin {order_id} indisponibil: {exc}")
        return False


def _cancel_order_confirmed(symbol, order_id):
    try:
        mkt.cancel_order(symbol, str(order_id))
        return True
    except Exception as exc:
        print(f"[{symbol}] cancel ordin {order_id} neconfirmat: {exc}")
        return False


def _place_failure_backoff(reason):
    """Return ``(side, seconds)`` for a terminal placement failure.

    Completely absent funds have an explicit reason. Other ``*_place_failed`` cases,
    including price guards, minimum notional, weight limits, and API refusal, also need
    throttling so transient errors cannot become an order loop with a new pair ID each time.
    """
    reason = str(reason or "").strip().lower()
    marker = "_insufficient_funds:"
    if marker in reason:
        side = reason.split(marker, 1)[0].upper()
        if side in {"BUY", "SELL"}:
            return side, RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC
    suffix = "_place_failed"
    if reason.endswith(suffix):
        side = reason[:-len(suffix)].upper()
        if side in {"BUY", "SELL"}:
            return side, RTRADE_PLACE_FAILURE_BACKOFF_SEC
    return None, 0.0


def _followup_force(symbol, side):
    """Choose MARKET force for a post-fill flip only when trend is not adverse.

    Selling into a clear decline or buying into a clear rise is adverse, so return False
    and leave a patient limit at the flip price. Weak, flat, favorable, or unavailable
    trend data retains immediate market behavior. This prevents desperate execution
    against the trend.
    """
    if not RTRADE_TREND_FILTER_ENABLED:
        return True
    decision = _market_regime_decision(symbol)
    if not decision.directional:
        return True   # A weak or flat trend permits an immediate flip.
    su = (side or "").upper()
    exposure = "LONG" if su == "SELL" else "SOLD"
    adverse = decision.adverse_to(exposure)
    if adverse:
        print(f"[{symbol}] followup {su}: regim {decision.regime} ADVERS "
              "-> limita rabdatoare, NU piata")
        return False
    return True


class TradingBot:
    def __init__(self, symbol, qty, DEFAULT_ADJUSTMENT_PERCENT):
        self.symbol = symbol
        self.qty = qty
        self.transaction_state = "COMPLETED"  # Initial state.
        current_price = api.get_current_price(symbol)
        self.filled_buy_price = round(current_price * (1 - RTRADE_INITIAL_SPREAD_PCT), 4)
        self.filled_sell_price = round(current_price * (1 + RTRADE_INITIAL_SPREAD_PCT), 4)
        self.buy_filled = False
        self.sell_filled = False
        self.DEFAULT_ADJUSTMENT_PERCENT = DEFAULT_ADJUSTMENT_PERCENT
        self.lock = threading.Lock()  # Synchronization lock.

    @property
    def is_buy_filled(self):
        with self.lock:
            return self.buy_filled

    @property
    def is_sell_filled(self):
        with self.lock:
            return self.sell_filled
        
    def mark_buy_filled(self, filled_buy_price=None):
        with self.lock:
            self.buy_filled = True
            self.sell_filled = False
            if filled_buy_price:
                self.filled_buy_price = filled_buy_price
            return self.filled_buy_price

    def mark_sell_filled(self, filled_sell_price=None):
        with self.lock:
            self.buy_filled = False
            self.sell_filled = True
            if filled_sell_price:
                self.filled_sell_price = filled_sell_price
            return self.filled_sell_price
        
    def repetitive_buy(self, current_price, filled_sell_price):
        adjustment_percent = self.DEFAULT_ADJUSTMENT_PERCENT
        failure_count = 1  # Count placement failures.
        max_failures = RTRADE_MAX_FAILURES  # Maximum accepted failures.

        while True:

            current_price = api.get_current_price(self.symbol)

            if self.is_sell_filled:
                adjustment_percent = max(MIN_adjustment_percent, adjustment_percent - adjustment_percent * RTRADE_BUY_DECAY_PCT)

            target_buy_price = round(current_price * (1 - adjustment_percent), 4)
            print(f"[{self.symbol}] Order BUY initiated at {target_buy_price:.2f} procent {adjustment_percent}%")

            if self.is_buy_filled:
                print(f"[{self.symbol}] Ignore BUY order. It was previously filled at {self.filled_buy_price:.2f}")
                return self.mark_buy_filled(self.filled_buy_price)

            buy_order = None
            h = RTRADE_BUY_DESPERATE_HOURS_BASE / failure_count
            try:
                if self.is_sell_filled: # Desperate follow-up path.
                    if adjustment_percent == MIN_adjustment_percent:
                        print(f"[{self.symbol}] sunt disperat!")
                        buy_order = mkt.place(self.symbol, "BUY", target_buy_price, self.qty,
                            safeback_seconds=RTRADE_DESPERATE_SAFEBACK_SEC, force=False, cancelorders=True, hours=h, smart=False)
                    else:
                        buy_order = mkt.place(self.symbol, "BUY", target_buy_price, self.qty,
                            safeback_seconds=RTRADE_DESPERATE_SAFEBACK_SEC, force=False, cancelorders=True, hours=h, smart=False)
                else:
                    buy_order = mkt.place(self.symbol, "BUY", target_buy_price, self.qty, cancelorders=True, hours=RTRADE_BUY_NORMAL_HOURS, smart=False)
            except po.WeightLimitBlock as e:
                print(f"[{self.symbol}] 24h limit reached — exiting without retry ({e})")
                return None

            if buy_order is None:
                print(f"[{self.symbol}] Order BUY failed, retryed {failure_count} times. Retrying again ...")
                time.sleep(WAIT_FOR_ORDER)
                failure_count += 1
                if failure_count > max_failures:
                    print(f"[{self.symbol}] Order BUY failed {failure_count} times. Exiting.")
                    return None
                continue

            failure_count = 1 # reset failure count after a successful order placement
            
            time.sleep(WAIT_FOR_ORDER)
            order_id = buy_order['orderId']
            self.filled_buy_price = round(float(buy_order['price']), 4)
            
            if _order_fully_filled(self.symbol, order_id):
                print(f"[{self.symbol}] BUY order filled at {self.filled_buy_price:.2f}")
                print(f"[{self.symbol}] SELL disperat tot 1....")
                mkt.place(self.symbol, "SELL", api.get_current_price(self.symbol) * (1 + RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                    force=_followup_force(self.symbol, "SELL"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                return self.mark_buy_filled(self.filled_buy_price)


            filled_buy_price = mkt.latest_fill_price(
                self.symbol, "BUY", WAIT_FOR_ORDER)
            if filled_buy_price is not None:
                print(f"[{self.symbol}] BUY order may have been filled :-) at {filled_buy_price:.2f}")
                print(f"[{self.symbol}] SELL disperat tot 2 ....")
                mkt.place(self.symbol, "SELL", api.get_current_price(self.symbol) * (1 + RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                    force=_followup_force(self.symbol, "SELL"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                return self.mark_buy_filled(filled_buy_price)

            current_price = api.get_current_price(self.symbol)
            if current_price > filled_sell_price and not u.are_close(current_price, filled_sell_price, RTRADE_BAD_DAY_TOLERANCE_PCT):
                print(f"[{self.symbol}] Bed day :-(. Trying BUY at current price - x2 {current_price:.2f}")
                adjustment_percent = RTRADE_BAD_DAY_MULTIPLIER * self.DEFAULT_ADJUSTMENT_PERCENT
            # if arrived here it means
            # current order was not filled , so try cancel and retry in the loop
            if not _cancel_order_confirmed(self.symbol, order_id):
                if _order_fully_filled(self.symbol, order_id):
                    print(f"[{self.symbol}] Cancel BUY order failed. Maybe it was filled :-)? Moving to SELL ...")
                    print(f"[{self.symbol}] SELL disperat tot 3 ....")
                    mkt.place(self.symbol, "SELL", api.get_current_price(self.symbol) * (1 + RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                    force=_followup_force(self.symbol, "SELL"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                    return self.mark_buy_filled(self.filled_buy_price)
                else:
                    print(f"[{self.symbol}] Cancel BUY order failed. Someone canceled it. Continuing BUY...")


    def repetitive_sell(self, current_price, filled_buy_price):
        adjustment_percent = self.DEFAULT_ADJUSTMENT_PERCENT
        failure_count = 1  # Count placement failures.
        max_failures = RTRADE_MAX_FAILURES  # Maximum accepted failures.

        while True:

            current_price = api.get_current_price(self.symbol)

            if self.is_buy_filled:
                adjustment_percent = max(MIN_adjustment_percent, adjustment_percent - adjustment_percent * RTRADE_SELL_DECAY_PCT)

            target_sell_price = round(current_price * (1 + adjustment_percent), 4)
            print(f"[{self.symbol}] Order SELL initiated at {target_sell_price:.2f} procent {adjustment_percent}%")

            if self.is_sell_filled:
                print(f"[{self.symbol}] Ignore SELL order. It was previously filled at {self.filled_sell_price:.2f}")
                return self.mark_sell_filled(self.filled_sell_price)

            sell_order = None
            h = RTRADE_SELL_DESPERATE_HOURS_BASE / failure_count
            try:
                if self.is_buy_filled: # Desperate follow-up path.
                    if adjustment_percent == MIN_adjustment_percent:
                        print(f"[{self.symbol}] sunt disperat!")
                        sell_order = mkt.place(self.symbol, "SELL", target_sell_price, self.qty,
                            safeback_seconds=RTRADE_DESPERATE_SAFEBACK_SEC, force=False, cancelorders=True, hours=h, smart=False)
                    else:
                        sell_order = mkt.place(self.symbol, "SELL", target_sell_price, self.qty,
                            safeback_seconds=RTRADE_DESPERATE_SAFEBACK_SEC, force=False, cancelorders=True, hours=h, smart=False)
                else:
                    sell_order = mkt.place(self.symbol, "SELL", target_sell_price, self.qty, cancelorders=True, hours=RTRADE_SELL_NORMAL_HOURS, smart=False)
            except po.WeightLimitBlock as e:
                print(f"[{self.symbol}] 24h limit reached (SELL) — exiting without retry ({e})")
                return None

            if sell_order is None:
                print(f"[{self.symbol}] Order SELL failed, retryed {failure_count} times. Retrying again ...")
                time.sleep(WAIT_FOR_ORDER)
                failure_count += 1
                if failure_count > max_failures:
                    print(f"[{self.symbol}] Order SELL failed {failure_count} times. Exiting.")
                    return None
                continue
            
            failure_count = 1 # reset failure count after a successful order placement

            time.sleep(WAIT_FOR_ORDER)
            order_id = sell_order['orderId']
            self.filled_sell_price = round(float(sell_order['price']), 4)

            if _order_fully_filled(self.symbol, order_id):
                print(f"[{self.symbol}] SELL order filled at {self.filled_sell_price:.2f}")
                print(f"[{self.symbol}] BUY disperat tot 1....")
                mkt.place(self.symbol, "BUY", api.get_current_price(self.symbol) * (1 - RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                    force=_followup_force(self.symbol, "BUY"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                return self.mark_sell_filled(self.filled_sell_price)


            filled_sell_price = mkt.latest_fill_price(
                self.symbol, "SELL", WAIT_FOR_ORDER)
            if filled_sell_price is not None:
                print(f"[{self.symbol}] SELL order may have been filled :-) at {filled_sell_price:.2f}")
                print(f"[{self.symbol}] BUY disperat tot 2....")
                mkt.place(self.symbol, "BUY", api.get_current_price(self.symbol) * (1 - RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                    force=_followup_force(self.symbol, "BUY"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                return self.mark_sell_filled(filled_sell_price)

            current_price = api.get_current_price(self.symbol)
            if current_price < filled_buy_price and not u.are_close(current_price, filled_buy_price, RTRADE_BAD_DAY_TOLERANCE_PCT):
                print(f"[{self.symbol}] Bed day :-(. Trying SELL at current price + x2 {current_price:.2f}")
                adjustment_percent = RTRADE_BAD_DAY_MULTIPLIER * self.DEFAULT_ADJUSTMENT_PERCENT
            # if arrived here it means
            # current order was not filled , so try cancel and retry in the loop
            if not _cancel_order_confirmed(self.symbol, order_id):
                if _order_fully_filled(self.symbol, order_id):
                    print(f"[{self.symbol}] Cancel SELL order failed. Maybe it was filled :-)? Moving to BUY ...")
                    print(f"[{self.symbol}] BUY disperat tot 3....")
                    mkt.place(self.symbol, "BUY", api.get_current_price(self.symbol) * (1 - RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                        force=_followup_force(self.symbol, "BUY"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                    return self.mark_sell_filled(self.filled_sell_price)
                else:
                    print(f"[{self.symbol}] Cancel SELL order failed. Someone canceled it. Continuing sell...")

    def _run_pair(self, executor, current_price):
        """Run both sides concurrently on the bot's persistent workers.

        ``Future.result`` propagates worker exceptions to the main loop, which already
        performs defensive reconciliation by canceling recent orders. The old version
        created two threads per round and lost their exceptions on stderr.
        """
        buy_future = executor.submit(
            self.repetitive_buy, current_price, self.filled_sell_price)
        sell_future = executor.submit(
            self.repetitive_sell, current_price, self.filled_buy_price)
        # Wait for both sides before propagating an exception. Resolving BUY immediately
        # could leave the old SELL active after a fast BUY failure while the main loop
        # starts another round over it.
        wait((buy_future, sell_future))
        return buy_future.result(), sell_future.result()

    def _run_coordinator_forever(self):
        pair_store = getattr(self, "pair_store", None) or RTradePairStore()
        venue = _LivePairVenue(self.symbol, pair_store=pair_store)
        policy = PairPolicy(
            adjustment_fraction=self.DEFAULT_ADJUSTMENT_PERCENT,
            quote_ttl_sec=WAIT_FOR_ORDER,
            poll_sec=RTRADE_PAIR_POLL_SEC,
            fast_fill_ratio=RTRADE_FAST_FILL_RATIO,
            min_edge_fraction=RTRADE_MIN_EDGE_PCT,
            shock_hard_stop_fraction=RTRADE_SHOCK_HARD_STOP_PCT,
            hard_stop_fraction=RTRADE_HARD_STOP_PCT,
        )
        if RTRADE_PAIR_MAX_ACTIVE_ROUNDS < 1:
            raise ValueError("RTRADE_PAIR_MAX_ACTIVE_ROUNDS trebuie sa fie >= 1")
        if RTRADE_PAIR_START_INTERVAL_SEC <= 0:
            raise ValueError("RTRADE_PAIR_START_INTERVAL_SEC trebuie sa fie > 0")
        if (not RTRADE_PAIR_DIRECTIONS
                or any(side not in {"BUY", "SELL"} for side in RTRADE_PAIR_DIRECTIONS)):
            raise ValueError("RTRADE_PAIR_DIRECTIONS accepta numai BUY,SELL")
        if RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC <= 0:
            raise ValueError("RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC trebuie sa fie > 0")
        if RTRADE_PLACE_FAILURE_BACKOFF_SEC <= 0:
            raise ValueError("RTRADE_PLACE_FAILURE_BACKOFF_SEC trebuie sa fie > 0")
        if RTRADE_DYNAMIC_MARKET_EXIT_MODE not in {"off", "shadow", "live"}:
            raise ValueError("RTRADE_DYNAMIC_MARKET_EXIT_MODE: off|shadow|live")
        if not RTRADE_HARD_STOP_PCT <= RTRADE_EMERGENCY_HARD_STOP_PCT < 1:
            raise ValueError(
                "RTRADE_EMERGENCY_HARD_STOP_PCT must be >= hard-stop and < 1")

        # Each coordinator exclusively owns one round's order IDs and inventory. An
        # exposed round continues managing its exit without blocking other rounds up
        # to the configured per-symbol limit.
        active = []
        recovery_blocked = False
        for record in pair_store.active(self.symbol):
            state = record.get("state")
            if not state:
                try:
                    tickets = []
                    snapshots = {}
                    for intent in record.get("intents", {}).values():
                        recovered = venue.recover_intent(record, intent)
                        if recovered is None:
                            continue
                        ticket, snap = recovered
                        order_id = ticket.order_id
                        snapshots[order_id] = vars(snap).copy()
                        tickets.append({
                            "order_id": order_id, "side": ticket.side,
                            "price": ticket.price, "qty": ticket.qty,
                            "active": snap.status not in {"closed", "canceled", "expired"},
                            "pair_id": record["pair_id"],
                        })
                    if not tickets:
                        terminal_state = {
                            "pair_id": record["pair_id"], "qty": record["qty"],
                            "start_side": record["start_side"], "phase": "failed",
                            "reason": "recovery_intent_not_submitted", "shock": False,
                            "elapsed_sec": 0, "first_fill_elapsed_sec": None,
                            "first_fill_side": None, "tickets": [], "snapshots": {},
                        }
                        pair_store.checkpoint(
                            record["pair_id"], terminal_state, terminal=True)
                        print(f"[{self.symbol}] pair={record['pair_id']} recovery: "
                              "intentia nu a putut fi plasata; inchisa controlat")
                        continue
                    state = {
                        "pair_id": record["pair_id"], "qty": record["qty"],
                        "start_side": record["start_side"], "phase": "quoting",
                        "reason": "recovered_by_client_order_id", "shock": False,
                        "elapsed_sec": max(0.0, time.time() - record["created_ts"]),
                        "first_fill_elapsed_sec": None, "first_fill_side": None,
                        "tickets": tickets, "snapshots": snapshots,
                    }
                    pair_store.checkpoint(record["pair_id"], state, terminal=False)
                except Exception as exc:
                    print(f"[{self.symbol}] RECOVERY BLOCAT: pair={record.get('pair_id')} {exc}")
                    recovery_blocked = True
                    continue
            try:
                coordinator = PairCoordinator.from_state(venue, policy, state)
                active.append(coordinator)
                print(f"[{self.symbol}] pair={coordinator.pair_id} adoptat "
                      f"phase={coordinator.phase} tickets={len(coordinator.tickets)}")
            except Exception as exc:
                print(f"[{self.symbol}] RECOVERY BLOCAT: {exc}")
                recovery_blocked = True

        try:
            known_client_ids = {
                intent.get("client_order_id")
                for rec in pair_store.active(self.symbol)
                for intent in rec.get("intents", {}).values()
            }
            orphan_orders = [
                order for order in venue.executor.open_orders(self.symbol)
                if str(order.get("clientOrderId") or "").startswith("RT_")
                and order.get("clientOrderId") not in known_client_ids
            ]
            for order in orphan_orders:
                order_id = str(order["orderId"])
                client_id = order.get("clientOrderId")
                # An RT_ order without local intent cannot be safely associated with a
                # round because its ID is hashed. Cancel it instead of inventing state.
                venue.executor.cancel_order(self.symbol, order_id)
                print(f"[{self.symbol}] recovery: ordin RT_ orfan anulat "
                      f"order_id={order_id} client_id={client_id}")
            if orphan_orders:
                remaining = {
                    str(order.get("orderId"))
                    for order in venue.executor.open_orders(self.symbol)
                }
                not_canceled = [
                    str(order["orderId"]) for order in orphan_orders
                    if str(order["orderId"]) in remaining
                ]
                if not_canceled:
                    print(f"[{self.symbol}] RECOVERY BLOCAT: anulare neconfirmata "
                          f"pentru ordinele RT_ {not_canceled}")
                    recovery_blocked = True
        except Exception as exc:
            print(f"[{self.symbol}] RECOVERY BLOCAT: inventar exchange indisponibil ({exc})")
            recovery_blocked = True
        last_start_at = float("-inf")
        next_direction = 0
        side_backoff_until = {"BUY": 0.0, "SELL": 0.0}
        while True:
            try:
                now = time.monotonic()
                _touch_rtrade_heartbeat(now=now)
                survivors = []
                checkpoints = []
                for coordinator in active:
                    outcome = coordinator.step(now=now)
                    export_state = getattr(coordinator, "export_state", None)
                    if callable(export_state):
                        checkpoints.append((
                            coordinator.pair_id, export_state(), outcome.terminal))
                    if outcome.terminal:
                        print(
                            f"[{self.symbol}] pair={outcome.pair_id} "
                            f"phase={outcome.phase} shock={outcome.shock} "
                            f"latency={outcome.fill_latency_sec} "
                            f"buy={outcome.buy_qty:.6f} sell={outcome.sell_qty:.6f} "
                            f"net={outcome.net_qty:.6f} "
                            f"cashflow={outcome.gross_pnl:.2f} "
                            f"fees={outcome.fees:.2f} reason={outcome.reason}")
                    else:
                        survivors.append(coordinator)
                pair_store.checkpoint_many(checkpoints)
                active = survivors

                can_start = (
                    not recovery_blocked
                    and not getattr(venue, "recovery_blocked", False)
                    and len(active) < RTRADE_PAIR_MAX_ACTIVE_ROUNDS
                    and now - last_start_at >= RTRADE_PAIR_START_INTERVAL_SEC
                )
                if can_start and not _trend_too_strong(self.symbol):
                    current_price = venue.current_price()
                    if current_price is not None:
                        start_side = None
                        for _ in range(len(RTRADE_PAIR_DIRECTIONS)):
                            candidate = RTRADE_PAIR_DIRECTIONS[next_direction]
                            next_direction = (
                                next_direction + 1) % len(RTRADE_PAIR_DIRECTIONS)
                            if now >= side_backoff_until[candidate]:
                                start_side = candidate
                                break
                        if start_side is None:
                            time.sleep(RTRADE_PAIR_POLL_SEC)
                            continue
                        round_qty = RTRADE_NOTIONAL_USDC / float(current_price)
                        reserved_pair_id = uuid.uuid4().hex
                        coordinator = PairCoordinator(
                            venue, round_qty, policy, start_side=start_side)
                        outcome = coordinator.start(
                            current_price, pair_id=reserved_pair_id)
                        export_state = getattr(coordinator, "export_state", None)
                        if callable(export_state):
                            pair_store.checkpoint(
                                coordinator.pair_id, export_state(),
                                terminal=outcome.terminal)
                        last_start_at = now
                        if outcome.terminal:
                            failed_side, backoff_sec = _place_failure_backoff(
                                outcome.reason)
                            if failed_side in side_backoff_until:
                                side_backoff_until[failed_side] = now + backoff_sec
                                print(
                                    f"[{self.symbol}] {failed_side} backoff "
                                    f"{backoff_sec:.0f}s dupa esec de plasare "
                                    f"({outcome.reason})")
                            print(
                                f"[{self.symbol}] pair={outcome.pair_id} "
                                f"direction={start_side}-first "
                                f"phase={outcome.phase} reason={outcome.reason}")
                        else:
                            active.append(coordinator)
                            print(
                                f"[{self.symbol}] pair={outcome.pair_id} started "
                                f"direction={start_side}-first "
                                f"active={len(active)}/"
                                f"{RTRADE_PAIR_MAX_ACTIVE_ROUNDS}")
                time.sleep(RTRADE_PAIR_POLL_SEC)
            except Exception as exc:  # noqa: BLE001 — retain rounds for reconciliation.
                print(f"[{self.symbol}] pair coordinator error: {exc}")
                # Do not globally cancel recent orders in a multi-round registry; that
                # could destroy valid legs owned by other pair IDs.
                time.sleep(RTRADE_PAIR_POLL_SEC)

    def run(self):
        _touch_rtrade_heartbeat(force=True)
        if RTRADE_PAIR_COORDINATOR_ENABLED:
            return self._run_coordinator_forever()
        # Reuse exactly two workers per bot across rounds. BUY and SELL remain concurrent
        # while avoiding thread churn and lost exceptions.
        prefix = f"rtrade-{self.symbol}"
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix=prefix) as executor:
            while True:
                _touch_rtrade_heartbeat()
                try:
                    current_price = api.get_current_price(self.symbol)
                    if current_price is None:
                        print(f"[{self.symbol}] Failed to fetch current price. Retrying in {WAIT_FOR_ORDER} seconds...")
                        time.sleep(WAIT_FOR_ORDER)
                        continue
                    print(f"[{self.symbol}] Current price: {current_price:.2f}")

                    # If the asset trends clearly, keep the spread bot idle for this cycle
                    # to avoid catching a falling knife. Retry on the next iteration.
                    if _trend_too_strong(self.symbol):
                        time.sleep(WAIT_FOR_ORDER)
                        continue

                    buy_result, sell_result = self._run_pair(executor, current_price)

                    if not buy_result or not sell_result:
                        continue

                    filled_buy_price = buy_result + RTRADE_ZERO_EPSILON  # avoid zero
                    filled_sell_price = sell_result

                    print(f"[{self.symbol}] Transaction complete: Bought at {filled_buy_price:.2f}, Sold at {filled_sell_price:.2f}")
                    if filled_buy_price < filled_sell_price:
                        print(f"[{self.symbol}] PROFIT: Profit ratio {filled_sell_price / filled_buy_price:.2f}")
                    else:
                        print(f"[{self.symbol}] LOSS: Loss ratio {filled_sell_price / filled_buy_price:.2f}")

                    time.sleep(1)

                    # Reset for the next round.
                    with self.lock:
                        self.buy_filled = self.sell_filled = False
                except Exception as e:
                    print(f"[{self.symbol}] Unexpected error: {e}")
                    # Worker exceptions arrive here through Future.result().
                    api.cancel_recent_orders("SELL", self.symbol, WAIT_FOR_ORDER)
                    api.cancel_recent_orders("BUY", self.symbol, WAIT_FOR_ORDER)
                    time.sleep(1)
                
                
DEFAULT_ADJUSTMENT_PERCENT = required_float_env("RTRADE_DEFAULT_ADJUSTMENT_PCT")
print(f"[INFO] DEFAULT_ADJUSTMENT_PERCENT = {DEFAULT_ADJUSTMENT_PERCENT}")

# Keep actual startup, including WebSocket setup and the infinite live order loop, under
# ``__main__``. Historically it ran during import and could start live trading from a test.
# Fleet startup executes ``python rtrade.py``, so production behavior remains unchanged
# while importing the module is safe.
if __name__ == "__main__":
    # Explicit user-data bridge: placement guards inspect order/fill history, so the WS
    # keeps that cache fresh instead of relying solely on three-minute polling.
    import cacheManager as cm
    cm.enable_real_ws_event_sync()

    initial_price = float(api.get_current_price(sym.taosymbol) or 0.0)
    if initial_price <= 0:
        raise RuntimeError(f"Price unavailable for {sym.taosymbol}")
    initial_qty = RTRADE_NOTIONAL_USDC / initial_price
    bot = TradingBot(sym.taosymbol, initial_qty,
                     DEFAULT_ADJUSTMENT_PERCENT=DEFAULT_ADJUSTMENT_PERCENT)
    #bot = TradingBot(sym.taosymbol, api.quantities[sym.taosymbol], DEFAULT_ADJUSTMENT_PERCENT=DEFAULT_ADJUSTMENT_PERCENT)
    bot.run()

    
