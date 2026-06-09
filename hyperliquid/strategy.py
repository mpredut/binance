#!/usr/bin/env python3
"""
strategy.py — motor DCA + take-profit pe Hyperliquid (PERP), LONG sau SHORT.

Generalizat pe directie (HL_DIRECTION = long | short):
  * LONG : intra cumparand sub piata; DCA cand pretul SCADE; TP cand pretul URCA.
  * SHORT: intra vanzand peste piata; DCA cand pretul URCA;  TP cand pretul SCADE.

Convensie cu semn: sign = +1 (long) / -1 (short).
  open_px  = price * (1 - sign * discount)       (intrare/DCA)
  tp_px    = avg   * (1 + sign * takeprofit)      (inchidere, reduce-only)
  DCA cand : sign * (price - last_open) <= -drop  (pretul a mers CONTRA noua)
  profit   = sign * (price - avg) * qty

Pozitia + pretul mediu vin din clearinghouseState (szi semnat, entryPx).
Fee HL minuscul -> TP poate fi strans. Levier din HL_LEVERAGE (1 = cvasi-spot).
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
HL_FEE_PCT = float_env("HL_FEE_PCT") or 0.035


def state_path_for(coin: str, direction: str) -> str:
    safe = "".join(c for c in coin if c.isalnum() or c in "._-")
    return os.path.join(_HERE, f".state_{safe}_{direction}.json")


@dataclass
class StratParams:
    currency: str
    direction: str            # "long" | "short"
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
        direction = os.environ.get("HL_DIRECTION", "long").strip().lower()
        if direction not in ("long", "short"):
            direction = "long"
        return cls(
            currency           = os.environ.get("STRAT_CURRENCY", "USDC").strip().upper(),
            direction          = direction,
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


def _new_state() -> dict:
    return {
        "cycle": 1, "qty": 0.0, "cost": 0.0, "spent": 0.0, "dca_buys": 0,
        "entry_price": None, "last_open_price": None,
        "realized_gross": 0.0, "realized_net": 0.0, "fees_total": 0.0,
        "orders": [],   # {oid, role(open/close), side, sz, px, amount, kind, ts}
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
        self.sign = 1 if params.direction == "long" else -1
        self.open_side = "buy" if self.sign > 0 else "sell"     # deschide/mareste pozitia
        self.close_side = "sell" if self.sign > 0 else "buy"    # reduce (reduce-only)
        self.state_file = state_path_for(coin, params.direction)
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

    def _open_pending(self) -> bool:
        return any(o["role"] == "open" for o in self.s["orders"])

    def _close_order(self) -> dict | None:
        return next((o for o in self.s["orders"] if o["role"] == "close"), None)

    def _remove(self, o: dict) -> None:
        if o in self.s["orders"]:
            self.s["orders"].remove(o)

    # -- plasare ---------------------------------------------------------------
    def _place(self, role: str, sz: float, px: float, kind: str, amount: float = 0.0) -> None:
        sz = round(sz, self.sz_dec)
        if sz <= 0:
            log("  ! [STRAT] sz 0 — sar"); return
        side = self.open_side if role == "open" else self.close_side
        reduce_only = (role == "close")
        if self.dry_run:
            self._paper_seq += 1
            log(f"  [STRAT] [PAPER] {role.upper()}({side}) {kind} {sz} @ {px:.4f} {self.ccy}")
            self.s["orders"].append({"oid": f"PAPER-{self._paper_seq}", "role": role, "side": side,
                                     "sz": sz, "px": px, "amount": amount, "kind": kind, "ts": time.time()})
            return
        ok, oid, msg = self.client.place_limit(self.coin, side == "buy", sz, px, reduce_only=reduce_only)
        if ok:
            log(f"  [STRAT] {role.upper()}({side}) {kind} plasat oid={oid} {sz} @ {px:.4f} ({msg})")
            self.s["orders"].append({"oid": oid, "role": role, "side": side, "sz": sz, "px": px,
                                     "amount": amount, "kind": kind, "ts": time.time()})
        else:
            log(f"  ! [STRAT] {role} {kind} esuat: {msg}")

    def _cancel_close(self) -> None:
        o = self._close_order()
        if not o:
            return
        if not self.dry_run and not str(o["oid"]).startswith("PAPER"):
            self.client.cancel(self.coin, o["oid"])
        self._remove(o)
        log(f"  [STRAT] anulat close {o['oid']}")

    # -- reconciliere ----------------------------------------------------------
    def reconcile(self, price: float) -> None:
        if self.dry_run:
            self._reconcile_paper(price)
        else:
            self._reconcile_real(price)

    def _reconcile_paper(self, price: float) -> None:
        for role in ("open", "close"):
            for o in [x for x in self.s["orders"] if x["role"] == role]:
                if o not in self.s["orders"]:
                    continue
                # open: umple instant; close: umple cand pretul a atins targetul (cu semn)
                if role == "open" or self.sign * (price - o["px"]) >= 0:
                    self._remove(o)
                    if role == "open":
                        self._apply_open(o["sz"], o["px"], o.get("amount", 0.0), o.get("kind"))
                    else:
                        self._apply_close(self._avg() or o["px"], o["px"], o["sz"])

    def _reconcile_real(self, price: float) -> None:
        try:
            szi, entry = self.client.position(self.coin)
        except HLError as e:
            log(f"  [STRAT] pozitie indisponibila ({e})"); return
        qty_now = abs(szi)
        active = {o.get("oid") for o in self.client.open_orders(self.coin)}
        prev = self.s["qty"]

        if qty_now > prev + 1e-9:        # s-a deschis/marit pozitia
            fp = entry if entry > 0 else price
            self._apply_open(qty_now - prev, fp, round((qty_now - prev) * fp, 2), None,
                             real_qty=qty_now, real_avg=entry)
        elif qty_now < prev - 1e-9:      # s-a redus (TP)
            self._apply_close(self._avg() or entry or price, price, prev - qty_now,
                              real_qty=qty_now, real_avg=entry)
        else:
            self.s["qty"] = qty_now
            if qty_now > 1e-12: self.s["cost"] = qty_now * entry

        for o in list(self.s["orders"]):
            if str(o["oid"]).startswith("PAPER"): continue
            if o["oid"] not in active:
                self._remove(o)
            elif o["role"] == "open" and (time.time()-o.get("ts",0))/60 > self.p.order_ttl_min \
                    and self.sign*(price - o["px"]) > 0.003*o["px"]:
                log(f"  [STRAT] open {o['oid']} neexecutat, pret a fugit — anulez & reasez")
                self.client.cancel(self.coin, o["oid"]); self._remove(o)

    def _apply_open(self, fq, fp, amount, kind, real_qty=None, real_avg=None):
        is_dca = self.s["qty"] > 1e-9
        self.s["last_open_price"] = fp
        if self.s["entry_price"] is None: self.s["entry_price"] = fp
        if is_dca: self.s["dca_buys"] += 1
        if real_qty is not None:
            self.s["qty"] = real_qty; self.s["cost"] = real_qty * real_avg
            self.s["spent"] = round(real_qty * real_avg, 2)
        else:
            self.s["qty"] += fq; self.s["cost"] += fq * fp
            self.s["spent"] += amount or fq * fp
        avg = self._avg()
        tag = "[PAPER] " if self.dry_run else ""
        log(f"  [STRAT] {tag}OPEN {self.p.direction.upper()} {fq:.6f} @ {fp:.4f} "
            f"({'DCA' if is_dca else 'ENTRY'}) qty={self.s['qty']:.6f} avg={avg:.4f}")
        notify(title=f"{tag}{self.coin} OPEN {self.p.direction} {fq:.6f} @ {fp:.4f}",
               body=f"{'DCA' if is_dca else 'ENTRY'} qty {self.s['qty']:.6f} avg {avg:.4f}\n{now_str()}",
               source="hyperliquid", price=fp, desktop=self.desktop)
        self._cancel_close()   # avg schimbat -> reasezam TP

    def _apply_close(self, avg, price, sz, real_qty=None, real_avg=None):
        gross = self.sign * (price - avg) * sz
        fee = (HL_FEE_PCT/100.0) * (avg*sz + price*sz)
        net = gross - fee
        self.s["realized_gross"] += gross; self.s["realized_net"] += net; self.s["fees_total"] += fee
        tag = "[PAPER] " if self.dry_run else ""
        log(f"  [STRAT] {tag}CLOSE {sz:.6f} @ {price:.4f}  brut={gross:+.4f} fee={fee:.4f} net={net:+.4f}")
        notify(title=f"{tag}{self.coin} CLOSE {sz:.6f} @ {price:.4f}  NET {net:+.2f}",
               body=f"Brut {gross:+.4f} - fee {fee:.4f} = NET {net:+.4f}\nNet total {self.s['realized_net']:+.4f}\n{now_str()}",
               source="hyperliquid", price=price, desktop=self.desktop)
        if real_qty is not None:
            self.s["qty"] = real_qty; self.s["cost"] = real_qty * (real_avg or 0)
        else:
            self.s["qty"] -= sz
        if self.s["qty"] <= 1e-12:
            keep = (self.s["realized_gross"], self.s["realized_net"], self.s["fees_total"], self.s.get("cycle",1)+1)
            self.s = _new_state()
            (self.s["realized_gross"], self.s["realized_net"], self.s["fees_total"], self.s["cycle"]) = keep
            log(f"  [STRAT] === ciclu inchis, reincep (ciclu {self.s['cycle']}) ===")

    # -- decizie ---------------------------------------------------------------
    def step(self, price: float) -> None:
        held = self.s["qty"]
        d = self.p.entry_discount_pct / 100
        if held <= 1e-12:
            if self._open_pending(): return
            if self.s["spent"] + self.p.entry_amount > self.p.max_budget:
                log(f"  [STRAT] plafon {self.p.max_budget} {self.ccy} atins"); return
            px = price * (1 - self.sign * d)
            self._place("open", self._sz_for(self.p.entry_amount, px), px, "ENTRY", self.p.entry_amount)
            return
        avg = self._avg()
        if self.p.enable_takeprofit and avg:
            target = avg * (1 + self.sign * self.p.takeprofit_pct/100)
            o = self._close_order()
            if o is None:
                self._place("close", held, target, "TP")
            elif abs(o["px"]-target)/target > 0.001 or abs(o["sz"]-held) > 1e-9:
                self._cancel_close(); self._place("close", held, target, "TP")
        # DCA: pretul a mers CONTRA pozitiei cu drop%
        moved = self.sign * (price - self.s["last_open_price"]) / self.s["last_open_price"] if self.s["last_open_price"] else 0
        if (self.s["dca_buys"] < self.p.max_dca_buys and self.s["last_open_price"]
                and moved <= -self.p.dca_drop_pct/100
                and self.s["spent"] + self.p.dca_amount <= self.p.max_budget
                and not self._open_pending()):
            px = price * (1 - self.sign * d)
            log(f"  [STRAT] pret contra ({moved*100:.2f}%) — DCA")
            self._place("open", self._sz_for(self.p.dca_amount, px), px, "DCA", self.p.dca_amount)

    # -- bucla -----------------------------------------------------------------
    def run(self) -> None:
        mode = "avg_tp" if self.p.enable_takeprofit else "dca_only"
        if not self.dry_run and self.client.exchange:
            self.client.set_leverage(self.coin, self.leverage)
        log("  === STRATEGIE HYPERLIQUID PORNITA ===")
        log(f"      coin       : {self.coin} perp  DIRECTIE={self.p.direction.upper()} levier {self.leverage}x  {'[PAPER]' if self.dry_run else '⚠ REAL'}")
        log(f"      mod        : {mode}")
        log(f"      intrare    : {self.p.entry_amount} {self.ccy} @ market{'-' if self.sign>0 else '+'}{self.p.entry_discount_pct}%")
        log(f"      DCA        : {self.p.dca_amount} {self.ccy} la {self.p.dca_drop_pct}% contra (max {self.p.max_dca_buys})")
        log(f"      take-profit: {self.p.takeprofit_pct}% in favoare" if self.p.enable_takeprofit else "      TP: off")
        log(f"      PLAFON     : {self.p.max_budget} {self.ccy} / ciclu  |  fee HL ~{HL_FEE_PCT}%/leg")
        try:
            while True:
                price = get_price(self.client, self.coin)
                if price is None:
                    log("  [STRAT] pret indisponibil"); time.sleep(self.p.check_minutes*60); continue
                self.reconcile(price); self.step(price); self._save()
                avg = self._avg()
                pos = f"qty={self.s['qty']:.6f} avg={avg:.4f}" if avg else "qty=0 (astept intrare)"
                log(f"  [STRAT] pret={price:.4f} [{self.p.direction}]  {pos}  "
                    f"NET={self.s['realized_net']:+.2f} (brut {self.s['realized_gross']:+.2f}, fee {self.s['fees_total']:.2f}) {self.ccy}  ord={len(self.s['orders'])}")
                time.sleep(self.p.check_minutes*60)
        except KeyboardInterrupt:
            log("  [STRAT] oprit manual."); self._save()
