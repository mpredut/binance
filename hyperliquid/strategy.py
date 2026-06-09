#!/usr/bin/env python3
"""
strategy.py — motor DCA + take-profit pe Hyperliquid (PERP long-only).

Aceeasi logica ca T212/Kraken, adaptata pentru Hyperliquid:
  * Pozitia (marime + pret mediu de intrare) vine direct din clearinghouseState
    -> reconciliere curata din pozitie (ca la T212, nu trebuie sa urmarim noi avg).
  * "buy" = deschide/mareste long; "TP" = ordin sell reduce-only la avg*(1+TP).
  * Fee Hyperliquid MINUSCUL (~0.045% taker / 0.015% maker) -> TP poate fi strans.

Long-only la levier mic (1x): lichidarea e foarte departe, comportament ~spot.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from common import log, now_str, float_env
from notify import notify
from hl_client import HLClient, HLError
from market_data import get_price

_HERE = os.path.dirname(os.path.abspath(__file__))
HL_FEE_PCT = float_env("HL_FEE_PCT") or 0.035   # per leg, estimativ (maker ~0.015, taker ~0.045)


def state_path_for(coin: str) -> str:
    safe = "".join(c for c in coin if c.isalnum() or c in "._-")
    return os.path.join(_HERE, f".state_{safe}.json")


@dataclass
class StratParams:
    currency: str
    entry_amount: float
    entry_discount_pct: float
    dca_amount: float
    dca_drop_pct: float
    check_minutes: float
    takeprofit_pct: float
    max_budget: float
    max_dca_buys: int
    enable_takeprofit: bool
    order_ttl_min: float

    @classmethod
    def from_env(cls) -> "StratParams":
        mode = os.environ.get("STRATEGY_MODE", "avg_tp").strip().lower()
        return cls(
            currency           = os.environ.get("STRAT_CURRENCY", "USDC").strip().upper(),
            entry_amount       = float_env("STRAT_ENTRY") or 50.0,
            entry_discount_pct = float_env("STRAT_ENTRY_DISCOUNT_PCT") or 0.2,
            dca_amount         = float_env("STRAT_DCA") or 30.0,
            dca_drop_pct       = float_env("STRAT_DCA_DROP_PCT") or 2.0,
            check_minutes      = float_env("STRAT_CHECK_MINUTES") or 1.0,
            takeprofit_pct     = float_env("STRAT_TAKEPROFIT_PCT") or 0.5,
            max_budget         = float_env("STRAT_MAX_BUDGET") or 500.0,
            max_dca_buys       = int(float_env("STRAT_MAX_DCA_BUYS") or 10),
            enable_takeprofit  = (mode != "dca_only"),
            order_ttl_min      = float_env("STRAT_ORDER_TTL_MIN") or 10.0,
        )


def _sell_pnl(avg: float, price: float, qty: float) -> tuple[float, float, float]:
    gross = (price - avg) * qty
    fee = (HL_FEE_PCT / 100.0) * (avg * qty + price * qty)
    return gross, fee, gross - fee


def _new_state() -> dict:
    return {
        "cycle": 1, "qty": 0.0, "cost": 0.0, "spent": 0.0, "dca_buys": 0,
        "entry_price": None, "last_buy_price": None,
        "realized_gross": 0.0, "realized_net": 0.0, "fees_total": 0.0,
        "orders": [],   # {oid, side, sz, px, amount, kind, ts}
    }


class Strategy:
    def __init__(self, client: HLClient, coin: str, params: StratParams,
                 dry_run: bool = True, desktop: bool = False, leverage: int = 1):
        self.client = client
        self.coin = coin
        self.p = params
        self.ccy = params.currency
        self.dry_run = dry_run
        self.desktop = desktop
        self.leverage = leverage
        self.state_file = state_path_for(coin)
        self.s = self._load()
        self._paper_seq = 0
        self.sz_dec = 2
        try:
            self.sz_dec = client.sz_decimals(coin)
        except HLError:
            pass

    # -- persistenta -----------------------------------------------------------
    def _load(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    st = json.load(f)
                m = _new_state(); m.update(st)
                log(f"  [STRAT] stare incarcata (ciclu {m.get('cycle')}, qty {m.get('qty')})")
                return m
            except (OSError, ValueError) as e:
                log(f"  ! [STRAT] stare invalida ({e}), pornesc curat")
        return _new_state()

    def _save(self) -> None:
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.s, f, indent=2)
        except OSError as e:
            log(f"  ! [STRAT] nu pot salva: {e}")

    # -- helperi ---------------------------------------------------------------
    def _avg(self) -> float | None:
        return self.s["cost"] / self.s["qty"] if self.s["qty"] > 1e-12 else None

    def _sz_for(self, amount: float, price: float) -> float:
        return round(amount / price, self.sz_dec) if price > 0 else 0.0

    def _has_open(self, side: str) -> bool:
        return any(o["side"] == side for o in self.s["orders"])

    def _find_open(self, side: str) -> dict | None:
        return next((o for o in self.s["orders"] if o["side"] == side), None)

    def _remove(self, o: dict) -> None:
        if o in self.s["orders"]:
            self.s["orders"].remove(o)

    # -- plasare ---------------------------------------------------------------
    def _place(self, side: str, sz: float, px: float, kind: str, amount: float = 0.0,
               reduce_only: bool = False) -> None:
        sz = round(sz, self.sz_dec)
        if sz <= 0:
            log("  ! [STRAT] sz 0 — sar"); return
        if self.dry_run:
            self._paper_seq += 1
            log(f"  [STRAT] [PAPER] {side.upper()} {kind} {sz} @ {px:.4f} {self.ccy}")
            self.s["orders"].append({"oid": f"PAPER-{self._paper_seq}", "side": side, "sz": sz,
                                     "px": px, "amount": amount, "kind": kind, "ts": time.time()})
            return
        ok, oid, msg = self.client.place_limit(self.coin, side == "buy", sz, px, reduce_only=reduce_only)
        if ok:
            log(f"  [STRAT] {side.upper()} {kind} plasat oid={oid} {sz} @ {px:.4f} ({msg})")
            self.s["orders"].append({"oid": oid, "side": side, "sz": sz, "px": px,
                                     "amount": amount, "kind": kind, "ts": time.time()})
        else:
            log(f"  ! [STRAT] {side} {kind} esuat: {msg}")

    def _cancel_open(self, side: str) -> None:
        o = self._find_open(side)
        if not o:
            return
        if not self.dry_run and not str(o["oid"]).startswith("PAPER"):
            self.client.cancel(self.coin, o["oid"])
        self._remove(o)
        log(f"  [STRAT] anulat {side} {o['oid']}")

    # -- reconciliere ----------------------------------------------------------
    def reconcile(self, price: float) -> None:
        if self.dry_run:
            self._reconcile_paper(price)
        else:
            self._reconcile_real(price)

    def _reconcile_paper(self, price: float) -> None:
        for side in ("buy", "sell"):
            for o in [x for x in self.s["orders"] if x["side"] == side]:
                if o not in self.s["orders"]:
                    continue
                if side == "buy" or price >= o["px"]:
                    self._remove(o)
                    self._apply_paper_fill(o, o["sz"], o["px"])

    def _apply_paper_fill(self, o, sz, px):
        if o["side"] == "buy":
            self.s["qty"] += sz; self.s["cost"] += sz * px
            self.s["last_buy_price"] = px
            if self.s["entry_price"] is None: self.s["entry_price"] = px
            self.s["spent"] += o.get("amount", sz * px)
            if o.get("kind") == "DCA": self.s["dca_buys"] += 1
            avg = self._avg()
            log(f"  [STRAT] [PAPER] BUY FILLED {sz} @ {px:.4f} qty={self.s['qty']} avg={avg:.4f}")
            self._cancel_open("sell")
        else:
            self._book_sell(self._avg() or px, px, sz)

    def _reconcile_real(self, price: float) -> None:
        try:
            szi, entry = self.client.position(self.coin)
        except HLError as e:
            log(f"  [STRAT] pozitie indisponibila ({e}) — sar reconcilierea"); return
        active = {o.get("oid") for o in self.client.open_orders(self.coin)}
        prev = self.s["qty"]

        if szi > prev + 1e-9:                       # BUY executat
            fq = szi - prev
            fp = entry if entry > 0 else price
            is_dca = prev > 1e-9
            self.s["last_buy_price"] = fp
            if self.s["entry_price"] is None: self.s["entry_price"] = fp
            if is_dca: self.s["dca_buys"] += 1
            self.s["qty"] = szi; self.s["cost"] = szi * entry
            self.s["spent"] = round(szi * entry, 2)
            log(f"  [STRAT] BUY EXECUTAT {fq:.6f} @ {fp:.4f} ({'DCA' if is_dca else 'ENTRY'}) qty={szi} avg={entry:.4f}")
            notify(title=f"{self.coin} BUY {fq:.6f} @ {fp:.4f}",
                   body=f"{'DCA' if is_dca else 'ENTRY'} qty {szi} avg {entry:.4f}\n{now_str()}",
                   source="hyperliquid", price=fp, desktop=self.desktop)
            self._cancel_open("sell")
        elif szi < prev - 1e-9:                     # SELL executat
            self._book_sell(self._avg() or entry or price, price, prev - szi)
            self.s["qty"] = szi; self.s["cost"] = szi * entry
        else:
            self.s["qty"] = szi
            if szi > 1e-12: self.s["cost"] = szi * entry

        for o in list(self.s["orders"]):
            if str(o["oid"]).startswith("PAPER"): continue
            if o["oid"] not in active:
                self._remove(o)
            elif o["side"] == "buy" and (time.time()-o.get("ts",0))/60 > self.p.order_ttl_min and price > o["px"]*1.003:
                log(f"  [STRAT] buy {o['oid']} neexecutat, pret a urcat — anulez & reasez")
                self.client.cancel(self.coin, o["oid"]); self._remove(o)

        if szi <= 1e-12 and prev > 1e-9:
            keep = (self.s["realized_gross"], self.s["realized_net"], self.s["fees_total"], self.s.get("cycle",1)+1)
            self.s = _new_state()
            (self.s["realized_gross"], self.s["realized_net"], self.s["fees_total"], self.s["cycle"]) = keep
            log(f"  [STRAT] === ciclu inchis, reincep (ciclu {self.s['cycle']}) ===")

    def _book_sell(self, avg: float, price: float, sz: float) -> None:
        gross, fee, net = _sell_pnl(avg, price, sz)
        self.s["realized_gross"] += gross; self.s["realized_net"] += net; self.s["fees_total"] += fee
        log(f"  [STRAT] {'[PAPER] ' if self.dry_run else ''}SELL {sz} @ {price:.4f}  "
            f"brut={gross:+.4f} fee={fee:.4f} net={net:+.4f} {self.ccy}")
        notify(title=f"{self.coin} SELL {sz} @ {price:.4f}  NET {net:+.2f} {self.ccy}",
               body=f"Brut {gross:+.4f} - fee {fee:.4f} = NET {net:+.4f}\nNet total {self.s['realized_net']:+.4f}\n{now_str()}",
               source="hyperliquid", price=price, desktop=self.desktop)
        # in PAPER reducem manual pozitia; in REAL o ia din clearinghouseState
        if self.dry_run:
            self.s["qty"] -= sz
            if self.s["qty"] <= 1e-12:
                keep = (self.s["realized_gross"], self.s["realized_net"], self.s["fees_total"], self.s.get("cycle",1)+1)
                self.s = _new_state()
                (self.s["realized_gross"], self.s["realized_net"], self.s["fees_total"], self.s["cycle"]) = keep
                log(f"  [STRAT] === ciclu inchis, reincep (ciclu {self.s['cycle']}) ===")

    # -- decizie ---------------------------------------------------------------
    def step(self, price: float) -> None:
        held = self.s["qty"]
        disc = 1 - self.p.entry_discount_pct / 100
        if held <= 1e-12:
            if self._has_open("buy"): return
            if self.s["spent"] + self.p.entry_amount > self.p.max_budget:
                log(f"  [STRAT] plafon {self.p.max_budget} {self.ccy} atins"); return
            self._place("buy", self._sz_for(self.p.entry_amount, price*disc), price*disc, "ENTRY", self.p.entry_amount)
            return
        avg = self._avg()
        if self.p.enable_takeprofit and avg:
            target = avg * (1 + self.p.takeprofit_pct/100)
            sell = self._find_open("sell")
            if sell is None:
                self._place("sell", held, target, "TP", reduce_only=True)
            elif abs(sell["px"]-target)/target > 0.001 or abs(sell["sz"]-held) > 1e-9:
                self._cancel_open("sell"); self._place("sell", held, target, "TP", reduce_only=True)
        if (self.s["dca_buys"] < self.p.max_dca_buys and self.s["last_buy_price"]
                and price <= self.s["last_buy_price"]*(1 - self.p.dca_drop_pct/100)
                and self.s["spent"] + self.p.dca_amount <= self.p.max_budget
                and not self._has_open("buy")):
            log(f"  [STRAT] dip {price:.4f} — DCA")
            self._place("buy", self._sz_for(self.p.dca_amount, price*disc), price*disc, "DCA", self.p.dca_amount)

    # -- bucla -----------------------------------------------------------------
    def run(self) -> None:
        mode = "avg_tp" if self.p.enable_takeprofit else "dca_only"
        if not self.dry_run and self.client.exchange:
            self.client.set_leverage(self.coin, self.leverage)
        log("  === STRATEGIE HYPERLIQUID PORNITA ===")
        log(f"      coin       : {self.coin} (perp, levier {self.leverage}x)  {'[PAPER]' if self.dry_run else '⚠ REAL'}")
        log(f"      mod        : {mode}")
        log(f"      intrare    : {self.p.entry_amount} {self.ccy} @ market-{self.p.entry_discount_pct}%")
        log(f"      DCA        : {self.p.dca_amount} {self.ccy} la -{self.p.dca_drop_pct}% (max {self.p.max_dca_buys})")
        log(f"      take-profit: +{self.p.takeprofit_pct}%" if self.p.enable_takeprofit else "      TP: off")
        log(f"      PLAFON     : {self.p.max_budget} {self.ccy} / ciclu")
        log(f"      ! fee HL ~{HL_FEE_PCT}%/leg (minuscul) -> TP={self.p.takeprofit_pct}% e ok")
        try:
            while True:
                price = get_price(self.client, self.coin)
                if price is None:
                    log("  [STRAT] pret indisponibil"); time.sleep(self.p.check_minutes*60); continue
                self.reconcile(price); self.step(price); self._save()
                avg = self._avg()
                pos = f"qty={self.s['qty']} avg={avg:.4f}" if avg else "qty=0 (astept intrare)"
                log(f"  [STRAT] pret={price:.4f}  {pos}  NET={self.s['realized_net']:+.2f} "
                    f"(brut {self.s['realized_gross']:+.2f}, fee {self.s['fees_total']:.2f}) {self.ccy}  ord={len(self.s['orders'])}")
                time.sleep(self.p.check_minutes*60)
        except KeyboardInterrupt:
            log("  [STRAT] oprit manual."); self._save()
