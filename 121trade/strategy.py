#!/usr/bin/env python3
"""
strategy.py — motor DCA + take-profit, generic pe orice instrument si valuta.

Logica (mod "avg_tp"):
  * INTRARE: cumpara STRAT_ENTRY (in STRAT_CURRENCY) la LIMIT = market - STRAT_ENTRY_DISCOUNT_PCT.
  * DCA: la fiecare scadere de STRAT_DCA_DROP_PCT fata de ultima cumparare,
         mai cumpara STRAT_DCA (scade pretul mediu).
  * TAKE-PROFIT: vinde TOATA pozitia cand pretul >= pret_mediu * (1 + STRAT_TAKEPROFIT_PCT),
         apoi reia ciclul.
  * Mod "dca_only": la fel, dar fara vanzare (doar acumulare).

Siguranta:
  * PAPER cand dry_run=True — logheaza, nu tranzactioneaza.
  * Plafon STRAT_MAX_BUDGET (in valuta) pe ciclu + STRAT_MAX_DCA_BUYS cumparari maxime.
  * Stare persistata per-instrument (.state_<TICKER>.json) — supravietuieste restartului.

Cost real (T212): comision 0; 0.15% conversie valutara la fiecare buy si sell
(~0.30% pe round-trip). TAKEPROFIT_PCT trebuie sa bata 0.30% + spread.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from ipo_common import log, now_str, float_env
from ipo_notify import notify
from market_data import get_eur_usd, get_usd_ron, get_price_usd, t212_to_yahoo
from t212_client import T212Client

FX_FEE_PCT = 0.15  # taxa conversie valutara T212, per directie
_HERE = os.path.dirname(os.path.abspath(__file__))


def state_path_for(ticker: str) -> str:
    safe = "".join(c for c in ticker if c.isalnum() or c in "._-")
    return os.path.join(_HERE, f".state_{safe}.json")


@dataclass
class StratParams:
    currency: str            # "RON" | "EUR" | "USD" — valuta sumelor de mai jos
    entry_amount: float      # marimea cumpararii initiale (in currency)
    entry_discount_pct: float
    dca_amount: float        # marimea unei cumparari pe scadere (in currency)
    dca_drop_pct: float
    check_minutes: float
    takeprofit_pct: float
    max_budget: float        # plafon total/ciclu (in currency)
    max_dca_buys: int
    validity: str
    enable_takeprofit: bool
    order_ttl_min: float
    stop_loss_pct: float     # SIGURANTA: vinde tot daca pierderea >= acest % (0 = oprit)

    @classmethod
    def from_env(cls) -> "StratParams":
        mode = os.environ.get("STRATEGY_MODE", "avg_tp").strip().lower()
        return cls(
            currency           = os.environ.get("STRAT_CURRENCY", "RON").strip().upper(),
            entry_amount       = float_env("STRAT_ENTRY") or 300.0,
            entry_discount_pct = float_env("STRAT_ENTRY_DISCOUNT_PCT") or 0.2,
            dca_amount         = float_env("STRAT_DCA") or 150.0,
            dca_drop_pct       = float_env("STRAT_DCA_DROP_PCT") or 2.0,
            check_minutes      = float_env("STRAT_CHECK_MINUTES") or 5.0,
            takeprofit_pct     = float_env("STRAT_TAKEPROFIT_PCT") or 1.5,
            max_budget         = float_env("STRAT_MAX_BUDGET") or 2000.0,
            max_dca_buys       = int(float_env("STRAT_MAX_DCA_BUYS") or 10),
            validity           = "GOOD_TILL_CANCEL",
            enable_takeprofit  = (mode != "dca_only"),
            order_ttl_min      = float_env("STRAT_ORDER_TTL_MIN") or 10.0,
            stop_loss_pct      = float_env("STRAT_STOP_LOSS_PCT") or 0.0,
        )


def _new_state() -> dict:
    return {
        "cycle": 1,
        "qty": 0.0,
        "cost_usd": 0.0,        # baza de cost in USD a cantitatii detinute
        "spent_cash": 0.0,      # suma desfasurata in ciclul curent (in valuta), pt plafon
        "dca_buys": 0,
        "entry_price": None,
        "last_buy_price": None,
        "realized_pnl_usd": 0.0,   # profit BRUT cumulat (fara fee)
        "realized_net_usd": 0.0,   # profit NET cumulat (dupa taxa FX 0.15% x2)
        "fees_usd": 0.0,           # total taxe FX platite
        "orders": [],           # {id, side, qty, limit, amount, kind, ts}
    }


def _sell_pnl(avg: float, price: float, qty: float) -> tuple[float, float, float]:
    """Returneaza (brut, fee_fx, net) pentru o vanzare de `qty` la `price`, cost mediu `avg`.

    Taxa FX 0.15% se aplica si pe valoarea cumparata (baza de cost), si pe cea vanduta.
    """
    gross = (price - avg) * qty
    fee = (FX_FEE_PCT / 100.0) * (avg * qty + price * qty)
    return gross, fee, gross - fee


class Strategy:
    def __init__(self, client: T212Client, ticker: str, params: StratParams,
                 dry_run: bool = True, desktop: bool = False):
        self.client = client
        self.ticker = ticker
        self.yahoo_sym = os.environ.get("YAHOO_SYMBOL") or t212_to_yahoo(ticker)
        self.p = params
        self.dry_run = dry_run
        self.desktop = desktop
        self.ccy = params.currency
        self.fx_to_usd = self._fx_to_usd(params.currency)
        self.state_file = state_path_for(ticker)
        self.s = self._load()
        self._paper_seq = 0

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
    def _load(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    st = json.load(f)
                # migrare: completeaza cheile noi lipsa (ex. net/fees din versiuni vechi)
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
            notify(title=f"{tag}{self.yahoo_sym} BUY {qty} @ {price:.2f}",
                   body=(f"{order.get('kind')} fill\nqty {self.s['qty']:.2f}  avg {avg:.2f} USD\n"
                         f"desfasurat {self.s['spent_cash']:.0f} {self.ccy}  "
                         f"DCA {self.s['dca_buys']}/{self.p.max_dca_buys}\n{now_str()}"),
                   source="strategy", price=price, desktop=self.desktop)
            self._cancel_open("SELL")     # avg s-a schimbat -> reasezam TP
        else:  # SELL
            avg = self._avg_cost() or price
            gross, fee, net = _sell_pnl(avg, price, qty)
            self.s["realized_pnl_usd"] += gross
            self.s["realized_net_usd"] += net
            self.s["fees_usd"] += fee
            self.s["qty"] -= qty
            log(f"  [STRAT] {tag}SELL FILLED {qty} @ {price:.2f} USD  "
                f"brut={gross:+.2f}  fee={fee:.2f}  net={net:+.2f} USD")
            notify(title=f"{tag}{self.yahoo_sym} SELL {qty} @ {price:.2f}  NET {net:+.2f} USD",
                   body=(f"Brut {gross:+.2f}  - fee FX {fee:.2f}  = NET {net:+.2f} USD\n"
                         f"Net total {self.s['realized_net_usd']:+.2f} USD\n"
                         f"Ciclu {self.s['cycle']} inchis.\n{now_str()}"),
                   source="strategy", price=price, desktop=self.desktop)
            if self.s["qty"] <= 1e-9:
                pnl, net_tot, fees = (self.s["realized_pnl_usd"],
                                      self.s["realized_net_usd"], self.s["fees_usd"])
                nxt = self.s.get("cycle", 1) + 1
                self.s = _new_state()
                self.s["realized_pnl_usd"] = pnl
                self.s["realized_net_usd"] = net_tot
                self.s["fees_usd"] = fees
                self.s["cycle"] = nxt
                self.s["last_sell_price"] = price   # regula de reintrare: nu recumpara mai sus
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
                                     "ts": time.time()})
            return
        status, data = self.client.place_limit_order(self.ticker, qty, round(limit, 2), self.p.validity)
        if status in (200, 201):
            log(f"  [STRAT] BUY {kind} plasat id={data.get('id')} {qty} @ {limit:.2f}")
            self.s["orders"].append({"id": data.get("id"), "side": "BUY", "qty": qty,
                                     "limit": round(limit, 2), "amount": amount, "kind": kind,
                                     "ts": time.time()})
        else:
            log(f"  ! [STRAT] BUY {kind} esuat HTTP {status}: {json.dumps(data)[:200]}")
            if "insufficient" in json.dumps(data).lower():
                # cont fara cash liber: nu mai spama la fiecare tick — pauza 30 min
                self.s["buy_backoff_until"] = time.time() + 1800
                log("  [STRAT] fonduri insuficiente — pauza cumparari 30 min (alimenteaza contul)")

    def _place_sell(self, qty: float, limit: float) -> None:
        if self.dry_run:
            self._paper_seq += 1
            log(f"  [STRAT] [PAPER] plasez SELL TP {qty:.2f} @ {limit:.2f} USD")
            self.s["orders"].append({"id": f"PAPER-{self._paper_seq}", "side": "SELL",
                                     "qty": round(qty, 2), "limit": round(limit, 2),
                                     "kind": "TP", "ts": time.time()})
            return
        status, data = self.client.place_limit_order(self.ticker, -abs(qty), round(limit, 2), self.p.validity)
        if status in (200, 201):
            log(f"  [STRAT] SELL TP plasat id={data.get('id')} {qty:.2f} @ {limit:.2f}")
            self.s["orders"].append({"id": data.get("id"), "side": "SELL", "qty": round(qty, 2),
                                     "limit": round(limit, 2), "kind": "TP", "ts": time.time()})
        else:
            log(f"  ! [STRAT] SELL TP esuat HTTP {status}: {json.dumps(data)[:200]}")

    def _cancel_open(self, side: str) -> None:
        o = self._find_open(side)
        if not o:
            return
        if not self.dry_run and not str(o["id"]).startswith("PAPER"):
            self.client.cancel_order(o["id"])
        self.s["orders"].remove(o)
        log(f"  [STRAT] anulat ordin {side} {o['id']}")

    # -- reconciliere ----------------------------------------------------------
    def _remove_order(self, o: dict) -> None:
        if o in self.s["orders"]:
            self.s["orders"].remove(o)

    def reconcile(self, price: float) -> None:
        if self.dry_run:
            self._reconcile_paper(price)
        else:
            self._reconcile_real(price)

    # -- reconciliere PAPER (simulare) -----------------------------------------
    def _reconcile_paper(self, price: float) -> None:
        # BUY: presupunem fill la limita; SELL: fill cand pretul atinge limita
        for side in ("BUY", "SELL"):
            for o in [x for x in self.s["orders"] if x["side"] == side]:
                if o not in self.s["orders"]:
                    continue
                if side == "BUY":
                    self._remove_order(o)
                    self._apply_fill(o, o["qty"], o["limit"])
                elif price >= o["limit"]:
                    self._remove_order(o)
                    self._apply_fill(o, o["qty"], o["limit"])

    # -- reconciliere REALA: portofoliul T212 e SURSA DE ADEVAR ----------------
    # (ordinele executate dispar din /equity/orders/{id} -> 404; pozitia apare
    #  in portofoliu. Deci ne uitam la portofoliu, nu la statusul ordinului.)
    def _portfolio_position(self) -> tuple[float, float] | None:
        pf = self.client.get_portfolio()
        if pf is None:
            return None
        for p in pf:
            if str(p.get("ticker", "")).upper() == self.ticker.upper():
                return float(p.get("quantity") or 0.0), float(p.get("averagePrice") or 0.0)
        return 0.0, 0.0   # nu detinem nimic

    def _active_order_ids(self) -> set | None:
        orders = self.client.list_active_orders()
        if orders is None:
            return None
        return {o.get("id") for o in orders
                if str(o.get("ticker", "")).upper() == self.ticker.upper()}

    def _reconcile_real(self, price: float) -> None:
        real = self._portfolio_position()
        if real is None:
            log("  [STRAT] portofoliu indisponibil — sar reconcilierea acest tick")
            return
        real_qty, real_avg = real
        active = self._active_order_ids()
        if active is None:
            active = {o["id"] for o in self.s["orders"]}   # nu putem lista -> nu curatam

        prev_qty = self.s["qty"]
        prev_avg = self._avg_cost() or real_avg

        # --- BUY executat: pozitia a crescut (sau adoptam o pozitie pre-existenta) ---
        if real_qty > prev_qty + 1e-6:
            fq = real_qty - prev_qty
            fp = ((real_avg * real_qty - prev_avg * prev_qty) / fq) if fq > 0 else real_avg
            is_dca = prev_qty > 1e-9
            self.s["last_buy_price"] = fp
            if self.s["entry_price"] is None:
                self.s["entry_price"] = fp
            if is_dca:
                self.s["dca_buys"] += 1
            self.s["qty"] = real_qty
            self.s["cost_usd"] = real_qty * real_avg
            self.s["spent_cash"] = round(real_qty * real_avg / self.fx_to_usd, 2)
            log(f"  [STRAT] BUY EXECUTAT {fq:.4f} @ {fp:.2f} USD "
                f"({'DCA' if is_dca else 'ENTRY'})  qty={real_qty:.4f} avg={real_avg:.2f}")
            notify(title=f"{self.yahoo_sym} BUY {fq:.4f} @ {fp:.2f}",
                   body=(f"{'DCA' if is_dca else 'ENTRY'} executat\nqty {real_qty:.4f}  "
                         f"avg {real_avg:.2f} USD\ndesfasurat {self.s['spent_cash']:.0f} {self.ccy}  "
                         f"DCA {self.s['dca_buys']}/{self.p.max_dca_buys}\n{now_str()}"),
                   source="strategy", price=fp, desktop=self.desktop)
            self._cancel_open("SELL")   # avg schimbat -> reasezam TP la pasul urmator

        # --- SELL executat: pozitia a scazut ---
        elif real_qty < prev_qty - 1e-6:
            sold = prev_qty - real_qty
            gross, fee, net = _sell_pnl(prev_avg, price, sold)
            self.s["realized_pnl_usd"] += gross
            self.s["realized_net_usd"] += net
            self.s["fees_usd"] += fee
            self.s["qty"] = real_qty
            self.s["cost_usd"] = real_qty * real_avg
            self.s["spent_cash"] = round(real_qty * real_avg / self.fx_to_usd, 2)
            log(f"  [STRAT] SELL EXECUTAT {sold:.4f} @ ~{price:.2f} USD  "
                f"brut={gross:+.2f}  fee={fee:.2f}  net={net:+.2f} USD")
            notify(title=f"{self.yahoo_sym} SELL {sold:.4f} @ ~{price:.2f}  NET {net:+.2f} USD",
                   body=(f"Brut {gross:+.2f}  - fee FX {fee:.2f}  = NET {net:+.2f} USD\n"
                         f"Net total {self.s['realized_net_usd']:+.2f} USD\n{now_str()}"),
                   source="strategy", price=price, desktop=self.desktop)

        else:
            # pozitie neschimbata -> sincronizam valorile cu realitatea
            self.s["qty"] = real_qty
            self.s["cost_usd"] = real_qty * real_avg
            if real_qty > 1e-9:
                self.s["spent_cash"] = round(real_qty * real_avg / self.fx_to_usd, 2)

        # --- curata ordinele care nu mai sunt active; TTL pe BUY-uri stale ---
        for o in list(self.s["orders"]):
            if str(o["id"]).startswith("PAPER"):
                continue
            if o["id"] not in active:
                self._remove_order(o)        # nu mai e pending (executat sau anulat)
            elif (o["side"] == "BUY"
                  and (time.time() - o.get("ts", 0)) / 60 > self.p.order_ttl_min
                  and price > o["limit"] * 1.003):
                log(f"  [STRAT] BUY {o['id']} neexecutat, pret a urcat — anulez & reasez")
                self.client.cancel_order(o["id"])
                self._remove_order(o)

        # --- ciclu inchis (am vandut tot) -> reincepe ---
        if real_qty <= 1e-9 and prev_qty > 1e-9:
            pnl, net_tot, fees = (self.s["realized_pnl_usd"],
                                  self.s["realized_net_usd"], self.s["fees_usd"])
            nxt = self.s.get("cycle", 1) + 1
            self.s = _new_state()
            self.s["realized_pnl_usd"] = pnl
            self.s["realized_net_usd"] = net_tot
            self.s["fees_usd"] = fees
            self.s["cycle"] = nxt
            log(f"  [STRAT] === ciclu inchis, reincep (ciclu {nxt}) ===")

    # -- pas de decizie --------------------------------------------------------
    def _check_stop_loss(self, price: float) -> bool:
        """Inchide TOT daca pierderea nerealizata depaseste pragul (anti-runaway DCA)."""
        if self.p.stop_loss_pct <= 0:
            return False
        avg = self._avg_cost()
        if not avg:
            return False
        loss_pct = (avg - price) / avg * 100   # long: pierdem cand pretul < pret mediu
        if loss_pct >= self.p.stop_loss_pct:
            log(f"  🛑 [STRAT] STOP-LOSS: pierdere {loss_pct:.2f}% >= {self.p.stop_loss_pct}% — VAND TOT (taie pierderea)")
            for o in list(self.s["orders"]):           # anuleaza toate ordinele pendinte (si DCA-urile)
                if not self.dry_run and not str(o["id"]).startswith("PAPER"):
                    self.client.cancel_order(o["id"])
                self._remove_order(o)
            self._place_sell(self.s["qty"], round(price * 0.995, 2))   # vinde agresiv -> fill sigur
            notify(title=f"🛑 STOP-LOSS {self.yahoo_sym} ({loss_pct:.1f}%)",
                   body=f"Pierdere {loss_pct:.1f}% >= prag {self.p.stop_loss_pct}% — am vandut tot.\n{now_str()}",
                   source="strategy", price=price, desktop=self.desktop)
            return True
        return False

    def step(self, price: float) -> None:
        held = self.s["qty"]
        disc = 1 - self.p.entry_discount_pct / 100

        # backoff dupa "insufficient funds": nu incerca cumparari cat contul e gol
        if time.time() < self.s.get("buy_backoff_until", 0):
            if self._check_stop_loss(price):
                return
            return

        if held <= 1e-9:
            if self._has_open("BUY"):
                return
            # REGULA DE REINTRARE (ca pe Kraken): dupa vanzare nu recumpara mai sus —
            # anti "vand la 174.17, recumpar la 174.9"
            lsp = self.s.get("last_sell_price")
            rdp = float_env("STRAT_REENTRY_DROP_PCT") or 0.0
            if rdp > 0 and lsp and price > lsp * (1 - rdp / 100):
                log(f"  [STRAT] reintrare blocata: {price:.2f} > prag {lsp * (1 - rdp / 100):.2f} "
                    f"(vandut la {lsp:.2f}, astept -{rdp}%)")
                return
            if self.s["spent_cash"] + self.p.entry_amount > self.p.max_budget:
                log(f"  [STRAT] plafon buget {self.p.max_budget:.0f} {self.ccy} atins — nu intru")
                return
            self._place_buy(self.p.entry_amount, price * disc, kind="ENTRY")
            return

        # STOP-LOSS: taie pierderea inainte de DCA/TP
        if self._check_stop_loss(price):
            return

        avg = self._avg_cost()

        if self.p.enable_takeprofit and avg:
            target = avg * (1 + self.p.takeprofit_pct / 100)
            sell = self._find_open("SELL")
            if sell is None:
                self._place_sell(held, target)
            elif abs(sell["limit"] - target) / target > 0.001 or abs(sell["qty"] - held) > 1e-6:
                self._cancel_open("SELL")
                self._place_sell(held, target)

        if (self.s["dca_buys"] < self.p.max_dca_buys
                and self.s["last_buy_price"]
                and price <= self.s["last_buy_price"] * (1 - self.p.dca_drop_pct / 100)
                and self.s["spent_cash"] + self.p.dca_amount <= self.p.max_budget
                and not self._has_open("BUY")):
            log(f"  [STRAT] dip: {price:.2f} <= {self.s['last_buy_price']:.2f}"
                f"×(1-{self.p.dca_drop_pct}%) — DCA")
            self._place_buy(self.p.dca_amount, price * disc, kind="DCA")

    # -- bucla -----------------------------------------------------------------
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
        log(f"      ! prag rentabilitate ~{FX_FEE_PCT*2:.2f}% (FX) + spread; TP={self.p.takeprofit_pct}%")

        try:
            while True:
                price = get_price_usd(self.yahoo_sym)
                if price is None:
                    log("  [STRAT] pret indisponibil — reincerc")
                    time.sleep(self.p.check_minutes * 60)
                    continue
                try:
                    self.reconcile(price)
                    self.step(price)
                    self._save()
                except Exception as e:  # noqa: BLE001 — REZILIENTA: net/API picat -> reincerc
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
