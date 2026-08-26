#!/usr/bin/env python3
"""Venue-independent spot DCA engine with take-profit and trailing exits.

The engine receives all data and executes every order through ``StrategyExecutor``.
Venue launchers inject the provider, state directory, and presentation details while
live and replay share one implementation of the financial rules.
"""

from __future__ import annotations

import math
import os
import statistics
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from alertnotifiers import bind_notify
from botcore import are_close, float_env, log
from providers.execution_audit import intent_client_order_id
from providers.strategy_executor import ProviderError, StrategyExecutor
from providers.tracked_order import (
    StrategyExecutorLifecycleApi,
    TrackedOrderLifecycle,
)

from . import spot_dca_rules as sr
from .state_store import JsonStateStore

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LEGACY_STATE_DIR = os.path.join(_ROOT, "kraken")
_DEFAULT_FEE_NOTE = "fee Kraken ~0.26% taker / ~0.16% maker per leg"


notify = bind_notify(("SYMBOL_LABEL", "KRAKEN_PAIR"), "CRYPTO")


def state_path_for(pair: str, state_dir: str | None = None) -> str:
    """Return the state path, retaining the Kraken fallback for live compatibility."""
    safe = "".join(c for c in pair if c.isalnum() or c in "._-")
    directory = state_dir or _LEGACY_STATE_DIR
    return os.path.join(directory, f".state_{safe}.json")


@dataclass
class StratParams:
    currency: str          # Quote currency used only for presentation.
    entry_amount: float    # Entry size in quote currency.
    entry_discount_pct: float
    dca_amount: float
    dca_drop_pct: float
    check_minutes: float
    takeprofit_pct: float
    max_budget: float
    max_dca_buys: int
    enable_takeprofit: bool
    order_ttl_min: float
    stop_loss_pct: float     # Sell everything at this loss percentage; zero disables it.
    adopt_cost: float        # Positive value adopts an existing position at this average cost.
    adopt_qty: float         # Adopted quantity; zero reads the base-asset balance automatically.
    reentry_drop_pct: float  # After TP, reenter only this far below the sale price; zero is immediate.
    reentry_tolerance_pct: float  # Treat prices within tolerance of the threshold as reached.
    reentry_adaptive: bool   # Use volatility-based reentry, falling back to fixed during warm-up.
    reentry_sl_bounce_pct: float  # After stop-loss, reenter on a bounce from the post-sale low.
    tp_tranches: list        # Gradual ``(percentage, share)`` sales; empty means one full TP.
    # --- TP trend-aware (EXPERIMENTAL, default OFF) -------------------------------
    tp_trend_hold: bool = False       # Hold during a short uptrend and exit near market on reversal.
    tp_trend_min_pct: float = 0.5     # Legacy v1 shadow-price trend threshold.
    tp_trail_pct: float = 2.0         # Above TP, exit after this pullback from the peak.
    tp_trail_profit_floor_pct: float = 0.0  # 0 preserves live compatibility; >0 allows MARKET trailing only
                                      # when the order reference is >= average*(1+floor%);
                                      # the MARKET hard stop retains priority.
    # --- TREND OVERLAY (combine strategies by regime; EXPERIMENTAL, default OFF) ---
    trend_overlay: bool = False       # Use top-up and trailing in confirmed uptrends; classic in ranges.
    trend_sma_n: int = 30             # SMA bar count for the long-trend signal.
    trend_interval: int = 240         # minutes per trend-signal bar (live: Kraken OHLC;
                                      # backtest: injected bars). 240=4h -> SMA(30)=~5 days.
    trend_confirm_bars: int = 3       # consecutive uptrend bars required to avoid false confirmation
    trend_topup: float = 2000.0       # amount bought on trend entry; larger values capture more trend
    trend_trail_pct: float = 5.0      # Trend-position exit pullback from peak.
    trend_exit_break: bool = False    # False: trailing only; True: trailing or price below SMA.
    # --- VOLATILITY ADAPTIVE (overlay redesign: modulation, not amplification; default OFF) ---
    tp_trail_adaptive: bool = False   # A: TP trailing becomes k×vol_1h: wide in volatile trends
                                      # to ride longer, tight in chop. Warm-up safely falls back
                                      # to fixed tp_trail_pct, like adaptive reentry.
    tp_trail_k: float = 2.0           # vol_1h multiplier for adaptive trailing
    tp_trail_min: float = 1.5         # lower clamp (%) avoids exits on negligible noise
    tp_trail_max: float = 8.0         # upper clamp (%) prevents giving all profit back
    tp_trail_vol_interval: int = 240  # Minutes per volatility bar; fixed in live and replay.
    dca_trend_brake: bool = False     # Skip DCA in confirmed downtrends to reduce risk.
    dca_brake_min_pct: float = 1.5    # Minimum recent/old slope percentage for downtrend.
    dca_spacing_growth_pct: float = 0.0  # Increase the threshold after every filled DCA;
                                         # Zero preserves byte-identical live behavior.
    # --- #2: VOLATILITY-SCALED DCA SIZING (default OFF) ---
    dca_vol_scale_k: float = 0.0      # 0=OFF. eff_dca = dca × (vol_ref/vol_1h)^k, clamp [0.3,3].
                                      # k>0 means smaller DCA in high volatility (defensive);
                                      # k<0 means larger DCA (aggressive harvesting). Safe warm-up fallback.
    dca_vol_ref: float = 2.0          # reference vol_1h percentage for scaling
    dca_vol_interval: int = 240       # Identical OHLC cadence in live and replay.
    # --- PERCENTAGE SIZING (optional; total_budget=0 or alloc_pct=0 keeps fixed values) ---
    total_budget: float = 0.0   # venue's TOTAL budget, e.g. the whole Kraken account
    alloc_pct: float = 0.0      # percentage allocated to THIS coin; venue allocations total 100
    entry_pct: float = 0.0      # Entry is this percentage of the asset allocation.
    dca_pct: float = 0.0        # DCA is this percentage of the asset allocation.

    def __post_init__(self):
        def finite(name: str, value) -> float:
            try:
                parsed = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} must be finite") from exc
            if not math.isfinite(parsed):
                raise ValueError(f"{name} must be finite")
            return parsed

        self.trend_topup = finite("STRAT_TREND_TOPUP", self.trend_topup)
        self.entry_discount_pct = finite("STRAT_ENTRY_DISCOUNT_PCT", self.entry_discount_pct)
        self.dca_drop_pct = finite("STRAT_DCA_DROP_PCT", self.dca_drop_pct)
        self.takeprofit_pct = finite("STRAT_TAKEPROFIT_PCT", self.takeprofit_pct)
        self.stop_loss_pct = finite("STRAT_STOP_LOSS_PCT", self.stop_loss_pct)

        if self.trend_topup <= 0:
            raise ValueError("STRAT_TREND_TOPUP must be > 0")
        if not 0 <= self.entry_discount_pct < 100:
            raise ValueError("STRAT_ENTRY_DISCOUNT_PCT must be in [0, 100)")
        if self.dca_drop_pct <= 0:
            raise ValueError("STRAT_DCA_DROP_PCT must be > 0")
        if self.takeprofit_pct < 0 or (self.enable_takeprofit and self.takeprofit_pct == 0):
            raise ValueError("STRAT_TAKEPROFIT_PCT must be > 0 when take-profit is enabled")
        if self.stop_loss_pct < 0:
            raise ValueError("STRAT_STOP_LOSS_PCT must be >= 0")

        percentage_names = (
            "STRAT_TOTAL_BUDGET", "STRAT_ALLOC_PCT",
            "STRAT_ENTRY_PCT", "STRAT_DCA_PCT",
        )
        values = tuple(
            finite(name, value)
            for name, value in zip(percentage_names, (
                self.total_budget, self.alloc_pct, self.entry_pct, self.dca_pct,
            ))
        )
        self.total_budget, self.alloc_pct, self.entry_pct, self.dca_pct = values
        if not any(value != 0 for value in values):
            return
        if self.total_budget <= 0:
            raise ValueError("STRAT_TOTAL_BUDGET must be > 0 when percentage sizing is configured")
        for name, value in zip(percentage_names[1:], values[1:]):
            if not 0 < value <= 100:
                raise ValueError(f"{name} must be in (0, 100] when percentage sizing is configured")

    def pct_sizing_on(self) -> bool:
        return self.total_budget > 0

    def allocated_budget(self) -> float:
        return self.total_budget * self.alloc_pct / 100.0 if self.pct_sizing_on() else self.max_budget

    def effective_entry_amount(self) -> float:
        return self.allocated_budget() * self.entry_pct / 100.0 if self.pct_sizing_on() else self.entry_amount

    def effective_dca_amount_base(self) -> float:
        return self.allocated_budget() * self.dca_pct / 100.0 if self.pct_sizing_on() else self.dca_amount

    def effective_max_budget(self) -> float:
        return self.allocated_budget()

    @classmethod
    def from_env(cls) -> "StratParams":
        mode = os.environ.get("STRATEGY_MODE", "avg_tp").strip().lower()
        reentry_sl_bounce = float_env("STRAT_REENTRY_SL_BOUNCE_PCT")
        return cls(
            currency           = os.environ.get("STRAT_CURRENCY", "EUR").strip().upper(),
            entry_amount       = float_env("STRAT_ENTRY") or 50.0,
            entry_discount_pct = float_env("STRAT_ENTRY_DISCOUNT_PCT") or 0.2,
            dca_amount         = float_env("STRAT_DCA") or 30.0,
            dca_drop_pct       = float_env("STRAT_DCA_DROP_PCT") or 2.0,
            check_minutes      = float_env("STRAT_CHECK_MINUTES") or 2.0,
            takeprofit_pct     = float_env("STRAT_TAKEPROFIT_PCT") or 1.0,
            max_budget         = float_env("STRAT_MAX_BUDGET") or 500.0,
            # Do not use ``or 10``: explicit zero validly disables DCA while managing exits.
            max_dca_buys       = int(float_env("STRAT_MAX_DCA_BUYS")) if float_env("STRAT_MAX_DCA_BUYS") is not None else 10,
            total_budget       = float_env("STRAT_TOTAL_BUDGET") or 0.0,
            alloc_pct          = float_env("STRAT_ALLOC_PCT") or 0.0,
            entry_pct          = float_env("STRAT_ENTRY_PCT") or 0.0,
            dca_pct            = float_env("STRAT_DCA_PCT") or 0.0,
            enable_takeprofit  = (mode != "dca_only"),
            order_ttl_min      = float_env("STRAT_ORDER_TTL_MIN") or 10.0,
            stop_loss_pct      = float_env("STRAT_STOP_LOSS_PCT") or 0.0,
            adopt_cost         = float_env("STRAT_ADOPT_COST") or 0.0,
            adopt_qty          = float_env("STRAT_ADOPT_QTY") or 0.0,
            reentry_drop_pct   = float_env("STRAT_REENTRY_DROP_PCT") or 0.0,
            reentry_tolerance_pct = float_env("STRAT_REENTRY_TOLERANCE_PCT") or 0.0,
            reentry_adaptive   = os.environ.get("STRAT_REENTRY_ADAPTIVE", "false").strip().lower() == "true",
            # Explicit zero disables bounce reentry after STOP and retains the classic rule.
            reentry_sl_bounce_pct = 1.5 if reentry_sl_bounce is None else reentry_sl_bounce,
            tp_tranches        = _parse_tranches(os.environ.get("STRAT_TP_TRANCHES", "")),
            tp_trend_hold      = os.environ.get("STRAT_TP_TREND_HOLD", "false").strip().lower() == "true",
            tp_trend_min_pct   = float_env("STRAT_TP_TREND_MIN_PCT") or 0.5,
            tp_trail_pct       = float_env("STRAT_TP_TRAIL_PCT") or 2.0,
            tp_trail_profit_floor_pct = max(
                0.0, float_env("STRAT_TP_TRAIL_PROFIT_FLOOR_PCT") or 0.0,
            ),
            trend_overlay      = os.environ.get("STRAT_TREND_OVERLAY", "false").strip().lower() == "true",
            trend_sma_n        = int(float_env("STRAT_TREND_SMA_N") or 30),
            trend_interval     = int(float_env("STRAT_TREND_INTERVAL") or 240),
            trend_confirm_bars = int(float_env("STRAT_TREND_CONFIRM_BARS") or 3),
            trend_topup        = float_env("STRAT_TREND_TOPUP") or 2000.0,
            trend_trail_pct    = float_env("STRAT_TREND_TRAIL_PCT") or 5.0,
            trend_exit_break   = os.environ.get("STRAT_TREND_EXIT_BREAK", "false").strip().lower() == "true",
            tp_trail_adaptive  = os.environ.get("STRAT_TP_TRAIL_ADAPTIVE", "false").strip().lower() == "true",
            tp_trail_k         = float_env("STRAT_TP_TRAIL_K") or 2.0,
            tp_trail_min       = float_env("STRAT_TP_TRAIL_MIN") or 1.5,
            tp_trail_max       = float_env("STRAT_TP_TRAIL_MAX") or 8.0,
            tp_trail_vol_interval = int(float_env("STRAT_TP_TRAIL_VOL_INTERVAL") or 240),
            dca_trend_brake    = os.environ.get("STRAT_DCA_TREND_BRAKE", "false").strip().lower() == "true",
            dca_brake_min_pct  = float_env("STRAT_DCA_BRAKE_MIN_PCT") or 1.5,
            dca_spacing_growth_pct = max(
                0.0, float_env("STRAT_DCA_SPACING_GROWTH_PCT") or 0.0,
            ),
            dca_vol_scale_k    = float_env("STRAT_DCA_VOL_SCALE_K") or 0.0,
            dca_vol_ref        = float_env("STRAT_DCA_VOL_REF") or 2.0,
            dca_vol_interval   = int(float_env("STRAT_DCA_VOL_INTERVAL") or 240),
        )


def _parse_tranches(spec: str) -> list:
    """Parse ``3:50,6:50`` into percentage/share pairs whose shares total 100."""
    out = []
    for part in spec.split(","):
        if ":" in part:
            try:
                pct, share = part.split(":")
                out.append((float(pct), float(share)))
            except ValueError:
                return []
    return out if out and abs(sum(s for _, s in out) - 100) < 1e-6 else []


def _new_state() -> dict:
    return {
        "cycle": 1,
        "qty": 0.0,
        "cost": 0.0,            # Cost basis in quote currency.
        "spent": 0.0,           # Deployed in the current cycle, subject to its cap.
        "dca_buys": 0,
        "entry_price": None,
        "last_buy_price": None,
        "cycle_fees": 0.0,      # Actual fees accumulated in the current cycle.
        "realized_gross": 0.0,
        "realized_net": 0.0,
        "fees_total": 0.0,
        "last_sell_price": None,  # Latest sale price for reentry rules.
        "last_exit_kind": None,   # TP/STOP/etc. for stop-aware reentry.
        "sl_low": None,           # Post-stop low used for bounce reentry.
        "trail_peak": None,       # Peak tracked after price exceeds TP.
        "trail_stop": None,       # Trailing floor ratchets upward even as volatility changes.
        "trend_mode": False,      # overlay: currently in a hold+trailing trend position
        "trend_peak": None,       # peak tracked in trend mode
        "trend_confirm_count": 0, # consecutive uptrend bars confirming the signal
        "orders": [],           # {txid, side, vol, price, amount, kind, ts}
        "pending_intent": None, # durable pre-submit boundary before venue acceptance
    }


class Strategy:
    def __init__(self, client: "StrategyExecutor", pair: str, params: StratParams,
                 dry_run: bool = True, desktop: bool = False,
                 initial_state: dict | None = None,
                 replay_mode: bool = False, state_dir: str | None = None,
                 notifier: Callable[..., None] | None = None,
                 notification_source: str = "kraken", venue_label: str = "Kraken",
                 fee_note: str = _DEFAULT_FEE_NOTE):
        self.client = client
        self.pair = pair
        self.p = params
        self.ccy = params.currency
        self.dry_run = dry_run
        # ``dry_run`` includes paper-live, not only backtests. Replay must be identified
        # separately so the trend signal uses injected bars.
        self.replay_mode = replay_mode
        self.desktop = desktop
        self._notifier = notifier
        self.notification_source = notification_source
        self.venue_label = venue_label
        self.fee_note = fee_note
        # Keep the legacy one-argument call so existing replays can replace
        # state_path_for and guarantee zero I/O.
        self.state_file = (
            state_path_for(pair) if state_dir is None else state_path_for(pair, state_dir)
        )
        self._state_write_failed = False
        self.s = initial_state if initial_state is not None else self._load()
        self.s.setdefault("pending_intent", None)
        self._paper_seq = 0
        # Observational adaptive-volatility shadow history stays in memory for sigma.
        # It is rebuilt after restart and never enters persistent state.
        self._shadow_prices = deque(maxlen=90)
        # Pair precision normalized by the provider.
        self.price_dec, self.vol_dec, self.ordermin = 5, 8, 0.0
        try:
            precision = client.pair_precision(pair)
            if precision:
                self.price_dec = precision.price_decimals
                self.vol_dec = precision.volume_decimals
                self.ordermin = precision.order_min
        except ProviderError:
            log("  ! nu pot citi precizia perechii — folosesc valori implicite")
        self._order_lifecycle = TrackedOrderLifecycle(
            StrategyExecutorLifecycleApi(client),
            provider_name=self.venue_label,
            venue=self.venue_label,
            missing_confirmations=2,
            retry_on_lookup_error=False,
        )

    def _emit(self, **event) -> None:
        """Send an event through the venue sink or legacy compatibility wrapper."""
        # Replay shares live strategy logic but must not create external effects.
        # ``dry_run`` alone is insufficient because paper-live still needs real alerts.
        if self.replay_mode:
            return
        (self._notifier or notify)(**event)

    # -- Persistence -----------------------------------------------------------
    def _store(self) -> JsonStateStore:
        return JsonStateStore(
            self.state_file, _new_state, label=self.venue_label,
            logger=log, fail_closed=not self.dry_run,
        )

    def _load(self) -> dict:
        return self._store().load()

    def _save(self) -> None:
        self._state_write_failed = True
        if self._store().save(self.s):
            self._state_write_failed = False

    def _persist_pending_intent(self, pending: dict | None) -> None:
        self.s["pending_intent"] = None if pending is None else dict(pending)
        self._save()
        if self._state_write_failed:
            raise ProviderError("starea intentiei nu a putut fi persistata")

    def _intent_identity(self, side: str, kind: str, vol: float, price: float) -> str:
        """Stable semantic ID for retries of one rounded strategy instruction."""
        return (
            f"spot-dca:{self.venue_label}:{self.pair}:cycle-{self.s.get('cycle', 1)}:"
            f"{kind}:{side}:{vol:.{self.vol_dec}f}:{price:.{self.price_dec}f}"
        )

    @staticmethod
    def _order_from_pending(pending: dict) -> dict:
        return {
            "txid": str(pending["order_id"]),
            "side": str(pending["side"]).lower(),
            "vol": float(pending["requested_qty"]),
            "price": float(pending.get("requested_price") or 0.0),
            "amount": float(pending.get("amount") or 0.0),
            "kind": pending.get("kind"),
            "market": bool(pending.get("market")),
            "intent_id": pending.get("intent_id"),
            "ts": float(pending.get("created_at") or time.time()),
        }

    def _adopt_pending_order(self, pending: dict) -> None:
        order_id = str(pending.get("order_id") or "")
        if not order_id:
            return
        if not any(str(order.get("txid")) == order_id for order in self.s["orders"]):
            self.s["orders"].append(self._order_from_pending(pending))
        self.s["pending_intent"] = None
        self._save()

    def _reconcile_pending_submit(self) -> None:
        pending = self.s.get("pending_intent")
        if not pending or self.dry_run:
            return
        try:
            result = self._order_lifecycle.reconcile(
                pending, persist=self._persist_pending_intent)
        except (ProviderError, RuntimeError, ValueError) as exc:
            log(f"  ! [STRAT] intent pending nereconciliat ({exc}) — nu retrimit")
            return
        if result.order_known:
            self._adopt_pending_order(result.intent)
            log(
                f"  [STRAT] intent recuperat {result.intent['side']} "
                f"txid={result.intent['order_id']}"
            )
        elif result.outcome in {"absent", "retryable"}:
            log("  [STRAT] intent absent confirmat — strategia il poate reevalua")

    # -- Helpers ---------------------------------------------------------------
    def _avg(self) -> float | None:
        return self.s["cost"] / self.s["qty"] if self.s["qty"] > 1e-12 else None

    def _qty_for(self, amount: float, price: float) -> float:
        return round(amount / price, self.vol_dec) if price > 0 else 0.0

    def _dust_safe_qty(self, qty: float) -> float:
        """Leave one volume tick to tolerate a venue's rounded balance.

        Selling the full internally tracked quantity can exceed a rounded ledger and
        cause permanent insufficient-funds retries. Preserve historical high-precision
        behavior without magnifying dust on low-precision venues.
        """
        dust_decimals = self.vol_dec if self.vol_dec <= 2 else self.vol_dec - 1
        step = 10.0 ** -max(dust_decimals, 1)
        # Epsilon prevents floating-point division from leaving two ticks after floor.
        ticks = math.floor((float(qty) + step * 1e-9) / step) - 1
        return round(max(ticks, 0) * step, self.vol_dec)

    def _has_open(self, side: str) -> bool:
        return any(o["side"] == side for o in self.s["orders"])

    def _find_open(self, side: str) -> dict | None:
        return next((o for o in self.s["orders"] if o["side"] == side), None)

    def _remove(self, o: dict) -> None:
        if o in self.s["orders"]:
            self.s["orders"].remove(o)

    # -- Placement -------------------------------------------------------------
    def _place(self, side: str, vol: float, price: float, kind: str, amount: float = 0.0,
               market: bool = False) -> bool:
        # A market trailing/stop exit executes immediately instead of risking a missed
        # sharp drop with a limit order. Backtests fill it at the next bar's open.
        vol = round(vol, self.vol_dec)
        price = round(price, self.price_dec)
        if vol <= 0 or (self.ordermin and vol < self.ordermin):
            log(f"  ! [STRAT] volum {vol} < ordin minim {self.ordermin} — sar")
            return False
        if self.dry_run:
            self._paper_seq += 1
            log(f"  [STRAT] [PAPER] {side.upper()} {kind}{' MKT' if market else ''} {vol} @ {price} {self.ccy}")
            self.s["orders"].append({"txid": f"PAPER-{self._paper_seq}", "side": side,
                                     "vol": vol, "price": price, "amount": amount,
                                     "kind": kind, "market": market, "ts": time.time()})
            return True
        if self.s.get("pending_intent"):
            log("  ! [STRAT] exista o intentie pre-submit nereconciliata — nu dublez")
            return False
        try:
            # Venues that might accept an underfunded BUY and cancel its remainder can
            # reject the intent during preflight before audit/submission.
            preflight = getattr(type(self.client), "preflight_order", None)
            if callable(preflight):
                preflight(
                    self.client, self.pair, side, vol, None if market else price,
                    market=market, kind=kind,
                )
            intent_id = self._intent_identity(side, kind, vol, price)
            client_order_id = intent_client_order_id(self.venue_label, intent_id)
            if not client_order_id:
                raise ProviderError(
                    f"{self.venue_label}: client_order_id indisponibil pentru lifecycle")
            intent = self._order_lifecycle.new_intent(
                intent_id=intent_id,
                client_order_id=client_order_id,
                symbol=self.pair,
                side=side,
                requested_qty=vol,
                requested_price=price,
                kind=kind,
                metadata={"amount": amount, "market": market},
            )
            submit_with_intent = getattr(type(self.client), "submit_order_with_intent", None)
            def submit():
                if callable(submit_with_intent):
                    return submit_with_intent(
                        self.client, intent_id, self.pair, side, vol,
                        None if market else price,
                        market=market, kind=kind,
                        reference_price=price if market else None,
                        client_order_id=client_order_id,
                    )
                return self.client.submit_order(
                    self.pair, side, vol, None if market else price,
                    market=market, kind=kind,
                    client_order_id=client_order_id,
                )

            tracked = self._order_lifecycle.submit(
                intent, persist=self._persist_pending_intent, submit=submit)
            if not tracked.order_known:
                log(
                    f"  ! [STRAT] {side} {kind} submit ambiguu; "
                    "intentia ramane persistata pentru reconciliere"
                )
                return False
            txid = str(tracked.intent["order_id"])
            log(f"  [STRAT] {side.upper()} {kind} plasat txid={txid} {vol} @ {price}")
            self._adopt_pending_order(tracked.intent)
            return True
        except (ProviderError, RuntimeError, ValueError) as e:
            log(f"  ! [STRAT] {side} {kind} esuat: {e}")
            return False

    def _cancel_order(self, o: dict) -> bool:
        """Request cancellation without forgetting the order before terminal confirmation.

        An accepted request does not prove that no concurrent fill occurred. Retain the
        order until the provider reports terminal state so reconciliation applies that fill.
        """
        if self.dry_run or str(o["txid"]).startswith("PAPER"):
            self._remove(o)
            log(f"  [STRAT] anulat {o['side']} {o['txid']}")
            return True
        if o.get("cancel_requested"):
            return True
        try:
            cancel_with_intent = getattr(type(self.client), "cancel_order_with_intent", None)
            if callable(cancel_with_intent):
                cancel_with_intent(
                    self.client, o.get("intent_id") or f"legacy-{self.pair}-{o['txid']}",
                    self.pair, o["txid"],
                )
            else:
                self.client.cancel_order(self.pair, o["txid"])
        except ProviderError as e:
            log(f"  ! [STRAT] cancel esuat pentru {o['txid']}: {e} — ordinul ramane urmarit")
            return False
        o["cancel_requested"] = True
        o["cancel_ts"] = time.time()
        # Persist cancellation intent across restart and track through terminal status.
        self._save()
        log(f"  [STRAT] cancel solicitat {o['side']} {o['txid']} — astept status terminal")
        return True

    def _cancel_open(self, side: str) -> bool:
        o = self._find_open(side)
        if not o:
            return True
        return self._cancel_order(o)

    def _cancel_orders(self, side: str | None = None, *, exclude_market: bool = False) -> bool:
        """Request cancellation of selected orders; return False if any request fails."""
        selected = [
            o for o in list(self.s["orders"])
            if (side is None or o["side"] == side)
            and not (exclude_market and o.get("market"))
        ]
        ok = True
        for o in selected:
            ok = self._cancel_order(o) and ok
        return ok

    def _has_pending_market_exit(self) -> bool:
        return any(o["side"] == "sell" and o.get("market") for o in self.s["orders"])

    # -- Reconciliation --------------------------------------------------------
    def reconcile(self, price: float) -> None:
        self._reconcile_pending_submit()
        for side in ("buy", "sell"):
            for o in [x for x in self.s["orders"] if x["side"] == side]:
                if o not in self.s["orders"]:
                    continue
                if self.dry_run:
                    if o.get("market"):
                        # In paper-live, a MARKET order fills at the currently observed
                        # price, including a drop below the stop/trailing reference.
                        fill_price = price
                    elif ((side == "buy" and price <= o["price"])
                          or (side == "sell" and price >= o["price"])):
                        fill_price = o["price"]
                    else:
                        continue
                    if o in self.s["orders"]:
                        self._remove(o)
                        self._apply_fill(o, o["vol"], fill_price, fee=0.0)
                    continue
                # Providers report cumulative quantity, cost, and fee even while an order
                # remains open. Apply only the delta since the saved reconciliation marker.
                try:
                    status_with_intent = getattr(type(self.client), "order_status_with_intent", None)
                    if callable(status_with_intent):
                        status = status_with_intent(
                            self.client, o.get("intent_id") or f"legacy-{self.pair}-{o['txid']}",
                            self.pair, o["txid"],
                        )
                    else:
                        status = self.client.order_status(self.pair, o["txid"])
                except ProviderError as e:
                    log(f"  ! [STRAT] status {o['txid']} esuat: {e} — pastrez ordinul")
                    continue
                st = status.status
                terminal = st in ("closed", "canceled", "expired")
                try:
                    total_vol = float(status.filled_qty or 0.0)
                    total_cost = float(status.cost or total_vol * o["price"])
                    total_fee = float(status.fee or 0.0)
                    reported_price = total_cost / total_vol if total_vol > 0 else float(o["price"])
                except (TypeError, ValueError):
                    log(f"  ! [STRAT] status {o['txid']} are executie invalida — pastrez ordinul")
                    continue

                applied_vol = float(o.get("applied_vol") or 0.0)
                applied_cost = float(o.get("applied_cost") or 0.0)
                applied_fee = float(o.get("applied_fee") or 0.0)
                eps = max(1e-12, float(o["vol"]) * 1e-12)
                if total_vol + eps < applied_vol or total_cost + eps < applied_cost:
                    log(f"  ! [STRAT] status {o['txid']} a regresat cumulativ "
                        f"(vol {total_vol}<{applied_vol}) — nu reaplic")
                    continue

                delta_vol = max(0.0, total_vol - applied_vol)
                delta_cost = max(0.0, total_cost - applied_cost)
                # Fees are signed because some venues report negative maker rebates.
                delta_fee = total_fee - applied_fee
                # Persist markers before accounting because a final SELL may replace state.
                o["applied_vol"] = total_vol
                o["applied_cost"] = total_cost
                o["applied_fee"] = total_fee

                if delta_vol > eps:
                    fill_order = dict(o)
                    if o["side"] == "buy":
                        # ``amount`` is the order's nominal cap; partial fills consume it proportionally.
                        fill_order["amount"] = (
                            float(o.get("amount") or 0.0) * delta_vol / float(o["vol"])
                            if float(o["vol"]) > 0 else delta_cost
                        )
                        if applied_vol > eps and fill_order.get("kind") in {"DCA", "TREND_ENTRY"}:
                            fill_order["kind"] = f"{fill_order['kind']}_PARTIAL"
                    fill_price = delta_cost / delta_vol if delta_cost > 0 else reported_price
                    if terminal:
                        self._remove(o)
                    self._apply_fill(
                        fill_order, delta_vol, fill_price, fee=delta_fee, final=False,
                    )
                elif abs(delta_fee) > eps:
                    # Charge a late final fee without simulating a zero-volume fill.
                    self.s["cycle_fees"] += delta_fee
                    self.s["fees_total"] += delta_fee
                    self.s["realized_net"] -= delta_fee

                if terminal:
                    if o in self.s["orders"]:
                        self._remove(o)
                    if o["side"] == "sell":
                        self._finalize_cycle_if_flat(o, reported_price)
                    log(f"  [STRAT] {o['txid']} {st} (executat {total_vol}/{o['vol']})")
                else:
                    age = (time.time() - o.get("ts", 0)) / 60
                    if (side == "buy" and not o.get("cancel_requested")
                            and age > self.p.order_ttl_min and price > o["price"] * 1.003):
                        log(f"  [STRAT] buy {o['txid']} neexecutat, pret a urcat — anulez & reasez")
                        self._cancel_order(o)

    def _apply_fill(self, o: dict, vol: float, price: float, fee: float,
                    *, final: bool = True) -> None:
        tag = "[PAPER] " if self.dry_run else ""
        self.s["cycle_fees"] += fee
        self.s["fees_total"] += fee
        # Charge every fee once even while the position remains open. This prevents net
        # mark-to-market P&L from overstating positions with a filled BUY.
        self.s["realized_net"] -= fee
        if o["side"] == "buy":
            self.s["qty"] += vol
            self.s["cost"] += vol * price
            self.s["last_buy_price"] = price
            if self.s["entry_price"] is None:
                self.s["entry_price"] = price
            self.s["spent"] += o.get("amount", vol * price)
            if o.get("kind") == "DCA":
                self.s["dca_buys"] += 1
            if o.get("kind") == "TREND_ENTRY":
                # A submitted order is not a trend position. Enable the mode only after
                # the venue or replay confirms the fill.
                self.s["trend_mode"] = True
                self.s["trend_peak"] = price
            avg = self._avg()
            log(f"  [STRAT] {tag}BUY FILLED {vol} @ {price} {self.ccy} ({o.get('kind')})  "
                f"qty={self.s['qty']:.8f} avg={avg:.{self.price_dec}f} fee={fee}")
            self._emit(title=f"{tag}{self.pair} BUY {vol:.2f}@{price:.2f}",
                       body=(f"{o.get('kind')} | q{self.s['qty']:.2f} a{avg:.2f} | "
                             f"desf{self.s['spent']:.0f}{self.ccy}"),
                       source=self.notification_source, price=price, desktop=self.desktop)
            self._cancel_orders("sell")
        else:  # sell
            avg = self._avg() or price
            gross = (price - avg) * vol
            net = gross - fee
            self.s["realized_gross"] += gross
            self.s["realized_net"] += gross
            self.s["qty"] -= vol
            # A partial SELL releases cost basis proportionally. Otherwise the full cost
            # remains assigned to the reduced quantity and distorts the average.
            self.s["cost"] = max(0.0, self.s["cost"] - avg * vol)
            log(f"  [STRAT] {tag}SELL FILLED {vol} @ {price} {self.ccy}  "
                f"brut={gross:+.4f} fee_ciclu={self.s['cycle_fees']:.4f} net={net:+.4f}")
            self._emit(title=f"{tag}{self.pair} SELL {vol:.2f}@{price:.2f} N{net:+.2f}{self.ccy}",
                       body=(f"a{avg:.2f} · br{gross:+.2f} fee{self.s['cycle_fees']:.2f} N{net:+.2f} | "
                             f"Ntot{self.s['realized_net']:+.2f}{self.ccy}"),
                       source=self.notification_source, price=price, desktop=self.desktop)
            if final:
                self._finalize_cycle_if_flat(o, price)

    def _finalize_cycle_if_flat(self, o: dict, price: float) -> None:
        """Close a cycle once, only after terminal exit status."""
        # Dust left by _dust_safe_qty must not keep a cycle open. Apply the threshold
        # only at terminal state because an open partial fill may validly retain dust.
        dust = 2 * 10.0 ** -(max(self.vol_dec - 1, 1))
        if abs(self.s["qty"]) < dust:
            self.s["qty"] = 0.0
            self.s["cost"] = 0.0
        if self.s["qty"] > 1e-12:
            return
        keep = (self.s["realized_gross"], self.s["realized_net"],
                self.s["fees_total"], self.s.get("cycle", 1) + 1)
        self.s = _new_state()
        (self.s["realized_gross"], self.s["realized_net"],
         self.s["fees_total"], self.s["cycle"]) = keep
        self.s["last_sell_price"] = price   # reentry rule must not buy back higher
        self.s["last_exit_kind"] = o.get("kind")   # TP/STOP enables stop-aware reentry
        self.s["sl_low"] = price            # Initial low for post-stop bounce reentry.
        log(f"  [STRAT] === ciclu inchis, reincep (ciclu {self.s['cycle']}) ===")

    # -- Decision logic --------------------------------------------------------
    def _check_stop_loss(self, price: float) -> bool:
        """Close everything when unrealized loss crosses the anti-runaway threshold."""
        if self.p.stop_loss_pct <= 0:
            return False
        avg = self._avg()
        if not avg:
            return False
        loss_pct = (avg - price) / avg * 100   # A long position loses below average cost.
        if sr.hit_stop(avg, price, self.p.stop_loss_pct):
            # Reconcile an already submitted MARKET exit instead of canceling or duplicating it.
            if self._has_pending_market_exit():
                return True
            log(f"  🛑 [STRAT] STOP-LOSS: pierdere {loss_pct:.2f}% >= {self.p.stop_loss_pct}% — VAND TOT (taie pierderea)")
            # Retain orders after failed cancellation because a ghost DCA/TP can fill
            # after exit. Submit exit only when all cancellations are accepted.
            if not self._cancel_orders():
                log("  ! [STRAT] STOP amanat: cel putin un ordin nu a putut fi anulat")
                return True
            placed = self._place("sell", self._dust_safe_qty(self.s["qty"]),
                                 round(price * 0.995, self.price_dec), kind="STOP", market=True)
            if not placed:
                log("  ! [STRAT] STOP declansat, dar ordinul MARKET nu a fost acceptat — reincerc")
                return True
            self._emit(title=f"🛑 SL {self.pair} -{loss_pct:.1f}%",
                       body=f"pierdere {loss_pct:.1f}% ≥prag{self.p.stop_loss_pct}% — vand tot",
                       source=self.notification_source, price=price, desktop=self.desktop)
            return True
        return False

    def _trail_profit_floor_price(self, avg: float) -> float | None:
        """Return the minimum soft-exit price, rounded up to venue precision.

        The threshold is intentionally gross and simple. Configuration must include
        enough fee buffer; central and stress benchmarks then measure net profit using
        scenario fees and fills. ``0`` exactly preserves the existing MARKET trailing.
        """
        pct = float(self.p.tp_trail_profit_floor_pct or 0.0)
        if pct <= 0 or avg <= 0:
            return None
        raw = sr.tp_price(avg, pct)
        scale = 10 ** self.price_dec
        return math.ceil(raw * scale - 1e-12) / scale

    def _maybe_adopt(self) -> None:
        """Adopt an existing account position instead of buying an entry.

        Run once on fresh state only so an active cycle cannot be corrupted.
        """
        if self.p.adopt_cost <= 0 or self.s.get("adopted"):
            return
        if (self.s["qty"] > 1e-12 or self.s["orders"]
                or self.s["cycle"] != 1 or self.s["spent"] > 0):
            log("  [STRAT] adopt: starea nu e proaspata — NU adopt (ciclu in curs)")
            return
        qty = self.p.adopt_qty
        if qty <= 0:  # Read quantity from the pair's base-asset balance.
            try:
                precision = self.client.pair_precision(self.pair)
                base = precision.base_asset if precision else ""
                qty = float(self.client.free_balance(base) or 0.0)
            except ProviderError as e:
                log(f"  ! [STRAT] adopt: nu pot citi balanta ({e})")
                return
        if qty <= 1e-12:
            log("  [STRAT] adopt: balanta 0 pe activul de baza — astept alocarea")
            return
        if self.p.adopt_qty <= 0:
            qty = self._dust_safe_qty(qty)
            if qty <= 0:
                return
        self.s["qty"] = qty
        self.s["cost"] = qty * self.p.adopt_cost
        self.s["entry_price"] = self.p.adopt_cost
        self.s["last_buy_price"] = self.p.adopt_cost
        self.s["adopted"] = True
        self._save()
        log(f"  📥 [STRAT] POZITIE ADOPTATA: {qty} @ {self.p.adopt_cost} {self.ccy} — gestionez TP/DCA/SL")
        self._emit(title=f"📥 {self.pair} ADOPTAT {qty:.2f}@{self.p.adopt_cost}",
                   body=f"TP+{self.p.takeprofit_pct}% DCA-{self.p.dca_drop_pct}% SL{self.p.stop_loss_pct}%",
                   source=f"{self.notification_source}-bot", price=self.p.adopt_cost,
                   desktop=self.desktop)

    # -- Percentage sizing; all-zero settings retain fixed sizing --------------
    def _pct_sizing_on(self) -> bool:
        return self.p.pct_sizing_on()

    def _alloc_budget(self) -> float:
        """Return this asset's allocation from the total budget."""
        return self.p.allocated_budget()

    def _effective_entry_amount(self) -> float:
        return self.p.effective_entry_amount()

    def _effective_max_budget(self) -> float:
        return self.p.effective_max_budget()

    def _base_dca_amount(self) -> float:
        return self.p.effective_dca_amount_base()

    # -- Adaptive-volatility shadow observation; does not decide ----------------
    def _effective_dca_amount(self) -> float:
        """Scale base DCA size by volatility when enabled, falling back during warm-up."""
        base = self._base_dca_amount()
        scale_k = float(self.p.dca_vol_scale_k)
        vol_ref = float(self.p.dca_vol_ref)
        if not scale_k:
            return base
        if not math.isfinite(scale_k) or not math.isfinite(vol_ref) or vol_ref <= 0:
            return base
        try:
            vol = self._dca_vol_1h()
            if not vol or not math.isfinite(vol) or vol <= 0:
                return base
            scale = (vol_ref / vol) ** scale_k
        except (ArithmeticError, TypeError, ValueError):
            return base
        if not math.isfinite(scale):
            return base
        return base * max(0.3, min(3.0, scale))

    def _dca_vol_1h(self) -> float | None:
        """Return DCA volatility from OHLC at the same cadence in live and replay."""
        if self.replay_mode:
            closes = [price for _, price in self._shadow_prices]
        else:
            closes = self.client.ohlc_closes(self.pair, self.p.dca_vol_interval)
        return self._hourly_vol_from_closes(closes, self.p.dca_vol_interval)

    def _shadow_vol_1h(self) -> float | None:
        """Return one-hour volatility from local tick history, or None during warm-up."""
        pts = list(self._shadow_prices)
        if len(pts) < 20:
            return None
        rets = [math.log(pts[i][1] / pts[i - 1][1]) for i in range(1, len(pts))
                if pts[i - 1][1] > 0 and pts[i][1] > 0]
        dts = [pts[i][0] - pts[i - 1][0] for i in range(1, len(pts))]
        if len(rets) < 19 or not dts:
            return None
        mean_dt = sum(dts) / len(dts)
        if mean_dt <= 0:
            return None
        try:
            std = statistics.stdev(rets)
        except statistics.StatisticsError:
            return None
        return std * math.sqrt(3600.0 / mean_dt) * 100.0

    def _trend_up(self, min_pts: int = 20) -> bool:
        """Detect a short uptrend from local price history.

        The recent-half mean must exceed the old-half mean by ``tp_trend_min_pct``.
        This deterministic rule is identical in live and replay and returns False during warm-up.
        """
        pts = [p for _, p in self._shadow_prices]
        if len(pts) < min_pts:
            return False
        half = len(pts) // 2
        old = sum(pts[:half]) / half
        new = sum(pts[half:]) / (len(pts) - half)
        if old <= 0:
            return False
        return (new - old) / old * 100.0 >= self.p.tp_trend_min_pct

    def _shadow_reentry_line(self, price: float, lsp: float, prag_fix: float) -> None:
        try:
            k_re = float_env("SHADOW_K_REENTRY") or 2.0
            vol = self._shadow_vol_1h()
            if vol is None:
                log(f"  [SHADOW] prag adaptiv: warm-up ({len(self._shadow_prices)}/20 puncte)")
                return
            adapt_pct = k_re * vol
            prag_adapt = lsp * (1 - adapt_pct / 100)
            verdict = "AR FI INTRAT" if price <= prag_adapt else "nu ar fi intrat nici el"
            log(f"  [SHADOW] prag fix {prag_fix:.2f} vs adaptiv {prag_adapt:.2f} "
                f"(vol_1h {vol:.2f}% x k={k_re}) → {verdict}")
        except Exception as e:  # noqa: BLE001 — observational
            log(f"  [SHADOW] eroare calcul ({e}) — ignor")

    def _effective_reentry_drop_pct(self) -> tuple[float, str]:
        """Return the effective adaptive or fixed reentry threshold.

        Use ``K_REENTRY * one-hour volatility`` when enabled and available. Otherwise
        fail safely to the fixed percentage so missing signals never stop trading.
        """
        if not self.p.reentry_adaptive:
            return self.p.reentry_drop_pct, "fix"
        try:
            k_re = float_env("SHADOW_K_REENTRY") or 2.0
            vol = self._shadow_vol_1h()
        except Exception as e:  # noqa: BLE001 — observational failure must not stop trading.
            log(f"  [REINTRARE-ADAPTIV] eroare calcul ({e}) — fallback pe fix")
            return self.p.reentry_drop_pct, "fix (fallback, eroare)"
        if vol is None:
            return self.p.reentry_drop_pct, f"fix (fallback, warm-up {len(self._shadow_prices)}/20)"
        return k_re * vol, f"adaptiv (vol_1h {vol:.2f}% x k={k_re})"

    def _effective_trail_pct(self) -> float:
        """Return the clamped volatility-adaptive or fixed trailing pullback.

        Warm-up and errors fall back to the fixed value. Volatile trends receive more
        room while choppy markets receive tighter trailing without buying higher.
        """
        if not self.p.tp_trail_adaptive:
            return self.p.tp_trail_pct
        try:
            vol = self._trail_vol_1h()
        except Exception as e:  # noqa: BLE001 — observational failure must not stop trading.
            log(f"  [STRAT] trailing adaptiv: OHLC indisponibil ({e}) — fallback pe fix")
            vol = None
        if vol is None:
            return self.p.tp_trail_pct
        return max(self.p.tp_trail_min, min(self.p.tp_trail_max, self.p.tp_trail_k * vol))

    def _trail_vol_1h(self) -> float | None:
        """Return one-hour-normalized volatility at one OHLC cadence in live and replay.

        Different live-tick and replay-bar cadences produced different signals even after
        square-root-of-time scaling. Live mode reads closed
        provider bars; replay is separately validated against the same interval.
        """
        if self.replay_mode:
            closes = [price for _, price in self._shadow_prices]
        else:
            closes = self.client.ohlc_closes(self.pair, self.p.tp_trail_vol_interval)
        return self._hourly_vol_from_closes(
            closes, self.p.tp_trail_vol_interval,
        )

    @staticmethod
    def _hourly_vol_from_closes(
        closes: list[float], interval_minutes: float,
    ) -> float | None:
        """Return log-return deviation normalized to one hour for a fixed cadence."""
        closes = closes[-90:]
        if len(closes) < 20 or interval_minutes <= 0:
            return None
        returns = [
            math.log(current / previous)
            for previous, current in zip(closes, closes[1:])
            if previous > 0 and current > 0
        ]
        if len(returns) < 19:
            return None
        try:
            std = statistics.stdev(returns)
        except statistics.StatisticsError:
            return None
        return std * math.sqrt(60.0 / interval_minutes) * 100.0

    def _trend_down(self, min_pts: int = 20) -> bool:
        """Detect the fixed-OHLC downtrend on the same time scale in live and replay."""
        pts = self._trend_closes()[-90:]
        if len(pts) < min_pts:
            return False
        half = len(pts) // 2
        old = sum(pts[:half]) / half
        new = sum(pts[half:]) / (len(pts) - half)
        if old <= 0:
            return False
        return (new - old) / old * 100.0 <= -self.p.dca_brake_min_pct

    # -- TREND OVERLAY ---------------------------------------------------------
    def _trend_closes(self) -> list:
        """Return long-trend closes at the same cadence in live and replay.

        Replay uses injected bar closes; live and paper-live use provider OHLC at
        ``trend_interval``. Thus SMA(N) has the same meaning in both modes.
        """
        if self.replay_mode:
            return [p for _, p in self._shadow_prices]
        try:
            return self.client.ohlc_closes(self.pair, self.p.trend_interval)
        except Exception as e:  # noqa: BLE001 — no signal simply means no trend entry.
            log(f"  [STRAT] trend OHLC fetch esuat ({e}) — trend nedeterminat")
            return []

    def _trend_up_series(self, closes: list) -> bool:
        """Confirm uptrend when recent closes exceed a rising SMA for the required bars."""
        n = self.p.trend_sma_n
        k = max(1, self.p.trend_confirm_bars)
        if len(closes) < n + k:
            return False
        for j in range(k):
            i = len(closes) - 1 - j
            sma = sum(closes[i - n + 1:i + 1]) / n
            sma_prev = sum(closes[i - n:i]) / n
            if not (closes[i] > sma and sma > sma_prev):
                return False
        return True

    def _overlay_step(self, price: float) -> bool:
        """Apply the regime overlay and return whether it handled the tick.

        Confirmed uptrend uses top-up, hold, and trailing or SMA-break exit. A range
        returns False so classic DCA/TP logic runs.
        """
        closes = self._trend_closes()
        up = self._trend_up_series(closes)
        pending_trend_entry = next(
            (o for o in self.s["orders"]
             if o["side"] == "buy" and o.get("kind") == "TREND_ENTRY"),
            None,
        )
        if self.s.get("trend_mode"):
            peak = max(self.s.get("trend_peak") or price, price)
            self.s["trend_peak"] = peak
            trail_stop = peak * (1 - self.p.trend_trail_pct / 100)
            n = self.p.trend_sma_n
            sma = sum(closes[-n:]) / n if len(closes) >= n else None
            broke = self.p.trend_exit_break and sma is not None and price < sma
            if (price <= trail_stop or broke) and self.s["qty"] > 1e-12:
                exit_px = round(price * 0.999, self.price_dec)
                if not self._has_pending_market_exit():
                    if not self._cancel_orders("sell", exclude_market=True):
                        log("  ! [STRAT] TREND EXIT amanat: un SELL nu a putut fi anulat")
                        return True
                    if self._place("sell", self._dust_safe_qty(self.s["qty"]), exit_px,
                                   kind="TP", market=True):
                        log(f"  [STRAT] TREND EXIT ({'break' if broke else 'trailing'} "
                            f"{self.p.trend_trail_pct}%) varf {peak:.{self.price_dec}f} -> IES la {exit_px}")
            else:
                self._cancel_orders("sell", exclude_market=True)  # Ride the trend; do not sell.
            return True
        if pending_trend_entry:
            if up:
                return True                         # Wait for the top-up fill.
            self._cancel_open("buy")                # Signal disappeared before fill.
            log("  [STRAT] TREND ENTER anulat: semnalul a dispărut înainte de fill")
            return False                            # revine la strategia range
        if up and self.s["spent"] + self.p.trend_topup <= self._effective_max_budget():
            self._cancel_orders("buy")              # Cancel pending range orders.
            self._cancel_orders("sell")
            if self.s["orders"]:                    # Live mode waits for terminal confirmations.
                return True
            self._place("buy", self._qty_for(self.p.trend_topup, price), price,
                        kind="TREND_ENTRY", amount=self.p.trend_topup)
            log(f"  [STRAT] TREND ENTER pending: top-up {self.p.trend_topup} {self.ccy} @ {price} "
                f"(SMA{self.p.trend_sma_n} up, confirmat)")
            return True
        return False

    def step(self, price: float, timestamp: float | None = None) -> None:
        if self.s.get("pending_intent"):
            log("  [STRAT] intentie pre-submit in reconciliere — aman deciziile noi")
            return
        held = self.s["qty"]
        entry_px = sr.entry_price(price, self.p.entry_discount_pct)   # = price*(1 - disc%)
        # Live uses wall time while replay injects bar time. Without this distinction, a
        # backtest processing hundreds of bars per second produces meaningless hourly
        # volatility and changes the adaptive threshold.
        tick_time = time.time() if timestamp is None else float(timestamp)
        self._shadow_prices.append((tick_time, price))

        # While adoption is pending, do not buy a new entry; the allocation is in transit.
        if self.p.adopt_cost > 0 and not self.s.get("adopted") and held <= 1e-12:
            self._maybe_adopt()
            if not self.s.get("adopted"):
                return
            held = self.s["qty"]

        # STOP-LOSS is a safety invariant and takes priority over all regime logic,
        # including overlay hold and trailing behavior.
        if held > 1e-12 and self._check_stop_loss(price):
            return
        # Reconcile a MARKET exit from an earlier tick before any new TP/DCA decision,
        # even if price has recovered meanwhile.
        if self._has_pending_market_exit():
            return

        # Combine range DCA/TP with trend hold/trailing behavior.
        if self.p.trend_overlay and self._overlay_step(price):
            return

        if held <= 1e-12:
            if self._has_open("buy"):
                return
            # Stop-aware reentry rule.
            lsp = self.s.get("last_sell_price")
            if self.s.get("last_exit_kind") == "STOP" and self.p.reentry_sl_bounce_pct > 0 and lsp:
                # After stop-loss, reenter on a bounce from the post-sale low instead of
                # waiting for a deeper drop that could leave the bot outside a recovery.
                low = min(self.s.get("sl_low") or price, price)
                self.s["sl_low"] = low
                prag_bounce = low * (1 + self.p.reentry_sl_bounce_pct / 100)
                if sr.reentry_stop_blocked(price, low, self.p.reentry_sl_bounce_pct, self.p.reentry_tolerance_pct):
                    log(f"  [STRAT] reintrare dupa STOP blocata: pret {price} < prag revenire "
                        f"{prag_bounce:.{self.price_dec}f} (min {low}, +{self.p.reentry_sl_bounce_pct}%)")
                    return
                log(f"  [STRAT] reintrare dupa STOP: revenire atinsa (pret {price} >= "
                    f"{prag_bounce:.{self.price_dec}f}, min {low}) — reintru")
            else:
                # After TP, do not repurchase above the sale price; wait for a real drop.
                drop_pct, drop_source = self._effective_reentry_drop_pct()
                if drop_pct > 0 and lsp:
                    prag = lsp * (1 - drop_pct / 100)
                    # Deterministic tolerance treats a near-threshold price as reached.
                    if sr.reentry_drop_blocked(price, lsp, drop_pct, self.p.reentry_tolerance_pct):
                        log(f"  [STRAT] reintrare blocata: pret {price} > prag {prag:.2f} [{drop_source}]"
                            f"{f' (tol {self.p.reentry_tolerance_pct}%)' if self.p.reentry_tolerance_pct else ''} "
                            f"(vandut la {lsp}, astept -{drop_pct:.2f}%)")
                        if not self.p.reentry_adaptive:
                            self._shadow_reentry_line(price, lsp, prag)   # Compare only while fixed logic decides.
                        return
            entry_amt = self._effective_entry_amount()
            budget = self._effective_max_budget()
            if self.s["spent"] + entry_amt > budget:
                log(f"  [STRAT] plafon {budget:.0f} {self.ccy} atins — nu intru")
                return
            self._place("buy", self._qty_for(entry_amt, entry_px),
                        entry_px, kind="ENTRY", amount=entry_amt)
            return

        avg = self._avg()
        trail_armed = self.s.get("trail_peak") is not None
        if (self.p.enable_takeprofit and avg and self.p.tp_trend_hold
                and (trail_armed or price >= sr.tp_price(avg, self.p.takeprofit_pct))):
            # Arm trailing at the first TP crossing and keep it armed through exit, even
            # if price later falls below TP, so the target pullback does not reset the peak.
            peak = max(self.s.get("trail_peak") or price, price)
            self.s["trail_peak"] = peak
            eff_trail = self._effective_trail_pct()   # Adaptive when enabled, otherwise fixed.
            candidate_stop = peak * (1 - eff_trail / 100)
            # A trailing stop ratchets one way: volatility may widen distance for future
            # peaks but cannot surrender profit already protected by the current stop.
            previous_stop = self.s.get("trail_stop")
            trail_stop = max(candidate_stop, previous_stop or candidate_stop)
            self.s["trail_stop"] = trail_stop
            # Use the same conservative reference as the MARKET order. Apply the floor
            # to that reference rather than the raw observed price so the 0.1% buffer
            # cannot turn an exit exactly at the floor into an implicit loss.
            exit_px = round(price * 0.999, self.price_dec)
            profit_floor = self._trail_profit_floor_price(avg)
            if price <= trail_stop and profit_floor is not None and exit_px < profit_floor:
                log(f"  [STRAT] trailing soft blocat: referința {exit_px:.{self.price_dec}f} sub pragul "
                    f"profitabil {profit_floor:.{self.price_dec}f}; hard stop rămâne MARKET")
                # Re-evaluate on the next tick. Leave no persistent order and do not open
                # a contradictory DCA on the tick that triggered trailing.
                return
            if price <= trail_stop:
                if not self._cancel_orders("sell", exclude_market=True):
                    log("  ! [STRAT] trailing exit amanat: un SELL nu a putut fi anulat")
                    return
                if self._place("sell", self._dust_safe_qty(self.s["qty"]), exit_px,
                               kind="TP", market=True):
                    log(f"  [STRAT] trailing: pullback {eff_trail:.2f}% de la varf "
                        f"{peak:.{self.price_dec}f} -> IES la {exit_px} (calarit trendul)")
                # Do not open a contradictory DCA on the exit tick.
                return
            else:
                self._cancel_orders("sell", exclude_market=True)
                log(f"  [STRAT] peste TP, CALARESC (varf {peak:.{self.price_dec}f}, "
                    f"trail-stop {trail_stop:.{self.price_dec}f})")
        elif self.p.enable_takeprofit and avg and self.p.tp_trend_hold:
            # Below TP with ride enabled, do not place a fixed TP. Wait to cross TP and
            # arm trailing; stop-loss remains the safety exit.
            self.s["trail_peak"] = None
            self.s["trail_stop"] = None
            self._cancel_orders("sell", exclude_market=True)
        elif self.p.enable_takeprofit and avg:
            self.s["trail_peak"] = None   # below TP/classic mode: reset peak
            self.s["trail_stop"] = None
            # Optional tranche TP (STRAT_TP_TRANCHES="3:50,6:50") sells gradually.
            # No configured tranches means the classic one-order full TP default.
            tranches = self.p.tp_tranches or [(self.p.takeprofit_pct, 100.0)]
            desired, rem = [], held
            for i, (pct, share) in enumerate(tranches):
                # The final tranche sells the remainder, risking "Insufficient funds".
                # Venue balance may be one decimal smaller than internally tracked holdings;
                # leave the same dust buffer used during adoption.
                q = self._dust_safe_qty(rem) if i == len(tranches) - 1 \
                    else min(rem, round(held * share / 100, self.vol_dec))
                rem = round(rem - q, self.vol_dec)
                if q > 0:
                    desired.append((round(sr.tp_price(avg, pct), self.price_dec), q))
            if self.ordermin and any(q < self.ordermin for _, q in desired):
                desired = [(round(sr.tp_price(avg, tranches[0][0]), self.price_dec), held)]
            sells = [o for o in self.s["orders"] if o["side"] == "sell"]
            ok = (len(sells) == len(desired) and
                  all(not o.get("cancel_requested") for o in sells) and
                  all(abs(o["price"] - p) / p <= 0.001 and abs(o["vol"] - q) <= 1e-9
                      for o, (p, q) in zip(sorted(sells, key=lambda x: x["price"]),
                                           sorted(desired))))
            if not ok:
                self._cancel_orders("sell")
                if not any(o["side"] == "sell" for o in self.s["orders"]):
                    for p_, q_ in desired:
                        self._place("sell", q_, p_, kind="TP")

        effective_dca_drop = sr.progressive_dca_drop_pct(
            self.p.dca_drop_pct,
            self.p.dca_spacing_growth_pct,
            self.s["dca_buys"],
        )
        effective_dca_amount = self._effective_dca_amount()
        if (self.s["dca_buys"] < self.p.max_dca_buys
                and self.s["last_buy_price"]
                # prag DCA + "aproape de prag" = atins (regula partajata cu backtest)
                and sr.dca_price_hit(
                    price, self.s["last_buy_price"], effective_dca_drop,
                    self.p.reentry_tolerance_pct,
                )
                and self.s["spent"] + effective_dca_amount <= self._effective_max_budget()
                and not (self.p.dca_trend_brake and self._trend_down())  # B: DCA brake in a downtrend
                and not self._has_open("buy")):
            log(
                f"  [STRAT] dip {price} <= {self.s['last_buy_price']}"
                f"×(1-{effective_dca_drop}%) "
                f"(tol {self.p.reentry_tolerance_pct}%) — "
                f"DCA {effective_dca_amount:.0f}"
            )
            self._place(
                "buy", self._qty_for(effective_dca_amount, entry_px), entry_px,
                kind="DCA", amount=effective_dca_amount,
            )

    # -- main loop -------------------------------------------------------------
    def run(self) -> None:
        mode = "avg_tp" if self.p.enable_takeprofit else "dca_only"
        log(f"  === STRATEGIE {self.venue_label.upper()} PORNITA ===")
        log(f"      pereche    : {self.pair}   {'[PAPER]' if self.dry_run else '⚠ REAL — BANI'}")
        log(f"      mod        : {mode}")
        log(f"      intrare    : {self._effective_entry_amount():.0f} {self.ccy} @ market-{self.p.entry_discount_pct}%")
        log(f"      DCA        : {self._base_dca_amount():.0f} {self.ccy} la -{self.p.dca_drop_pct}% (max {self.p.max_dca_buys})")
        if self._pct_sizing_on():
            log(f"      SIZING %   : felie {self.p.alloc_pct}% × total {self.p.total_budget:.0f} "
                f"= {self._alloc_budget():.0f} {self.ccy} (entry {self.p.entry_pct}%, DCA {self.p.dca_pct}%)")
        if self.p.dca_spacing_growth_pct > 0:
            log(
                "      DCA growth : +"
                f"{self.p.dca_spacing_growth_pct}pp după fiecare DCA executat"
            )
        if self.p.dca_vol_scale_k:
            log(
                f"      DCA vol    : k={self.p.dca_vol_scale_k}, "
                f"referință={self.p.dca_vol_ref}%, "
                f"OHLC={self.p.dca_vol_interval}m"
            )
        log(f"      take-profit: +{self.p.takeprofit_pct}%" if self.p.enable_takeprofit else "      take-profit: off")
        log(f"      PLAFON     : {self._effective_max_budget():.0f} {self.ccy} / ciclu")
        if self.fee_note:
            log(f"      ! {self.fee_note}; TP={self.p.takeprofit_pct}%")
        self._maybe_adopt()
        try:
            while True:
                price = self.client.get_current_price(self.pair)
                if price is None:
                    log("  [STRAT] pret indisponibil — reincerc")
                    time.sleep(self.p.check_minutes * 60)
                    continue
                try:
                    # After a disk error, make no new decisions until current memory state
                    # can be persisted again.
                    if self._state_write_failed:
                        self._save()
                    self.reconcile(price)
                    self.step(price)
                    self._save()
                except Exception as e:  # noqa: BLE001 — REZILIENTA: net/API picat -> reincerc
                    log(f"  ! [STRAT] eroare ({e.__class__.__name__}: {e}) — reincerc")
                    time.sleep(self.p.check_minutes * 60)
                    continue
                avg = self._avg()
                pos = f"qty={self.s['qty']:.8f} avg={avg:.{self.price_dec}f}" if avg else "qty=0 (astept intrare)"
                log(f"  [STRAT] pret={price}  {pos}  "
                    f"NET={self.s['realized_net']:+.2f} (brut {self.s['realized_gross']:+.2f}, "
                    f"fee {self.s['fees_total']:.2f}) {self.ccy}  ord={len(self.s['orders'])}")
                time.sleep(self.p.check_minutes * 60)
        except KeyboardInterrupt:
            log("  [STRAT] oprit manual.")
            self._save()
