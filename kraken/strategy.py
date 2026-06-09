#!/usr/bin/env python3
"""
strategy.py — motor DCA + take-profit pe Kraken (Spot).

Aceeasi logica validata la 121trade (entry la market-%, DCA pe scadere,
take-profit la pret_mediu*(1+TP), reia ciclul), adaptata pentru Kraken:

  * Pret/sizing in valuta de cotare a perechii (HYPEEUR -> EUR, fara conversie FX).
  * Detectia executiei prin QueryOrders (merge si pt ordine inchise — Kraken NU
    da 404 ca T212). Costul/fee-ul real vin direct din ordinul inchis.
  * P&L NET cu fee-urile REALE raportate de Kraken (nu estimat).

ATENTIE la economie: fee Kraken spot ~0.26% taker / ~0.16% maker per tranzactie
=> ~0.3-0.5% pe round-trip. TAKEPROFIT_PCT trebuie sa bata pragul asta + spread.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from common import log, now_str, float_env
from notify import notify
from kraken_client import KrakenClient, KrakenError
from market_data import get_price, pair_precision

_HERE = os.path.dirname(os.path.abspath(__file__))


def state_path_for(pair: str) -> str:
    safe = "".join(c for c in pair if c.isalnum() or c in "._-")
    return os.path.join(_HERE, f".state_{safe}.json")


@dataclass
class StratParams:
    currency: str          # valuta de cotare (EUR/USD) — doar pt afisare
    entry_amount: float    # marimea intrarii in valuta de cotare
    entry_discount_pct: float
    dca_amount: float
    dca_drop_pct: float
    check_minutes: float
    takeprofit_pct: float
    max_budget: float
    max_dca_buys: int
    enable_takeprofit: bool
    order_ttl_min: float
    stop_loss_pct: float     # SIGURANTA: vinde tot daca pierderea >= acest % (0 = oprit)

    @classmethod
    def from_env(cls) -> "StratParams":
        mode = os.environ.get("STRATEGY_MODE", "avg_tp").strip().lower()
        return cls(
            currency           = os.environ.get("STRAT_CURRENCY", "EUR").strip().upper(),
            entry_amount       = float_env("STRAT_ENTRY") or 50.0,
            entry_discount_pct = float_env("STRAT_ENTRY_DISCOUNT_PCT") or 0.2,
            dca_amount         = float_env("STRAT_DCA") or 30.0,
            dca_drop_pct       = float_env("STRAT_DCA_DROP_PCT") or 2.0,
            check_minutes      = float_env("STRAT_CHECK_MINUTES") or 2.0,
            takeprofit_pct     = float_env("STRAT_TAKEPROFIT_PCT") or 1.0,
            max_budget         = float_env("STRAT_MAX_BUDGET") or 500.0,
            max_dca_buys       = int(float_env("STRAT_MAX_DCA_BUYS") or 10),
            enable_takeprofit  = (mode != "dca_only"),
            order_ttl_min      = float_env("STRAT_ORDER_TTL_MIN") or 10.0,
            stop_loss_pct      = float_env("STRAT_STOP_LOSS_PCT") or 0.0,
        )


def _new_state() -> dict:
    return {
        "cycle": 1,
        "qty": 0.0,
        "cost": 0.0,            # baza de cost in valuta de cotare
        "spent": 0.0,           # desfasurat in ciclul curent (plafon)
        "dca_buys": 0,
        "entry_price": None,
        "last_buy_price": None,
        "cycle_fees": 0.0,      # fee real acumulat in ciclul curent
        "realized_gross": 0.0,
        "realized_net": 0.0,
        "fees_total": 0.0,
        "orders": [],           # {txid, side, vol, price, amount, kind, ts}
    }


class Strategy:
    def __init__(self, client: KrakenClient, pair: str, params: StratParams,
                 dry_run: bool = True, desktop: bool = False):
        self.client = client
        self.pair = pair
        self.p = params
        self.ccy = params.currency
        self.dry_run = dry_run
        self.desktop = desktop
        self.state_file = state_path_for(pair)
        self.s = self._load()
        self._paper_seq = 0
        # precizie pereche
        self.price_dec, self.vol_dec, self.ordermin = 5, 8, 0.0
        try:
            info = client.pair_info(pair)
            if info:
                self.price_dec, self.vol_dec, self.ordermin = pair_precision(info)
        except KrakenError:
            log("  ! nu pot citi precizia perechii — folosesc valori implicite")

    # -- persistenta -----------------------------------------------------------
    def _load(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    st = json.load(f)
                merged = _new_state()
                merged.update(st)
                log(f"  [STRAT] stare incarcata (ciclu {merged.get('cycle')}, qty {merged.get('qty')})")
                return merged
            except (OSError, ValueError) as e:
                log(f"  ! [STRAT] nu pot citi starea ({e}), pornesc curat")
        return _new_state()

    def _save(self) -> None:
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.s, f, indent=2)
        except OSError as e:
            log(f"  ! [STRAT] nu pot salva starea: {e}")

    # -- helperi ---------------------------------------------------------------
    def _avg(self) -> float | None:
        return self.s["cost"] / self.s["qty"] if self.s["qty"] > 1e-12 else None

    def _qty_for(self, amount: float, price: float) -> float:
        return round(amount / price, self.vol_dec) if price > 0 else 0.0

    def _has_open(self, side: str) -> bool:
        return any(o["side"] == side for o in self.s["orders"])

    def _find_open(self, side: str) -> dict | None:
        return next((o for o in self.s["orders"] if o["side"] == side), None)

    def _remove(self, o: dict) -> None:
        if o in self.s["orders"]:
            self.s["orders"].remove(o)

    # -- plasare ---------------------------------------------------------------
    def _place(self, side: str, vol: float, price: float, kind: str, amount: float = 0.0) -> None:
        vol = round(vol, self.vol_dec)
        price = round(price, self.price_dec)
        if vol <= 0 or (self.ordermin and vol < self.ordermin):
            log(f"  ! [STRAT] volum {vol} < ordin minim {self.ordermin} — sar")
            return
        if self.dry_run:
            self._paper_seq += 1
            log(f"  [STRAT] [PAPER] {side.upper()} {kind} {vol} @ {price} {self.ccy}")
            self.s["orders"].append({"txid": f"PAPER-{self._paper_seq}", "side": side,
                                     "vol": vol, "price": price, "amount": amount,
                                     "kind": kind, "ts": time.time()})
            return
        try:
            res = self.client.add_order(self.pair, side, vol, price, ordertype="limit")
            txid = (res.get("txid") or ["?"])[0]
            log(f"  [STRAT] {side.upper()} {kind} plasat txid={txid} {vol} @ {price}")
            self.s["orders"].append({"txid": txid, "side": side, "vol": vol, "price": price,
                                     "amount": amount, "kind": kind, "ts": time.time()})
        except KrakenError as e:
            log(f"  ! [STRAT] {side} {kind} esuat: {e}")

    def _cancel_open(self, side: str) -> None:
        o = self._find_open(side)
        if not o:
            return
        if not self.dry_run and not str(o["txid"]).startswith("PAPER"):
            try:
                self.client.cancel_order(o["txid"])
            except KrakenError as e:
                log(f"  ! [STRAT] cancel esuat: {e}")
        self._remove(o)
        log(f"  [STRAT] anulat {side} {o['txid']}")

    # -- reconciliere ----------------------------------------------------------
    def reconcile(self, price: float) -> None:
        for side in ("buy", "sell"):
            for o in [x for x in self.s["orders"] if x["side"] == side]:
                if o not in self.s["orders"]:
                    continue
                if self.dry_run:
                    if side == "buy" or price >= o["price"]:
                        self._remove(o)
                        self._apply_fill(o, o["vol"], o["price"], fee=0.0)
                    continue
                # REAL: QueryOrders merge si pt ordine inchise (fara 404)
                try:
                    info = self.client.query_orders(o["txid"]).get(o["txid"], {})
                except KrakenError:
                    continue
                st = info.get("status")
                if st == "closed":
                    vol = float(info.get("vol_exec") or o["vol"])
                    cost = float(info.get("cost") or vol * o["price"])
                    fee = float(info.get("fee") or 0.0)
                    fp = (cost / vol) if vol else o["price"]
                    self._remove(o)
                    self._apply_fill(o, vol, fp, fee=fee)
                elif st in ("canceled", "expired"):
                    log(f"  [STRAT] {o['txid']} {st}")
                    self._remove(o)
                else:
                    age = (time.time() - o.get("ts", 0)) / 60
                    if side == "buy" and age > self.p.order_ttl_min and price > o["price"] * 1.003:
                        log(f"  [STRAT] buy {o['txid']} neexecutat, pret a urcat — anulez & reasez")
                        self._cancel_open("buy")

    def _apply_fill(self, o: dict, vol: float, price: float, fee: float) -> None:
        tag = "[PAPER] " if self.dry_run else ""
        self.s["cycle_fees"] += fee
        self.s["fees_total"] += fee
        if o["side"] == "buy":
            self.s["qty"] += vol
            self.s["cost"] += vol * price
            self.s["last_buy_price"] = price
            if self.s["entry_price"] is None:
                self.s["entry_price"] = price
            self.s["spent"] += o.get("amount", vol * price)
            if o.get("kind") == "DCA":
                self.s["dca_buys"] += 1
            avg = self._avg()
            log(f"  [STRAT] {tag}BUY FILLED {vol} @ {price} {self.ccy} ({o.get('kind')})  "
                f"qty={self.s['qty']:.8f} avg={avg:.{self.price_dec}f} fee={fee}")
            notify(title=f"{tag}{self.pair} BUY {vol} @ {price}",
                   body=(f"{o.get('kind')} fill  qty {self.s['qty']:.8f}  avg {avg}\n"
                         f"desfasurat {self.s['spent']:.2f} {self.ccy}\n{now_str()}"),
                   source="kraken", price=price, desktop=self.desktop)
            self._cancel_open("sell")
        else:  # sell
            avg = self._avg() or price
            gross = (price - avg) * vol
            net = gross - self.s["cycle_fees"]   # scade fee-urile reale ale ciclului
            self.s["realized_gross"] += gross
            self.s["realized_net"] += net
            self.s["qty"] -= vol
            log(f"  [STRAT] {tag}SELL FILLED {vol} @ {price} {self.ccy}  "
                f"brut={gross:+.4f} fee_ciclu={self.s['cycle_fees']:.4f} net={net:+.4f}")
            notify(title=f"{tag}{self.pair} SELL {vol} @ {price}  NET {net:+.2f} {self.ccy}",
                   body=(f"Brut {gross:+.4f} - fee {self.s['cycle_fees']:.4f} = NET {net:+.4f}\n"
                         f"Net total {self.s['realized_net']:+.4f} {self.ccy}\n{now_str()}"),
                   source="kraken", price=price, desktop=self.desktop)
            if self.s["qty"] <= 1e-12:
                keep = (self.s["realized_gross"], self.s["realized_net"],
                        self.s["fees_total"], self.s.get("cycle", 1) + 1)
                self.s = _new_state()
                (self.s["realized_gross"], self.s["realized_net"],
                 self.s["fees_total"], self.s["cycle"]) = keep
                log(f"  [STRAT] === ciclu inchis, reincep (ciclu {self.s['cycle']}) ===")

    # -- decizie ---------------------------------------------------------------
    def _check_stop_loss(self, price: float) -> bool:
        """Inchide TOT daca pierderea nerealizata depaseste pragul (anti-runaway DCA)."""
        if self.p.stop_loss_pct <= 0:
            return False
        avg = self._avg()
        if not avg:
            return False
        loss_pct = (avg - price) / avg * 100   # long: pierdem cand pretul < pret mediu
        if loss_pct >= self.p.stop_loss_pct:
            log(f"  🛑 [STRAT] STOP-LOSS: pierdere {loss_pct:.2f}% >= {self.p.stop_loss_pct}% — VAND TOT (taie pierderea)")
            for o in list(self.s["orders"]):           # anuleaza toate ordinele pendinte (si DCA-urile)
                if not self.dry_run and not str(o["txid"]).startswith("PAPER"):
                    try:
                        self.client.cancel_order(o["txid"])
                    except KrakenError:
                        pass
                self._remove(o)
            self._place("sell", self.s["qty"], round(price * 0.995, self.price_dec), kind="STOP")
            notify(title=f"🛑 STOP-LOSS {self.pair} ({loss_pct:.1f}%)",
                   body=f"Pierdere {loss_pct:.1f}% >= prag {self.p.stop_loss_pct}% — am vandut tot.\n{now_str()}",
                   source="kraken", price=price, desktop=self.desktop)
            return True
        return False

    def step(self, price: float) -> None:
        held = self.s["qty"]
        disc = 1 - self.p.entry_discount_pct / 100

        if held <= 1e-12:
            if self._has_open("buy"):
                return
            if self.s["spent"] + self.p.entry_amount > self.p.max_budget:
                log(f"  [STRAT] plafon {self.p.max_budget} {self.ccy} atins — nu intru")
                return
            self._place("buy", self._qty_for(self.p.entry_amount, price * disc),
                        price * disc, kind="ENTRY", amount=self.p.entry_amount)
            return

        # STOP-LOSS: taie pierderea inainte de DCA/TP
        if self._check_stop_loss(price):
            return

        avg = self._avg()
        if self.p.enable_takeprofit and avg:
            target = avg * (1 + self.p.takeprofit_pct / 100)
            sell = self._find_open("sell")
            if sell is None:
                self._place("sell", held, target, kind="TP")
            elif abs(sell["price"] - target) / target > 0.001 or abs(sell["vol"] - held) > 1e-9:
                self._cancel_open("sell")
                self._place("sell", held, target, kind="TP")

        if (self.s["dca_buys"] < self.p.max_dca_buys
                and self.s["last_buy_price"]
                and price <= self.s["last_buy_price"] * (1 - self.p.dca_drop_pct / 100)
                and self.s["spent"] + self.p.dca_amount <= self.p.max_budget
                and not self._has_open("buy")):
            log(f"  [STRAT] dip {price} <= {self.s['last_buy_price']}×(1-{self.p.dca_drop_pct}%) — DCA")
            self._place("buy", self._qty_for(self.p.dca_amount, price * disc),
                        price * disc, kind="DCA", amount=self.p.dca_amount)

    # -- bucla -----------------------------------------------------------------
    def run(self) -> None:
        mode = "avg_tp" if self.p.enable_takeprofit else "dca_only"
        log("  === STRATEGIE KRAKEN PORNITA ===")
        log(f"      pereche    : {self.pair}   {'[PAPER]' if self.dry_run else '⚠ REAL — BANI'}")
        log(f"      mod        : {mode}")
        log(f"      intrare    : {self.p.entry_amount} {self.ccy} @ market-{self.p.entry_discount_pct}%")
        log(f"      DCA        : {self.p.dca_amount} {self.ccy} la -{self.p.dca_drop_pct}% (max {self.p.max_dca_buys})")
        log(f"      take-profit: +{self.p.takeprofit_pct}%" if self.p.enable_takeprofit else "      take-profit: off")
        log(f"      PLAFON     : {self.p.max_budget} {self.ccy} / ciclu")
        log(f"      ! fee Kraken ~0.26% taker / ~0.16% maker per leg; TP={self.p.takeprofit_pct}%")
        try:
            while True:
                price = get_price(self.client, self.pair)
                if price is None:
                    log("  [STRAT] pret indisponibil — reincerc")
                    time.sleep(self.p.check_minutes * 60)
                    continue
                self.reconcile(price)
                self.step(price)
                self._save()
                avg = self._avg()
                pos = f"qty={self.s['qty']:.8f} avg={avg:.{self.price_dec}f}" if avg else "qty=0 (astept intrare)"
                log(f"  [STRAT] pret={price}  {pos}  "
                    f"NET={self.s['realized_net']:+.2f} (brut {self.s['realized_gross']:+.2f}, "
                    f"fee {self.s['fees_total']:.2f}) {self.ccy}  ord={len(self.s['orders'])}")
                time.sleep(self.p.check_minutes * 60)
        except KeyboardInterrupt:
            log("  [STRAT] oprit manual.")
            self._save()
