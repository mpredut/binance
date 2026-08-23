#!/usr/bin/env python3
"""Instrument- and currency-generic DCA plus take-profit engine.

Average-TP mode buys a discounted entry, adds DCA after configured drops, and closes
the position at its average-cost profit target. DCA-only mode accumulates without sale.
Dry-run is paper-only, per-cycle budget and buy-count caps bound exposure, and per-ticker
state survives restarts. T212 FX conversion fees apply on both BUY and SELL legs.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ipo_common import log, now_str, float_env, are_close
from ipo_notify import notify
from market_data import get_eur_usd, get_usd_ron, get_price_usd, t212_to_yahoo, trend_slope_pct
from t212_client import T212Client
from providers.execution_audit import AuditedStrategyExecutor, ExecutionAudit, new_intent_id
from providers.strategy_executor import ProviderError
from providers.t212_provider import T212Provider
from strategies.state_store import JsonStateStore

FX_FEE_PCT = 0.15  # T212 currency-conversion fee per direction.
_HERE = os.path.dirname(os.path.abspath(__file__))


def state_path_for(ticker: str) -> str:
    safe = "".join(c for c in ticker if c.isalnum() or c in "._-")
    return os.path.join(_HERE, f".state_{safe}.json")


@dataclass
class StratParams:
    currency: str            # Currency used by the monetary settings below.
    entry_amount: float      # Initial purchase size in that currency.
    entry_discount_pct: float
    dca_amount: float        # Size of one dip purchase in configured currency.
    dca_drop_pct: float
    check_minutes: float
    takeprofit_pct: float
    max_budget: float        # Per-cycle total cap in configured currency.
    max_dca_buys: int
    validity: str
    enable_takeprofit: bool
    order_ttl_min: float
    stop_loss_pct: float     # Sell all at this loss percentage; zero disables it.
    yahoo_sym: str = ""             # Configured Yahoo symbol or one derived from ticker.
    reentry_drop_pct: float = 0.0   # After TP, reenter only below the sale price by X percent.
    reentry_tolerance_pct: float = 0.0  # Near-threshold tolerance for reentry and DCA.
    tp_ladder: list = field(default_factory=list)   # Scale-out level/fraction pairs.
    fx_fee_pct: float = FX_FEE_PCT   # T212 FX fee per direction.
    loss_alert_step: float = 1.0     # Alert for each further unrealized-loss band.
    ladder_min_free: float = 6.0     # Minimum dollar amount left unreserved by TP ladder.
    sl_rebuy_enabled: bool = False   # Rebuy on a bounce from the post-stop low.
    sl_rebuy_bounce_pct: float = 1.2 # Bounce confirming the fall has stopped.
    dca_trend_gate_pct: float = 0.0  # Skip DCA below this confirmed downtrend slope; zero disables.
    trail_pct: float = 0.0           # Sell all after this drop from position peak; zero disables.
    trail_min_profit_pct: float = 5.0  # Keep trailing inactive until price gains this much over average.

    @classmethod
    def from_env(cls, env: dict | None = None) -> "StratParams":
        """Read the environment or an explicit mapping for collision-free multi-asset use."""
        e = os.environ if env is None else env
        mode = e.get("STRATEGY_MODE", "avg_tp").strip().lower()
        # Parse scale-out ladder percentages and fractions.
        _ladder = []
        for _part in (e.get("STRAT_TP_LADDER") or "").split(","):
            _part = _part.strip()
            if ":" in _part:
                _lvl, _frac = _part.split(":", 1)
                try:
                    _ladder.append((float(_lvl), float(_frac) / 100.0))
                except ValueError:
                    pass
        _budget = float_env("STRAT_MAX_BUDGET", e) or 2000.0
        # Size entry and DCA as budget percentages or legacy absolute fallbacks.
        _entry_pct = float_env("STRAT_ENTRY_PCT", e)
        _entry = (_budget * _entry_pct / 100.0) if _entry_pct else (float_env("STRAT_ENTRY", e) or 300.0)
        _dca_pct = float_env("STRAT_DCA_PCT", e)
        _dca = (_budget * _dca_pct / 100.0) if _dca_pct else (float_env("STRAT_DCA", e) or 150.0)
        # Auto DCA count derives from budget; otherwise use an explicit value or ten.
        _mdb = (e.get("STRAT_MAX_DCA_BUYS") or "").strip().lower()
        if _mdb in ("auto", "buget", "budget"):
            _max_dca = max(0, int((_budget - _entry) // _dca)) if _dca > 0 else 0
            _entry = _budget - _max_dca * _dca   # Entry absorbs remainder to cover the full budget.
        elif _mdb:
            _max_dca = int(float(_mdb))
        else:
            _max_dca = 10
        _fx_fee = float_env("STRAT_FX_FEE_PCT", e)
        if _fx_fee is None:                 # Zero validly represents a USD account without FX.
            _fx_fee = FX_FEE_PCT
        _loss_step = float_env("STRAT_LOSS_ALERT_STEP", e)
        if _loss_step is None:
            _loss_step = 1.0
        _ladder_free = float_env("STRAT_LADDER_MIN_FREE", e)
        if _ladder_free is None:
            _ladder_free = 6.0
        _trail_minp = float_env("STRAT_TRAIL_MIN_PROFIT_PCT", e)
        if _trail_minp is None:             # Zero validly enables immediate loss-cutting.
            _trail_minp = 5.0               # Match the original Binance/Kraken default.
        return cls(
            currency           = e.get("STRAT_CURRENCY", "RON").strip().upper(),
            entry_amount       = _entry,
            entry_discount_pct = float_env("STRAT_ENTRY_DISCOUNT_PCT", e) or 0.2,
            dca_amount         = _dca,
            dca_drop_pct       = float_env("STRAT_DCA_DROP_PCT", e) or 2.0,
            check_minutes      = float_env("STRAT_CHECK_MINUTES", e) or 5.0,
            takeprofit_pct     = float_env("STRAT_TAKEPROFIT_PCT", e) or 1.5,
            max_budget         = _budget,
            max_dca_buys       = _max_dca,
            validity           = "GOOD_TILL_CANCEL",
            enable_takeprofit  = (mode != "dca_only"),
            order_ttl_min      = float_env("STRAT_ORDER_TTL_MIN", e) or 10.0,
            stop_loss_pct      = float_env("STRAT_STOP_LOSS_PCT", e) or 0.0,
            yahoo_sym          = (e.get("YAHOO_SYMBOL") or "").strip(),
            reentry_drop_pct   = float_env("STRAT_REENTRY_DROP_PCT", e) or 0.0,
            reentry_tolerance_pct = float_env("STRAT_REENTRY_TOLERANCE_PCT", e) or 0.0,
            tp_ladder          = _ladder,
            fx_fee_pct         = _fx_fee,
            loss_alert_step    = _loss_step,
            ladder_min_free    = _ladder_free,
            sl_rebuy_enabled   = (e.get("STRAT_SL_REBUY_ENABLED", "false").strip().lower() == "true"),
            sl_rebuy_bounce_pct= float_env("STRAT_SL_REBUY_BOUNCE_PCT", e) or 1.2,
            dca_trend_gate_pct = float_env("STRAT_DCA_TREND_GATE_PCT", e) or 0.0,
            trail_pct          = float_env("STRAT_TRAIL_PCT", e) or 0.0,
            trail_min_profit_pct = _trail_minp,
        )


def _new_state() -> dict:
    return {
        "cycle": 1,
        "qty": 0.0,
        "cost_usd": 0.0,        # USD cost basis of held quantity.
        "spent_cash": 0.0,      # Current-cycle deployed cash for cap enforcement.
        "dca_buys": 0,
        "entry_price": None,
        "last_buy_price": None,
        "realized_pnl_usd": 0.0,   # Cumulative gross profit.
        "realized_net_usd": 0.0,   # Cumulative net profit after FX fees.
        "fees_usd": 0.0,           # Total paid FX fees.
        "loss_band": 0,         # Deepest alerted loss band for anti-spam.
        "tp_sold_levels": [],   # Ladder levels sold in the current cycle.
        "orders": [],           # {id, side, qty, limit, amount, kind, ts, level}
        "pos_peak": 0.0,        # Highest price while holding, for trailing crash breaker.
        "tr_alerted": False,    # Current-episode trailing notification anti-spam.
        "tr_armed": False,      # Arm only after the configured gain over average.
    }


def _sell_pnl(avg: float, price: float, qty: float, fee_pct: float = FX_FEE_PCT) -> tuple[float, float, float]:
    """Return gross, round-trip FX fee, and net for selling quantity at price."""
    gross = (price - avg) * qty
    fee = (fee_pct / 100.0) * (avg * qty + price * qty)
    return gross, fee, gross - fee


class Strategy:
    def __init__(self, client: T212Client, ticker: str, params: StratParams,
                 dry_run: bool = True, desktop: bool = False,
                 initial_state: dict | None = None,
                 fx_to_usd: float | None = None,
                 clock: Callable[[], float] | None = None,
                 trend_slope_provider: Callable[[str], float | None] | None = None,
                 execution_audit: ExecutionAudit | None = None):
        self.client = client
        self.ticker = ticker
        self.yahoo_sym = params.yahoo_sym or t212_to_yahoo(ticker)
        self.p = params
        self.dry_run = dry_run
        self.desktop = desktop
        self.ccy = params.currency
        self._clock = clock or time.time
        self._trend_slope_provider = trend_slope_provider or trend_slope_pct
        self.execution_audit = execution_audit or ExecutionAudit()
        # Financial strategy remains separate while submission/status/cancellation use
        # the same strict audited contract as spot_dca.
        self.executor = AuditedStrategyExecutor(
            T212Provider(
                client=client, live_enabled=not dry_run,
                order_validity=params.validity,
            ),
            audit=self.execution_audit, venue="T212",
        )
        self.fx_to_usd = self._fx_to_usd(params.currency) if fx_to_usd is None else fx_to_usd
        self.state_file = state_path_for(ticker)
        self._state_write_failed = False
        self.s = initial_state if initial_state is not None else self._load()
        self._paper_seq = 0

    def _now(self) -> float:
        return float(self._clock())

    # -- valuta ----------------------------------------------------------------
    def _fx_to_usd(self, currency: str) -> float:
        """Cati USD intr-o unitate din valuta data — generic pt orice valuta Yahoo."""
        if currency == "USD":
            return 1.0
        if currency == "EUR":
            return get_eur_usd()          # USD per EUR
        if currency == "RON":
            return 1.0 / get_usd_ron()    # USD per RON = 1 / (RON per USD)
        rate = get_price_usd(f"{currency}USD=X")   # generic: GBP, CHF, PLN...
        if rate:
            return rate
        log(f"  ! curs {currency}/USD indisponibil — tratez sumele ca USD (1:1). "
            f"Verifica STRAT_CURRENCY!")
        return 1.0

    # -- persistenta -----------------------------------------------------------
    def _store(self) -> JsonStateStore:
        return JsonStateStore(
            self.state_file, _new_state, label="T212",
            logger=log, fail_closed=not self.dry_run,
        )

    def _load(self) -> dict:
        return self._store().load()

    def _save(self) -> None:
        self._state_write_failed = True
        if self._store().save(self.s):
            self._state_write_failed = False

    # -- helperi ---------------------------------------------------------------
    def _avg_cost(self) -> float | None:
        return self.s["cost_usd"] / self.s["qty"] if self.s["qty"] > 1e-9 else None

    def _qty_for_amount(self, amount: float, price: float) -> float:
        usd = amount * self.fx_to_usd
        return round(usd / price, 2) if price > 0 else 0.0

    def _has_open(self, side: str) -> bool:
        return any(o["side"] == side for o in self.s["orders"])

    def _find_open(self, side: str) -> dict | None:
        for o in self.s["orders"]:
            if o["side"] == side:
                return o
        return None

    # -- aplicare fill ---------------------------------------------------------
    def _apply_fill(self, order: dict, qty: float, price: float) -> None:
        tag = "[PAPER] " if self.dry_run else ""
        if order["side"] == "BUY":
            self.s["qty"] += qty
            self.s["cost_usd"] += qty * price
            self.s["last_buy_price"] = price
            if self.s["entry_price"] is None:
                self.s["entry_price"] = price
            self.s["spent_cash"] += order.get("amount", 0.0)
            if order.get("kind") == "DCA":
                self.s["dca_buys"] += 1
            avg = self._avg_cost()
            log(f"  [STRAT] {tag}BUY FILLED {qty} @ {price:.2f} USD ({order.get('kind')})  "
                f"qty_total={self.s['qty']:.2f} avg={avg:.2f}")
            notify(title=f"{tag}{self.yahoo_sym} BUY {qty}@{price:.2f}",
                   body=(f"{order.get('kind')} | q{self.s['qty']:.2f} a{avg:.2f} | "
                         f"desf{self.s['spent_cash']:.0f}{self.ccy} DCA{self.s['dca_buys']}/{self.p.max_dca_buys}"),
                   source="T212", price=price, desktop=self.desktop)
            self._cancel_open("SELL")     # avg s-a schimbat -> reasezam TP
        else:  # SELL
            qty = min(qty, self.s["qty"])
            avg = self._avg_cost() or price
            gross, fee, net = _sell_pnl(avg, price, qty, self.p.fx_fee_pct)
            self.s["realized_pnl_usd"] += gross
            self.s["realized_net_usd"] += net
            self.s["fees_usd"] += fee
            self.s["qty"] -= qty
            # Cost basis belongs to remaining quantity; reduce it after partial scale-out
            # to avoid inflating the residual position's average.
            self.s["cost_usd"] = max(0.0, self.s["cost_usd"] - avg * qty)
            log(f"  [STRAT] {tag}SELL FILLED {qty} @ {price:.2f} USD  "
                f"brut={gross:+.2f}  fee={fee:.2f}  net={net:+.2f} USD")
            notify(title=f"{tag}{self.yahoo_sym} SELL {qty}@{price:.2f} N{net:+.2f}$",
                   body=(f"a{avg:.2f} · br{gross:+.2f} fee{fee:.2f} N{net:+.2f}$ | "
                         f"Ntot{self.s['realized_net_usd']:+.2f}$ | ciclu{self.s['cycle']} inchis"),
                   source="T212", price=price, desktop=self.desktop)
            if self.s["qty"] <= 1e-9:
                was_sl = bool(self.s.get("sl_pending"))
                pnl, net_tot, fees = (self.s["realized_pnl_usd"],
                                      self.s["realized_net_usd"], self.s["fees_usd"])
                nxt = self.s.get("cycle", 1) + 1
                self.s = _new_state()
                self.s["realized_pnl_usd"] = pnl
                self.s["realized_net_usd"] = net_tot
                self.s["fees_usd"] = fees
                self.s["cycle"] = nxt
                self.s["last_sell_price"] = price   # Reentry rule: do not buy back higher.
                if was_sl and self.p.sl_rebuy_enabled:
                    self.s["sl_rebuy"] = {"low": price, "sell_price": price}
                    log(f"  🟢 [STRAT] re-buy pe recul ARMAT dupa stop-loss "
                        f"(asteptam +{self.p.sl_rebuy_bounce_pct}% de la minim)")
                log(f"  [STRAT] === ciclu inchis, reincep (ciclu {nxt}) ===")

    # -- plasare / anulare -----------------------------------------------------
    def _place_buy(self, amount: float, limit: float, kind: str) -> None:
        qty = self._qty_for_amount(amount, limit)
        if qty <= 0:
            log("  ! [STRAT] qty 0 — sar")
            return
        if self.dry_run:
            self._paper_seq += 1
            log(f"  [STRAT] [PAPER] plasez BUY {kind} {qty} @ {limit:.2f} USD (~{amount:.0f} {self.ccy})")
            self.s["orders"].append({"id": f"PAPER-{self._paper_seq}", "side": "BUY", "qty": qty,
                                     "limit": round(limit, 2), "amount": amount, "kind": kind,
                                     "ts": self._now()})
            return
        intent_id = new_intent_id("T212", self.ticker, kind)
        try:
            order_id = self.executor.submit_order_with_intent(
                intent_id, self.ticker, "buy", qty, round(limit, 2),
                market=False, kind=kind,
            )
        except ProviderError as exc:
            log(f"  ! [STRAT] BUY {kind} esuat: {exc}")
            if "insufficient" in str(exc).lower():
                self.s["buy_backoff_until"] = self._now() + 1800
                log("  [STRAT] fonduri insuficiente — pauza cumparari 30 min (alimenteaza contul)")
            return
        log(f"  [STRAT] BUY {kind} plasat id={order_id} {qty} @ {limit:.2f}")
        self.s["orders"].append({
            "id": order_id, "side": "BUY", "qty": qty,
            "limit": round(limit, 2), "amount": amount, "kind": kind,
            "intent_id": intent_id, "market": False, "ts": self._now(),
        })
        self._save()

    def _place_sell(self, qty: float, limit: float, level: float | None = None,
                    kind: str = "TP", *, market: bool = False) -> bool:
        qty = round(float(qty), 2)
        if qty <= 0:
            log("  ! [STRAT] SELL qty 0 după rotunjire — păstrez dust-ul, nu trimit ordin")
            return False
        tag = f"+{level:g}%" if level is not None else ""
        if self.dry_run:
            self._paper_seq += 1
            order_type = "MKT" if market else f"@ {limit:.2f}"
            log(f"  [STRAT] [PAPER] plasez SELL {kind}{tag} {qty:.2f} {order_type} USD")
            self.s["orders"].append({"id": f"PAPER-{self._paper_seq}", "side": "SELL",
                                     "qty": qty, "limit": round(limit, 2),
                                     "kind": kind, "level": level, "market": market,
                                     "ts": self._now()})
            return True
        intent_id = new_intent_id("T212", self.ticker, kind)
        try:
            order_id = self.executor.submit_order_with_intent(
                intent_id, self.ticker, "sell", qty,
                None if market else round(limit, 2), market=market, kind=kind,
                reference_price=round(limit, 2) if market else None,
            )
        except ProviderError as exc:
            message = str(exc)
            log(f"  ! [STRAT] SELL {kind}{tag} esuat: {message}")
            if "selling-equity-not-owned" not in message:
                return False
            # Reset only when the venue confirms owned is effectively zero. A positive
            # owned amount with lower free balance must not erase a real position.
            _m = re.search(r'owned["\']?\s*[:=]\s*([0-9.]+)', message)
            if _m is None:
                log("  ! [STRAT] selling-not-owned fara cantitatea owned — NU resetez pozitia")
                return False
            _owned = float(_m.group(1))
            if _owned <= 1e-6:
                log("  ! [STRAT] T212 confirmă owned=0 — resetez starea (poziția închisă/vândută)")
                self.s["qty"] = 0.0
                self.s["cost_usd"] = 0.0
                self.s["spent_cash"] = 0.0
                self.s["orders"] = []
                self.s["locked_zero_until"] = self._now() + 300  # ignora adoptie stala 5 min
                self._save()
            else:
                log(f"  ! [STRAT] selling-not-owned dar owned={_owned} (free<ordin) — NU resetez pozitia")
            return False
        order_type = "MARKET" if market else f"@ {limit:.2f}"
        log(f"  [STRAT] SELL {kind}{tag} plasat id={order_id} {qty:.2f} {order_type}")
        self.s["orders"].append({
            "id": order_id, "side": "SELL", "qty": round(qty, 2),
            "limit": round(limit, 2), "kind": kind, "level": level,
            "intent_id": intent_id, "market": market, "ts": self._now(),
        })
        self._save()
        return True

    def _cancel_open(self, side: str) -> bool:
        o = self._find_open(side)
        if not o:
            return True
        return self._cancel_specific(o)

    def _cancel_specific(self, o: dict) -> bool:
        if self.dry_run or str(o["id"]).startswith("PAPER"):
            self._remove_order(o)
            log(f"  [STRAT] anulat ordin {o.get('side', '?')} {o['id']}")
            return True
        if o.get("cancel_requested"):
            return True
        try:
            self.executor.cancel_order_with_intent(
                o.get("intent_id") or f"legacy-t212-{self.ticker}-{o['id']}",
                self.ticker, str(o["id"]),
            )
        except ProviderError as exc:
            log(f"  ! [STRAT] cancel esuat pentru {o['id']}: {exc} — ordinul ramane urmarit")
            return False
        o["cancel_requested"] = True
        o["cancel_ts"] = self._now()
        self._save()
        log(f"  [STRAT] cancel solicitat {o.get('side', '?')} {o['id']} — astept status terminal")
        return True

    def _cancel_all_orders(self) -> bool:
        success = True
        for order in list(self.s["orders"]):
            if not self._cancel_specific(order):
                success = False
        return success

    def _manage_tp_ladder(self, held: float, avg: float) -> None:
        """Maintain one SELL per unsold ladder level at its target over average.

        Size remaining levels proportionally from current holdings and replace them when
        average cost changes after DCA. Never recreate levels already sold.
        """
        # Cancel any legacy non-level SELL before creating a ladder.
        for o in [x for x in self.s["orders"] if x["side"] == "SELL" and x.get("level") is None]:
            if not self._cancel_specific(o):
                return  # Do not overlap a ladder with a potentially active SELL.
            if o in self.s["orders"]:
                return  # live: cererea e acceptata, dar asteptam statusul terminal
        sold = set(self.s.get("tp_sold_levels", []))
        remaining = [(lvl, frac) for (lvl, frac) in self.p.tp_ladder if lvl not in sold]
        total = sum(f for _, f in remaining)
        if total <= 0 or held <= 1e-9:
            return
        # Highest tranche receives the remainder minus an unreserved buffer, preventing
        # rounding oversell while satisfying T212's minimum-open-position rule.
        desired = {}   # nivel -> (qty, limit)
        buf = (self.p.ladder_min_free / avg) if (self.p.ladder_min_free > 0 and avg > 0) else 0.0
        remaining_sorted = sorted(remaining, key=lambda x: x[0])
        acc = 0.0
        for i, (lvl, frac) in enumerate(remaining_sorted):
            last = (i == len(remaining_sorted) - 1)
            q = round(held - acc - buf, 2) if last else round(held * frac / total, 2)
            acc += q
            if q > 0:
                desired[lvl] = (q, round(avg * (1 + lvl / 100.0), 2))
        open_sells = {o.get("level"): o for o in self.s["orders"]
                      if o["side"] == "SELL" and o.get("level") is not None}
        # Cancel unwanted orders or orders whose price/quantity changed after DCA.
        for lvl, o in list(open_sells.items()):
            d = desired.get(lvl)
            if d is None or abs(o["limit"] - d[1]) / d[1] > 0.001 or abs(o["qty"] - d[0]) > 1e-6:
                if self._cancel_specific(o) and o not in self.s["orders"]:
                    open_sells.pop(lvl, None)
        # Place missing levels. Back off for 30 minutes after persistent tranche failure
        # instead of retrying on every tick.
        now = self._now()
        fails = self.s.setdefault("tp_fail_until", {})
        for lvl, (q, lim) in desired.items():
            if lvl in open_sells:
                continue
            if now < fails.get(str(lvl), 0):
                continue
            if not self._place_sell(q, lim, level=lvl):
                fails[str(lvl)] = now + 1800

    # -- Reconciliation --------------------------------------------------------
    def _remove_order(self, o: dict) -> None:
        if o in self.s["orders"]:
            self.s["orders"].remove(o)

    def reconcile(self, price: float) -> None:
        if self.dry_run:
            self._reconcile_paper(price)
        else:
            self._reconcile_real(price)

    # -- Paper reconciliation --------------------------------------------------
    def _reconcile_paper(self, price: float) -> None:
        # Assume BUY fills at limit; SELL fills when price reaches its limit.
        for side in ("BUY", "SELL"):
            for o in [x for x in self.s["orders"] if x["side"] == side]:
                if o not in self.s["orders"]:
                    continue
                if side == "BUY":
                    self._remove_order(o)
                    self._apply_fill(o, o["qty"], o["limit"])
                elif o.get("market") or price >= o["limit"]:
                    self._remove_order(o)
                    if o.get("level") is not None:
                        self.s.setdefault("tp_sold_levels", []).append(o["level"])
                    self._apply_fill(o, o["qty"], price if o.get("market") else o["limit"])

    # -- Live reconciliation: T212 portfolio is the source of truth -------------
    # Filled orders disappear from the order endpoint while their position appears in portfolio.
    def _portfolio_position(self) -> tuple[float, float] | None:
        pf = self.client.get_portfolio()
        if pf is None:
            return None
        for p in pf:
            if str(p.get("ticker", "")).upper() == self.ticker.upper():
                return float(p.get("quantity") or 0.0), float(p.get("averagePrice") or 0.0)
        return 0.0, 0.0   # No holding.

    def _active_order_ids(self) -> set | None:
        orders = self.client.list_active_orders()
        if orders is None:
            return None
        return {str(o.get("id")) for o in orders
                if str(o.get("ticker", "")).upper() == self.ticker.upper()}

    def _tracked_order_status(self, order: dict, cache: dict[str, object]):
        """Return strict order status, cached for one reconciliation pass."""
        key = str(order.get("id"))
        if key in cache:
            return cache[key]
        try:
            status = self.executor.order_status_with_intent(
                order.get("intent_id") or f"legacy-t212-{self.ticker}-{key}",
                self.ticker, key,
            )
        except ProviderError as exc:
            log(f"  ! [STRAT] status T212 {key} indisponibil: {exc} — pastrez ordinul")
            cache[key] = None
            return None
        cache[key] = status
        return status

    def _exact_execution_price(self, side: str, position_delta: float,
                               cache: dict[str, object]) -> float | None:
        """Return the weighted price from tracked orders' cumulative deltas.

        The portfolio remains the source of truth for quantity. Order prices are
        used only when the sum of the deltas matches the position change.
        """
        rows = []
        total_qty = total_cost = 0.0
        for order in [o for o in self.s["orders"] if o.get("side") == side]:
            status = self._tracked_order_status(order, cache)
            if status is None:
                continue
            try:
                cumulative_qty = float(status.filled_qty or 0.0)
                cumulative_cost = float(status.cost or 0.0)
                cumulative_fee = float(status.fee or 0.0)
                applied_qty = float(order.get("applied_fill_qty") or 0.0)
                applied_cost = float(order.get("applied_fill_cost") or 0.0)
                applied_fee = float(order.get("applied_fill_fee") or 0.0)
            except (TypeError, ValueError):
                continue
            if cumulative_qty + 1e-9 < applied_qty or cumulative_cost + 1e-9 < applied_cost:
                log(f"  ! [STRAT] fill T212 {order.get('id')} a regresat cumulativ — ignor")
                continue
            delta_qty = max(0.0, cumulative_qty - applied_qty)
            delta_cost = max(0.0, cumulative_cost - applied_cost)
            delta_fee = cumulative_fee - applied_fee
            if delta_qty > 1e-9:
                rows.append((order, cumulative_qty, cumulative_cost, cumulative_fee,
                             delta_qty, delta_cost, delta_fee, status.status))
                total_qty += delta_qty
                total_cost += delta_cost

        tolerance = max(0.011, abs(position_delta) * 1e-6)
        if not rows or abs(total_qty - position_delta) > tolerance or total_cost <= 0:
            if rows:
                log(f"  ! [STRAT] fill-uri T212 {side} ({total_qty:.4f}) != delta portfolio "
                    f"({position_delta:.4f}) — folosesc fallback-ul de portofoliu")
            return None

        for (order, cumulative_qty, cumulative_cost, cumulative_fee,
             _delta_qty, _delta_cost, _delta_fee, _venue_status) in rows:
            order["applied_fill_qty"] = cumulative_qty
            order["applied_fill_cost"] = cumulative_cost
            order["applied_fill_fee"] = cumulative_fee
        return total_cost / total_qty

    def _reconcile_real(self, price: float) -> None:
        real = self._portfolio_position()
        if real is None:
            # Debounce: log initially and every ten minutes, not on every tick.
            now = self._now()
            last = getattr(self, "_pf_unavail_logged", 0)
            if now - last > 600:
                log("  [STRAT] portofoliu indisponibil — sar reconcilierea (suprima repetatele 10 min)")
                self._pf_unavail_logged = now
            return
        real_qty, real_avg = real
        active = self._active_order_ids()
        if active is None:
            active = {str(o["id"]) for o in self.s["orders"]}  # Cannot list, so retain tracked orders.

        prev_qty = self.s["qty"]
        prev_avg = self._avg_cost() or real_avg
        status_cache: dict[str, object] = {}

        # --- Executed BUY: the position increased (or adopt an existing position). ---
        if real_qty > prev_qty + 1e-6 and self._now() < self.s.get("locked_zero_until", 0):
            log("  [STRAT] adoptie ignorata — portfolio stale (not-owned recent, lock activ)")
            return
        if real_qty > prev_qty + 1e-6:
            fq = real_qty - prev_qty
            exact_price = self._exact_execution_price("BUY", fq, status_cache)
            fp = exact_price or (
                (real_avg * real_qty - prev_avg * prev_qty) / fq if fq > 0 else real_avg
            )
            source_order = next(
                (o for o in self.s["orders"] if o.get("side") == "BUY"), None,
            )
            is_adoption = source_order is None and prev_qty < 1e-9
            is_dca = bool(
                source_order is not None
                and source_order.get("kind") == "DCA"
                and not source_order.get("dca_counted")
            )
            if is_dca:
                # Count a partially filled DCA order once, not once per poll.
                source_order["dca_counted"] = True
            self.s["last_buy_price"] = fp
            if self.s["entry_price"] is None:
                self.s["entry_price"] = fp
            if is_dca:
                self.s["dca_buys"] += 1
            self.s["qty"] = real_qty
            self.s["cost_usd"] = real_qty * real_avg
            self.s["spent_cash"] = round(real_qty * real_avg / self.fx_to_usd, 2)
            kind_label = (
                "ADOPTAT" if is_adoption
                else str((source_order or {}).get("kind") or ("DCA" if prev_qty > 1e-9 else "ENTRY"))
            )
            log(f"  [STRAT] BUY EXECUTAT {fq:.4f} @ {fp:.2f} USD "
                f"({kind_label})  qty={real_qty:.4f} avg={real_avg:.2f}")
            notify(title=f"{self.yahoo_sym} {'ADOPTAT' if is_adoption else 'BUY'} {fq:.4f}@a{real_avg:.2f}",
                   body=(f"{kind_label} | q{real_qty:.4f} a{real_avg:.2f} p~{fp:.2f} | "
                         f"desf{self.s['spent_cash']:.0f}{self.ccy} DCA{self.s['dca_buys']}/{self.p.max_dca_buys}"),
                   source="T212", price=fp, desktop=self.desktop)
            self._cancel_open("SELL")   # The average changed; rebuild TP on the next step.

        # --- Executed SELL: the position decreased. ---
        elif real_qty < prev_qty - 1e-6:
            sold = prev_qty - real_qty
            exact_price = self._exact_execution_price("SELL", sold, status_cache)
            sell_price = exact_price or price
            gross, fee, net = _sell_pnl(prev_avg, sell_price, sold, self.p.fx_fee_pct)
            self.s["realized_pnl_usd"] += gross
            self.s["realized_net_usd"] += net
            self.s["fees_usd"] += fee
            self.s["qty"] = real_qty
            self.s["cost_usd"] = real_qty * real_avg
            self.s["spent_cash"] = round(real_qty * real_avg / self.fx_to_usd, 2)
            self.s["last_sell_price"] = sell_price
            approx = "" if exact_price is not None else "~"
            log(f"  [STRAT] SELL EXECUTAT {sold:.4f} @ {approx}{sell_price:.2f} USD  "
                f"brut={gross:+.2f}  fee={fee:.2f}  net={net:+.2f} USD")
            notify(title=f"{self.yahoo_sym} SELL {sold:.4f}@{approx}{sell_price:.2f} N{net:+.2f}$",
                   body=f"a{prev_avg:.2f} · br{gross:+.2f} fee{fee:.2f} N{net:+.2f}$ | Ntot{self.s['realized_net_usd']:+.2f}$",
                   source="T212", price=sell_price, desktop=self.desktop)

        else:
            # Unchanged position: synchronize state with the actual position.
            self.s["qty"] = real_qty
            self.s["cost_usd"] = real_qty * real_avg
            if real_qty > 1e-9:
                self.s["spent_cash"] = round(real_qty * real_avg / self.fx_to_usd, 2)

        # --- Remove inactive orders and enforce the TTL for stale BUY orders. ---
        for o in list(self.s["orders"]):
            if str(o["id"]).startswith("PAPER"):
                continue
            if str(o["id"]) not in active:
                status = self._tracked_order_status(o, status_cache)
                if status is None or status.status not in {"closed", "canceled", "expired"}:
                    continue
                if (o["side"] == "SELL" and o.get("level") is not None
                        and float(status.filled_qty or 0.0) > 1e-9):
                    self.s.setdefault("tp_sold_levels", []).append(o["level"])
                self._remove_order(o)
            elif (o["side"] == "BUY"
                  and (self._now() - o.get("ts", 0)) / 60 > self.p.order_ttl_min
                  and price > o["limit"] * 1.003):
                log(f"  [STRAT] BUY {o['id']} neexecutat, pret a urcat — anulez & reasez")
                self._cancel_specific(o)

        # --- Closed cycle (the full position was sold): start a new cycle. ---
        if real_qty <= 1e-9 and prev_qty > 1e-9:
            pnl, net_tot, fees = (self.s["realized_pnl_usd"],
                                  self.s["realized_net_usd"], self.s["fees_usd"])
            was_sl = bool(self.s.get("sl_pending"))   # Was this a catastrophic stop-loss exit?
            exit_price = self.s.get("last_sell_price") or price
            nxt = self.s.get("cycle", 1) + 1
            self.s = _new_state()
            self.s["realized_pnl_usd"] = pnl
            self.s["realized_net_usd"] = net_tot
            self.s["fees_usd"] = fees
            self.s["cycle"] = nxt
            self.s["last_sell_price"] = exit_price
            if was_sl and self.p.sl_rebuy_enabled:   # Rebuy on a bounce, not below the sale, to catch recovery.
                self.s["sl_rebuy"] = {"low": exit_price, "sell_price": exit_price}
                log(f"  🟢 [STRAT] re-buy pe recul ARMAT dupa stop-loss (asteptam +{self.p.sl_rebuy_bounce_pct}% de la minim)")
            log(f"  [STRAT] === ciclu inchis, reincep (ciclu {nxt}) ===")

    # -- decision step ---------------------------------------------------------
    def _check_trailing(self, price: float) -> bool:
        """Sell the entire position after a ``trail_pct`` drop from its peak.

        This crash breaker complements the fixed average-cost stop loss by exiting
        a sustained decline earlier. It reuses ``sl_rebuy`` to reenter on a bounce.
        Return ``True`` when triggered so processing of the current tick stops.
        """
        if self.p.trail_pct <= 0 or self.s["qty"] <= 1e-9:
            self.s["pos_peak"] = 0.0            # Flat or disabled trailing: reset the peak.
            self.s["tr_alerted"] = False
            self.s["tr_armed"] = False          # A new cycle starts with a new warm-up.
            return False
        # WARM-UP (matching Binance/Kraken trailing_core): keep trailing inactive until
        # price rises trail_min_profit_pct above average. This protects gains without
        # selling on an ordinary dip; stop_loss_pct still handles catastrophic loss.
        # A value of zero arms trailing immediately.
        if not self.s.get("tr_armed"):
            minp = self.p.trail_min_profit_pct
            avg = self._avg_cost()
            if minp > 0 and (not avg or price < avg * (1 + minp / 100.0)):
                return False                    # Warming up: do not track a peak or sell.
            self.s["tr_armed"] = True
            self.s["pos_peak"] = price          # Start the peak at the activation price.
            log(f"  [STRAT] trailing ARMAT: pret {price:.2f} ≥ avg{(avg or 0):.2f}+{minp}% — protejez de-acum (-{self.p.trail_pct}% de la peak)")
        peak = self.s.get("pos_peak", 0.0) or 0.0
        if price > peak:
            self.s["pos_peak"] = peak = price   # Track the high while holding the position.
        if peak <= 0:
            return False
        drop_pct = (peak - price) / peak * 100
        if drop_pct < self.p.trail_pct:
            return False
        # The peak drawdown reached the threshold: sell all once, replacing only a stale limit.
        tr = next((o for o in self.s["orders"] if o.get("kind") == "TR"), None)
        if tr is None or (not tr.get("market") and price < tr["limit"]):
            if not self._cancel_all_orders():
                log("  ! [STRAT] TRAILING amanat: cel putin un ordin nu a putut fi anulat")
                return True
            placed = self._place_sell(
                self.s["qty"], round(price, 2), kind="TR", market=True,
            )
            if not placed:
                return True
            self.s["sl_pending"] = True         # Arm bounce reentry as for a catastrophic stop loss.
        if not self.s.get("tr_alerted"):
            self.s["tr_alerted"] = True
            log(f"  📉 [STRAT] TRAILING: -{drop_pct:.2f}% de la peak {peak:.2f} >= {self.p.trail_pct}% — VAND TOT")
            notify(title=f"📉 TRAILING {self.yahoo_sym} -{drop_pct:.1f}%",
                   body=f"de la peak{peak:.2f} ≥{self.p.trail_pct}% — vand tot",
                   source="T212", price=price, desktop=self.desktop)
        return True

    def _check_stop_loss(self, price: float) -> bool:
        """Close the entire position when unrealized loss exceeds the threshold."""
        if self.p.stop_loss_pct <= 0:
            return False
        avg = self._avg_cost()
        if not avg:
            return False
        loss_pct = (avg - price) / avg * 100   # A long loses when price is below average cost.
        if loss_pct < self.p.stop_loss_pct:
            self.s["sl_alerted"] = False        # Recovery above threshold permits a new alert.
            self.s["sl_pending"] = False        # End this stop-loss episode after recovery.
            return False
        # Anti-spam: place the stop once and replace it only if price moved below its limit.
        sl = next((o for o in self.s["orders"] if o.get("kind") == "SL"), None)
        if sl is None or (not sl.get("market") and price < sl["limit"]):
            if not self._cancel_all_orders():
                log("  ! [STRAT] STOP amanat: cel putin un ordin nu a putut fi anulat")
                return True
            placed = self._place_sell(
                self.s["qty"], round(price, 2), kind="SL", market=True,
            )
            if not placed:
                return True
            self.s["sl_pending"] = True         # Mark this episode so cycle closure arms bounce reentry.
        if not self.s.get("sl_alerted"):           # Notify only once per episode.
            self.s["sl_alerted"] = True
            log(f"  🛑 [STRAT] STOP-LOSS: pierdere {loss_pct:.2f}% >= {self.p.stop_loss_pct}% — VAND TOT (taie pierderea)")
            notify(title=f"🛑 SL {self.yahoo_sym} -{loss_pct:.1f}%",
                   body=f"pierdere {loss_pct:.1f}% ≥prag{self.p.stop_loss_pct}% — vand tot",
                   source="T212", price=price, desktop=self.desktop)
        return True

    def _check_loss_alert(self, price: float) -> None:
        """Send an informational alert for each new unrealized-loss band.

        This does not sell. One notification per ``loss_alert_step`` band avoids
        spam, and recovery is silent.
        """
        step = self.p.loss_alert_step
        avg = self._avg_cost()
        if step <= 0 or not avg:
            return
        loss_pct = (avg - price) / avg * 100
        band = int(loss_pct // step) if loss_pct > 0 else 0
        # High-water mark: notify only when loss reaches a new maximum band.
        # Do not lower it on recovery, so revisiting an alerted level does not notify again.
        # Cycle closure resets it through _new_state (loss_band=0).
        if band > self.s.get("loss_band", 0):
            log(f"  📉 [STRAT] {self.yahoo_sym} pierdere -{loss_pct:.1f}% (prag {band*step:.0f}%)")
            notify(title=f"📉 {self.yahoo_sym} -{loss_pct:.1f}%",
                   body=f"pierdere -{loss_pct:.1f}% (peste {band*step:.0f}%) | q{self.s['qty']:.2f} a{avg:.2f} p{price:.2f}",
                   source="T212", price=price, desktop=self.desktop)
            self.s["loss_band"] = band

    def _handle_sl_rebuy(self, price: float) -> None:
        """Rebuy after a catastrophic stop loss once price bounces from its low.

        Track the post-sale low and reenter when price recovers by
        ``sl_rebuy_bounce_pct``. This mirrors Binance/Kraken trailing reentry and
        targets recovery rather than a falling price.
        """
        rb = self.s.get("sl_rebuy")
        if not rb:
            return
        rb["low"] = min(rb.get("low", price), price)          # Track the post-stop low.
        if price < rb["low"] * (1 + self.p.sl_rebuy_bounce_pct / 100.0):
            return                                            # Wait until the bounce is confirmed.
        self.s.pop("sl_rebuy", None)                          # Consume the one-tranche reentry arm.
        if self.s["spent_cash"] + self.p.entry_amount > self.p.max_budget:
            log(f"  [STRAT] re-buy SL anulat — plafon buget {self.p.max_budget:.0f} {self.ccy} atins")
            return
        disc = 1 - self.p.entry_discount_pct / 100
        log(f"  🟢 [STRAT] RE-BUY dupa SL: recul +{self.p.sl_rebuy_bounce_pct}% de la minim {rb['low']:.2f} — reintru ENTRY")
        notify(title=f"🟢 {self.yahoo_sym} RE-BUY dupa stop-loss",
               body=(f"recul +{self.p.sl_rebuy_bounce_pct}% de la min {rb['low']:.2f} — "
                     f"reintru cu {self.p.entry_amount:.0f}{self.ccy}"),
               source="T212", price=price, desktop=self.desktop)
        self._place_buy(self.p.entry_amount, price * disc, kind="ENTRY")

    def step(self, price: float) -> None:
        held = self.s["qty"]
        self._check_loss_alert(price)   # Alert on deeper loss while holding; never sells.
        disc = 1 - self.p.entry_discount_pct / 100

        in_backoff = self._now() < self.s.get("buy_backoff_until", 0)

        if held <= 1e-9:
            if in_backoff:   # After insufficient funds, avoid buys while the account is empty.
                return
            if self._has_open("BUY"):
                return
            # Rebuy on a bounce after a catastrophic stop loss, as on Binance/Kraken.
            # While armed, it takes priority over reentry below the previous sale.
            if self.s.get("sl_rebuy"):
                self._handle_sl_rebuy(price)
                return
            # Kraken-style reentry rule: do not buy back above the previous sale price.
            lsp = self.s.get("last_sell_price")
            rdp = self.p.reentry_drop_pct
            if rdp > 0 and lsp:
                prag = lsp * (1 - rdp / 100)
                # Treat a value close to the threshold as reached (deterministic are_close).
                if price > prag and not are_close(price, prag, self.p.reentry_tolerance_pct):
                    log(f"  [STRAT] reintrare blocata: {price:.2f} > prag {prag:.2f} "
                        f"(vandut la {lsp:.2f}, astept -{rdp}%)")
                    return
            if self.s["spent_cash"] + self.p.entry_amount > self.p.max_budget:
                log(f"  [STRAT] plafon buget {self.p.max_budget:.0f} {self.ccy} atins — nu intru")
                return
            self._place_buy(self.p.entry_amount, price * disc, kind="ENTRY")
            return

        # Check the tighter trailing crash breaker before the catastrophic stop loss.
        if self._check_trailing(price):
            return

        # Stop loss cuts the loss before DCA or take-profit handling.
        if self._check_stop_loss(price):
            return

        avg = self._avg_cost()

        if self.p.enable_takeprofit and avg:
            if self.p.tp_ladder:
                self._manage_tp_ladder(held, avg)        # Scale out in ladder steps.
            else:
                target = avg * (1 + self.p.takeprofit_pct / 100)
                sell = self._find_open("SELL")
                if sell is None:
                    self._place_sell(held, target)
                elif abs(sell["limit"] - target) / target > 0.001 or abs(sell["qty"] - held) > 1e-6:
                    if self._cancel_open("SELL") and sell not in self.s["orders"]:
                        self._place_sell(held, target)

        prag_dca = (self.s["last_buy_price"] * (1 - self.p.dca_drop_pct / 100)
                    if self.s["last_buy_price"] else None)
        if (not in_backoff
                and self.s["dca_buys"] < self.p.max_dca_buys
                and prag_dca
                # Treat a price close to the threshold as reached, using reentry tolerance.
                and (price <= prag_dca or are_close(price, prag_dca, self.p.reentry_tolerance_pct))
                and self.s["spent_cash"] + self.p.dca_amount <= self.p.max_budget
                and not self._has_open("BUY")):
            # Binance/Kraken-style trend gate: skip DCA during a confirmed downtrend
            # so capital is not added to a falling asset. Zero disables the gate.
            # During a sustained dip, the DCA condition is true on every tick (~18s),
            # so a five-minute cache prevents repeated Yahoo calls and log spam. The
            # slope already uses five-minute bars.
            if self.p.dca_trend_gate_pct > 0:
                if self._now() < getattr(self, "_dca_gate_until", 0):
                    return   # Recently trend-blocked; retry after the cache window.
                slope = self._trend_slope_provider(self.yahoo_sym)
                if slope is not None and slope < -self.p.dca_trend_gate_pct:
                    self._dca_gate_until = self._now() + 300
                    log(f"  [STRAT] DCA BLOCAT de trend: panta {slope:+.3f}%/bara "
                        f"< -{self.p.dca_trend_gate_pct}% (downtrend) — re-verific in 5 min")
                    return
            log(f"  [STRAT] dip: {price:.2f} <= {self.s['last_buy_price']:.2f}"
                f"×(1-{self.p.dca_drop_pct}%) — DCA")
            self._place_buy(self.p.dca_amount, price * disc, kind="DCA")

    # -- main loop -------------------------------------------------------------
    def run(self) -> None:
        mode = "avg_tp" if self.p.enable_takeprofit else "dca_only"
        log("  === STRATEGIE PORNITA ===")
        log(f"      instrument : {self.ticker}  (pret via {self.yahoo_sym})")
        log(f"      mod        : {mode}   {'[PAPER]' if self.dry_run else '⚠ REAL — BANI ADEVARATI'}")
        log(f"      intrare    : {self.p.entry_amount:.0f} {self.ccy} @ market-{self.p.entry_discount_pct}%")
        log(f"      DCA        : {self.p.dca_amount:.0f} {self.ccy} la fiecare -{self.p.dca_drop_pct}% "
            f"(max {self.p.max_dca_buys})")
        if self.p.enable_takeprofit:
            log(f"      take-profit: +{self.p.takeprofit_pct}% fata de pret mediu")
        else:
            log("      take-profit: dezactivat (dca_only)")
        log(f"      PLAFON     : {self.p.max_budget:.0f} {self.ccy} / ciclu")
        log(f"      check      : la {self.p.check_minutes:.0f} min   |  1 {self.ccy} = {self.fx_to_usd:.4f} USD")
        log(f"      ! prag rentabilitate ~{self.p.fx_fee_pct*2:.2f}% (FX) + spread; TP={self.p.takeprofit_pct}%")

        try:
            while True:
                price = get_price_usd(self.yahoo_sym)
                if price is None:
                    log("  [STRAT] pret indisponibil — reincerc")
                    time.sleep(self.p.check_minutes * 60)
                    continue
                try:
                    # After a disk error, make no new decisions until the current
                    # snapshot can be persisted again.
                    if self._state_write_failed:
                        self._save()
                    self.reconcile(price)
                    self.step(price)
                    self._save()
                except Exception as e:  # noqa: BLE001 — Resilience: retry network/API failures.
                    log(f"  ! [STRAT] eroare ({e.__class__.__name__}: {e}) — reincerc")
                    time.sleep(self.p.check_minutes * 60)
                    continue

                avg = self._avg_cost()
                net = self.s.get("realized_net_usd", 0.0)
                fees = self.s.get("fees_usd", 0.0)
                if avg:
                    log(f"  [STRAT] pret={price:.2f}  qty={self.s['qty']:.2f}  avg={avg:.2f}  "
                        f"desf={self.s['spent_cash']:.0f}{self.ccy}  "
                        f"NET={net:+.2f}USD (brut {self.s['realized_pnl_usd']:+.2f}, fee {fees:.2f})  "
                        f"ord={len(self.s['orders'])}")
                else:
                    log(f"  [STRAT] pret={price:.2f}  qty=0  "
                        f"NET={net:+.2f}USD (brut {self.s['realized_pnl_usd']:+.2f}, fee {fees:.2f})  "
                        f"(astept intrare)")
                time.sleep(self.p.check_minutes * 60)
        except KeyboardInterrupt:
            log("  [STRAT] oprit manual.")
            self._save()
