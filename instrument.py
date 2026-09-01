# instrument.py
"""Explicit instrument descriptor: provider, symbol, assets, and parameters.

Consumers operate on ``Instrument`` instances instead of inferring a venue from a
symbol. The provider name is resolved through ``MarketApi.provider_by_name`` so the
same asset can be represented independently on multiple venues.

``free()`` queries the instrument provider with the base asset (or the full symbol
when no base is configured). ``place()`` either delegates to an internally guarded
provider or runs the shared policy pipeline before provider mechanics.
"""
import os
import sys
import time
from typing import Optional, List, Callable, Any

from providers.market_api import api as _default_api
from providers.execution_audit import ExecutionAudit
import order_guard
import order_outcomes_log as _outcomes_log
from lock import trade_cooldown


_EXECUTION_AUDIT = ExecutionAudit()


def _accepted_order_payload(payload):
    """Return whether a provider payload proves venue acceptance, never fill."""
    if not isinstance(payload, dict):
        return False
    status = str(payload.get("status") or "").upper()
    if status in {"REJECTED", "CANCELED", "CANCELLED", "EXPIRED", "FAILED"}:
        return False
    native_id = payload.get("orderId", payload.get("id", payload.get("txid")))
    if isinstance(native_id, (list, tuple)):
        return any(str(item).strip() for item in native_id)
    return native_id is not None and str(native_id).strip() != ""


def _order_id_from_payload(payload):
    if not isinstance(payload, dict):
        return None
    native_id = payload.get("orderId", payload.get("id", payload.get("txid")))
    if isinstance(native_id, (list, tuple)):
        native_id = next((item for item in native_id if str(item).strip()), None)
    return None if native_id is None else str(native_id)


class Instrument:
    """Provider-bound symbol with namespaced consumer parameters.

    ``params`` is a flat mapping such as ``{"mt.gain": "9.2"}``; use ``param``
    for optional casting and defaults.
    """

    def __init__(self, name: str, symbol: str, provider: str,
                 base: Optional[str] = None, quote: Optional[str] = None,
                 enabled: bool = True, isolation: str = "own_ledger",
                 market_hours: str = "24x7",
                 params: Optional[dict] = None, api=None):
        self.name = name
        self.symbol = symbol
        self.provider_name = provider
        self.base = base
        self.quote = quote
        self.enabled = enabled
        self.isolation = isolation          # 'dedicated' | 'own_ledger' (vezi designul)
        self.market_hours = market_hours    # '24x7' | 'rth' | ...
        self.params = dict(params or {})
        self._api = api or _default_api
        self._provider = self._api.provider_by_name(provider)
        if self._provider is None:
            raise ValueError(
                f"Instrument {name!r} ({symbol}): provider necunoscut {provider!r}. "
                f"Inregistrat in market_api?")

    # ── identitate / acces provider ────────────────────────────────────────────
    @property
    def provider(self):
        return self._provider

    @property
    def provider_label(self) -> str:
        return self._provider.name

    # ── market-data (delegat la provider, pe symbolul instrumentului) ──────────
    def price(self) -> Optional[float]:
        return self._provider.get_current_price(self.symbol)

    def history(self, lookback_h: float) -> Optional[List]:
        return self._provider.get_price_history(self.symbol, lookback_h)

    # -- Account: free asset balance; orders and trades by symbol. -------------
    def free(self) -> Optional[float]:
        return self._provider.free_balance(self.base or self.symbol)

    def orders(self, side: Optional[str], since_s: float) -> List[dict]:
        return self._provider.get_orders(self.symbol, side, since_s)

    def trades(self, since_s: float) -> List[dict]:
        return self._provider.get_trades(self.symbol, since_s)

    def open_orders(self) -> List[dict]:
        return self._provider.open_orders(self.symbol)

    # -- Order placement: dry or real after provider gates. --------------------
    def place(self, side: str, price: float, qty: float, **kwargs):
        # Complete provider-agnostic guard: profit, daily cap/anti-spam, rapid-fire
        # cooldown, instantaneous trend deferral, and fleet-wide logging. Apply only to providers
        # without internal guards. Binance retains its richer implementation using
        # real API-permission data, avoiding duplicated or conflicting cooldowns.
        # Kraken and Hyperliquid receive the equivalent protections through shared
        # order_guard, trade_cooldown, cacheManager, and outcome-log components.
        #
        # bypass_profit_guard skips both profit and weight guards for emergency
        # flows. bypass_profit_reference skips only the historical price-reference
        # check while retaining quantity/weight policy and every other guard.
        # bypass_quantity_policy skips only the dynamic quantity/weight policy;
        # balance, fee, profit, daily-cap, cooldown, and trend guards stay active.
        # A caller such as rtrade or the outbox worker may own its lifecycle and
        # retry; in that case this pipeline does not touch the global queue.
        caller_owns_retry = bool(kwargs.pop("caller_owns_retry", False))
        # Internal mutable result channel used by the outbox worker.  It keeps the
        # public return type backward compatible while exposing the exact refusal
        # reason (notably trend deferral) without parsing logs or blocking.
        outcome_context = kwargs.pop("_outcome_context", None)
        if outcome_context is not None and not isinstance(outcome_context, dict):
            raise TypeError("_outcome_context must be a dict")
        bypass = bool(kwargs.pop("bypass_profit_guard", False))
        bypass_profit_reference = bool(kwargs.pop("bypass_profit_reference", False))
        bypass_quantity_policy = bool(kwargs.pop("bypass_quantity_policy", False))
        wait_for_trend = bool(kwargs.pop("wait_for_trend", True))
        cooldown_pair_id = kwargs.pop("cooldown_pair_id", None)
        side_u = side.upper()
        # This narrow escape hatch exists only for drawdown BUY flows.  A SELL
        # must always prove profitability against its historical BUY reference;
        # silently ignore the flag there so a misplaced caller argument cannot
        # weaken the exit guard.
        if bypass_profit_reference and side_u != "BUY":
            print(
                f"[GARD] {side_u} {self.symbol}: bypass_profit_reference ignorat; "
                "is allowed only for BUY")
            bypass_profit_reference = False
        # Quantity-policy bypass reduces exposure and is therefore SELL-only.
        # Ignore it on BUY so a misplaced argument can never increase exposure.
        if bypass_quantity_policy and side_u != "SELL":
            print(
                f"[GARD] {side_u} {self.symbol}: bypass_quantity_policy ignorat; "
                "is allowed only for SELL")
            bypass_quantity_policy = False
        if self._provider.guards_internally():
            return self._provider.place_order(self.symbol, side, price, qty, **kwargs)

        # Capture the original uncapped quantity, requested price, and reproducible
        # arguments before mutation in case the placement must be queued for retry.
        orig_qty = qty
        orig_price = price   # Requested price preserves caller intent for the retry guard.
        reason = None
        order = None
        prequeued_record_id = None
        prequeued_client_order_id = None
        prequeued_intent_id = None
        # safeback_seconds is explicitly overridden by monitortrades (normally 12
        # days) and tradeall (14 days), so Binance rarely uses the 48-hour config
        # default. Apply the same window to enabled Kraken instruments. Retain it
        # in kwargs for provider.place_order even where currently ignored.
        safeback_override = kwargs.get("safeback_seconds")
        # ``smart`` distinguishes two legacy interfaces: place_order_smart cancels
        # opposing orders and nudges price before guards, while place_safe_order
        # does neither. Default true for the main path; former safe-order callers
        # explicitly pass false.
        smart = bool(kwargs.pop("smart", True))
        # Reconstruct this call exactly for retry after bypass/smart were popped;
        # all other placement metadata remains in kwargs.
        retry_kwargs = dict(kwargs)
        retry_kwargs["bypass_profit_guard"] = bypass
        retry_kwargs["bypass_profit_reference"] = bypass_profit_reference
        retry_kwargs["bypass_quantity_policy"] = bypass_quantity_policy
        retry_kwargs["wait_for_trend"] = wait_for_trend
        retry_kwargs["smart"] = smart
        if cooldown_pair_id:
            retry_kwargs["cooldown_pair_id"] = cooldown_pair_id
        # A market order ignores the requested price, so revalidate immediately
        # before submission at the current executable price. This prevents a
        # force-market sell from passing at +1% then executing below the validated
        # margin, a financial time-of-check/time-of-use issue.
        is_market = bool(kwargs.get("force", False))
        profit_margin = None
        profit_window_ref = None
        try:
            # 0. For smart placement only, apply venue price mechanics and remove
            # opposing orders before the profit guard so it sees the submitted price.
            # Safe placement leaves price unchanged here; final submission still
            # clamps and rounds it through place_order_mechanics.
            if smart:
                price = self._provider.adjust_order_price(self.symbol, side_u, price,
                                                          cancel_opposite=True)

            # 1. Provider-agnostic daily cap and anti-spam, never bypassed.
            ok, reason = order_guard.daily_limit_guard(self._provider, self.symbol, side_u,
                                                       safeback_sec=safeback_override)
            if not ok:
                return None

            if not bypass:
                if bypass_profit_reference:
                    print(
                        f"[GARD] {side_u} {self.symbol}: referinta istorica de pret "
                        "ocolita explicit; quantity/weight guard ramane activ")
                else:
                    profit_margin = order_guard.margin_for(self._provider.name)
                    # Tier-one min/max reference comes from the provider hook. Kraken
                    # and Hyperliquid use their configured venue window; Binance uses
                    # safeback_sec from the order cache. An empty window falls back to
                    # the last opposite fill.
                    profit_window_ref = self._provider.profit_guard_window_ref(
                        self.symbol, side_u, safeback_override)
                    ok = order_guard.profit_guard(
                        self._provider, self.symbol, side_u, price, profit_margin,
                        window_ref=profit_window_ref)
                    if not ok:
                        reason = "profit_guard"
                        return None
                # Cap quantity in both directions through the provider hook so the
                # full balance is not traded at once. SELL uses base balance; BUY
                # uses quote balance divided by price. Provider hooks retain
                # cancelorders/hours metadata without rereading or reserving balance.
                decision = self._provider.quantity_decision(
                    self.symbol, side_u, price, qty,
                    base=self.base, quote=self.quote,
                    cancelorders=bool(kwargs.get("cancelorders", False)),
                    hours=float(kwargs.get("hours", 5) or 5),
                    apply_policy=not bypass_quantity_policy,
                )
                qty = decision.final_qty
                if qty <= 0:
                    print(f"[{self.symbol}] {side_u} qty refuzat: "
                          f"{decision.refuse_reason} asset={decision.balance_asset}")
                    reason = decision.refuse_reason or "qty_zero_after_weight"
                    return None

            # 2. Optional provider-agnostic trend gate is instantaneous.  Placement
            # must never sleep or poll: a negative decision returns immediately and
            # the durable outbox retries it on a later worker tick.  Callers that own
            # their lifecycle (AssetGuardian) apply the same check in their strategy
            # loop and disable this duplicate gate.
            if wait_for_trend:
                try:
                    import cacheManager as cm
                    if cm.get_short_trend_manager().should_wait(side_u, self.symbol):
                        print(
                            f"[{self.symbol}] {side_u} amanat de trend; "
                            "placement does not wait, the intent stays queued for retry")
                        reason = "trend_deferred"
                        return None
                except Exception as e:  # noqa: BLE001 — Opportunistic gate.
                    print(f"[{self.symbol}] {side_u} trend-gate indisponibil: {e}")

            # Market orders must use the current quote rather than the caller's
            # decorative limit. Explicit bypass remains available for protective
            # exits that must accept a loss.
            if not bypass and not bypass_profit_reference and is_market:
                guard_price = price
                if is_market:
                    guard_price = self._provider.get_current_price(self.symbol)
                    if guard_price is None or float(guard_price) <= 0:
                        print(f"[{self.symbol}] {side_u} MARKET BLOCAT: pret curent indisponibil")
                        reason = "market_price_unavailable"
                        return None
                    guard_price = float(guard_price)
                ok = order_guard.profit_guard(
                    self._provider, self.symbol, side_u, guard_price, profit_margin,
                    window_ref=profit_window_ref)
                if not ok:
                    reason = "profit_guard"
                    return None

            # 3. Provider-agnostic rapid-fire cooldown shared with Binance. Keys are
            # symbols, preventing collisions between different venues.
            with trade_cooldown.trade_slot(
                    side_u, self.symbol, pair_id=cooldown_pair_id) as slot:
                if not slot.allowed:
                    age = time.time() - slot.info.get("timestamp", 0)
                    print(f"[{self.symbol}] {side_u} BLOCAT de cooldown: ultim ordin "
                          f"({slot.info.get('side')}) acum {age:.0f}s")
                    reason = "cooldown"
                    return None
                # Persist the exact intent before the external side effect.  This
                # closes the process-crash/response-loss gap for every normal caller
                # of Instrument.place. Lifecycle-owned callers already persist in
                # their own campaign state and opt out with caller_owns_retry=True.
                if not caller_owns_retry:
                    try:
                        import order_retry
                        if order_retry.RETRY_ENABLED:
                            prequeue_kwargs = dict(retry_kwargs)
                            prequeue_kwargs["smart"] = smart
                            prequeued_record_id = order_retry.enqueue(
                                self.symbol, side_u, qty, prequeue_kwargs,
                                requested_price=orig_price, ref_price=price,
                                failure_reason="submit_pending",
                                provider_name=self.provider_name,
                                intent_id=kwargs.get("intent_id"),
                                kind=kwargs.get("kind") or kwargs.get("motivation"))
                            prequeued = order_retry.get(prequeued_record_id)
                            if prequeued is None:
                                raise RuntimeError("the intent cannot be read back after persisting")
                            prequeued_client_order_id = dict(
                                prequeued.get("place_kwargs") or {}).get(
                                    "client_order_id")
                            prequeued_intent_id = prequeued.get("intent_id")
                            if not prequeued_client_order_id:
                                raise RuntimeError("intentia persistata nu are client_order_id")
                            kwargs["client_order_id"] = prequeued_client_order_id
                    except Exception as exc:
                        print(
                            f"[{self.symbol}] {side_u} BLOCAT: intentia nu poate fi "
                            f"persistata inainte de submit ({exc})")
                        reason = "pre_submit_persist_failed"
                        return None

                reason = "submit_ambiguous"
                if prequeued_intent_id:
                    _EXECUTION_AUDIT.record(
                        "submit_requested", intent_id=prequeued_intent_id,
                        venue=self.provider_name, symbol=self.symbol,
                        side=side_u.lower(), qty=qty, price=price,
                        market=is_market,
                        kind=kwargs.get("kind") or kwargs.get("motivation"),
                        client_order_id=prequeued_client_order_id,
                    )
                response = self._provider.place_order(
                    self.symbol, side, price, qty, **kwargs)
                if _accepted_order_payload(response):
                    order = response
                    reason = None
                    slot.commit(_order_id_from_payload(order))
                else:
                    reason = "response_without_order_id"
                    order = None
                return order
        except Exception as e:  # noqa: BLE001 — Fail closed when guards cannot be verified.
            print(f"[{self.symbol}] {side_u} BLOCAT (fail-closed): {e}")
            reason = reason or "guard_check_failed"
            return None
        finally:
            if outcome_context is not None:
                outcome_context["accepted"] = order is not None
                outcome_context["reason"] = None if order is not None else reason
            try:
                caller = os.path.basename(sys._getframe(1).f_code.co_filename)
            except Exception:
                caller = None
            _outcomes_log.log_order_outcome(
                self.symbol, side_u, price, qty, "accepted" if order else "refused",
                None if order else reason, kwargs.get("motivation"), caller=caller)
            # Acceptance is not a fill. Preserve the venue ID in the exact outbox
            # record so the single worker follows open/partial/terminal state. If
            # this persistence step fails, the pre-submit record and deterministic
            # client ID remain available for response-loss recovery.
            if order is not None and not caller_owns_retry:
                try:
                    import order_retry
                    if prequeued_record_id is not None:
                        tracked = order_retry.mark_accepted(
                            prequeued_record_id,
                            order,
                            client_order_id=prequeued_client_order_id)
                        if not tracked:
                            print(
                                f"[{self.symbol}] {side_u} acceptat, dar tracking-ul "
                                "nu a putut fi actualizat; intentia ramane pending")
                        elif prequeued_intent_id:
                            _EXECUTION_AUDIT.record(
                                "submit_accepted", intent_id=prequeued_intent_id,
                                venue=self.provider_name, symbol=self.symbol,
                                side=side_u.lower(), qty=qty, price=price,
                                market=is_market,
                                kind=kwargs.get("kind") or kwargs.get("motivation"),
                                client_order_id=prequeued_client_order_id,
                                order_id=_order_id_from_payload(order),
                            )
                except Exception as _e:  # noqa: BLE001
                    print(f"[{self.symbol}] {side_u} tracking accepted esuat: {_e}")
            # Persist a failed intent unless this is already a retry. Queue handling
            # is best effort and never changes the placement return value.
            elif order is None and not caller_owns_retry:
                try:
                    import order_retry
                    if prequeued_intent_id:
                        _EXECUTION_AUDIT.record(
                            "submit_ambiguous", intent_id=prequeued_intent_id,
                            venue=self.provider_name, symbol=self.symbol,
                            side=side_u.lower(), qty=qty, price=price,
                            market=is_market,
                            kind=kwargs.get("kind") or kwargs.get("motivation"),
                            client_order_id=prequeued_client_order_id,
                            failure_reason=reason,
                        )
                    if order_retry.RETRY_ENABLED:
                        if prequeued_record_id is not None:
                            order_retry.mark_failure(
                                prequeued_record_id, reason,
                                client_order_id=prequeued_client_order_id)
                        else:
                            # Capture best-effort market price only when enqueueing,
                            # for retry price guarding and deduplication.
                            queue_qty = orig_qty
                            try:
                                if queue_qty is None or float(queue_qty) <= 0:
                                    queue_qty = qty
                            except (TypeError, ValueError, OverflowError):
                                queue_qty = qty
                            ref_price = None
                            try:
                                ref_price = self._provider.get_current_price(self.symbol)
                            except Exception:  # noqa: BLE001
                                ref_price = None
                            order_retry.enqueue(
                                self.symbol, side_u, queue_qty, retry_kwargs,
                                requested_price=orig_price, ref_price=ref_price,
                                failure_reason=reason,
                                provider_name=self.provider_name,
                                intent_id=kwargs.get("intent_id"),
                                kind=kwargs.get("kind") or kwargs.get("motivation"))
                except Exception as _e:  # noqa: BLE001
                    print(f"[{self.symbol}] {side_u} enqueue retry esuat (ignor): {_e}")

    def min_qty(self) -> float:
        """Return the venue minimum order quantity; ``0`` means no known minimum."""
        try:
            return float(self._provider.min_order_qty(self.symbol) or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

    # ── params namespaced (mt.* / tradeall.* / rtrade.*) ───────────────────────
    def param(self, consumer: str, key: str, default: Any = None,
              cast: Optional[Callable] = None) -> Any:
        """Return ``consumer.key`` or ``default`` when missing or conversion fails."""
        v = self.params.get(f"{consumer}.{key}")
        if v is None:
            return default
        if cast is None:
            return v
        try:
            return cast(v)
        except (ValueError, TypeError):
            return default

    def __repr__(self) -> str:
        st = "on" if self.enabled else "off"
        return f"<Instrument {self.name} {self.symbol}@{self.provider_name} {st}>"
