#!/usr/bin/env python3
"""
strategy.py — motor DCA + take-profit pe Kraken (Spot).

Aceeasi logica validata la 212trading (entry la market-%, DCA pe scadere,
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
import math
import os
import statistics
import time
from collections import deque
from dataclasses import dataclass

from kraken_common import log, now_str, float_env, are_close
from notify import notify
from kraken_client import KrakenClient, KrakenError
import strat_rules as sr   # reguli de decizie PARTAJATE cu backtest.py (aceleasi praguri)
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
    adopt_cost: float        # >0 = ADOPTA pozitia existenta din cont la acest cost mediu (ex. alocare IPO/xStock)
    adopt_qty: float         # cantitatea adoptata; 0 = citeste automat balanta activului de baza
    reentry_drop_pct: float  # dupa TP, reintra DOAR daca pretul scade cu acest % sub pretul vandut (0 = imediat)
    reentry_tolerance_pct: float  # "aproape de prag" conteaza ca atins: pret <= prag*(1+tol%) intra
                                  # (15 iul: HYPE a ricosat la 65.93 vs prag 65.91 — 2 centi — si a ratat intrarea)
    reentry_adaptive: bool   # 23 iul: prag de reintrare = K_REENTRY * vol_1h (nu procentul fix) —
                             # investigat in offline/research/kraken_adaptive_thresholds/: adaptivul bate
                             # fixul pe HYPEUSD (~30 zile, TOTAL +3.26% vs +2.20%), K=2.0 confirmat
                             # optim printr-un sweep dedicat (K=1.5 si K=2.5 dau amandoua mai putin).
                             # Fail-safe: cade pe reentry_drop_pct (fix) daca volatilitatea nu poate
                             # fi calculata inca (warm-up <20 puncte de pret).
    reentry_sl_bounce_pct: float  # dupa STOP-LOSS (NU dupa TP): reintra pe REVENIRE — cand pretul
                                  # urca cu acest % de la minimul atins dupa vanzare. Regula
                                  # veche (reintra doar SUB pretul vandut) e corecta dupa un TP
                                  # (ai vandut sus, astepti sa cumperi mai jos), dar dupa un
                                  # stop-loss lasa botul BLOCAT afara cand pretul isi revine
                                  # (4 aug: exact ce s-a intamplat — vandut 51.19, HYPE la 55.6,
                                  # prag reintrare 50.06 nu se mai atinge). 0 = dezactivat (regula veche).
    tp_tranches: list        # [(pct, cota%), ...] vanzare graduala; [] = TP clasic pe tot
    # --- TP trend-aware (EXPERIMENTAL, default OFF) -------------------------------
    tp_trend_hold: bool = False       # True = cat trendul scurt e UP, NU face TP (calaresc
                                      # trendul); ies aproape de piata cand trendul se intoarce.
                                      # Adreseaza "DCA+TP lasa mult pe masa in bull" (240m: +4% vs
                                      # buy&hold +48%). OFF = comportament clasic (TP fix).
    tp_trend_min_pct: float = 0.5     # (v1, nefolosit in v2) prag semnal trend din _shadow_prices.
    tp_trail_pct: float = 2.0         # v2: PESTE nivelul TP, ies la un pullback de acest %% de la
                                      # varf (trailing). Doar peste TP -> nu iesi niciodata in pierdere.
    # --- TREND OVERLAY (combina strategii pe regim; EXPERIMENTAL, default OFF) -----
    trend_overlay: bool = False       # True = in UPTREND confirmat, intra cu TOP-UP mare si
                                      # CALARESTE (hold+trailing) in loc de DCA/TP; in range = clasic.
    trend_sma_n: int = 30             # fereastra SMA (nr bare) pt semnalul de trend LUNG
    trend_interval: int = 240         # minute/bara pt semnalul de trend (live: OHLC Kraken;
                                      # backtest: barele fed-uite). 240=4h -> SMA(30)=~5 zile.
    trend_confirm_bars: int = 3       # bare consecutive de uptrend ca sa confirme (anti-fals)
    trend_topup: float = 2000.0       # cat cumpar la intrarea in trend (sume mai mari = prinde trendul)
    trend_trail_pct: float = 5.0      # trailing-ul pozitiei de trend (pullback de la varf la care ies)
    trend_exit_break: bool = False    # False=A (doar trailing); True=B (trailing SAU pret<SMA=trend rupt)
    # --- ADAPTIV pe VOLATILITATE (redesign overlay: MODULARE, nu amplificare; default OFF) ---
    tp_trail_adaptive: bool = False   # A: trailing-ul TP (tp_trail_pct) devine k×vol_1h — larg in
                                      # trend volatil (calaresc mai mult), strans in chop. Fail-safe
                                      # pe warm-up (fallback pe tp_trail_pct fix), ca reintrarea adaptiva.
    tp_trail_k: float = 2.0           # multiplicatorul vol_1h pt trailing adaptiv
    tp_trail_min: float = 1.5         # clamp jos (%) — nu iesi pe zgomot infim
    tp_trail_max: float = 8.0         # clamp sus (%) — nu lasa profitul sa scape complet
    dca_trend_brake: bool = False     # B: in DOWNTREND confirmat, NU face DCA (nu prinde cutitul care
                                      # cade) — ataca direct maxDD, overlay care REDUCE risc.
    dca_brake_min_pct: float = 1.5    # panta minima (%) recent/vechi ca sa considere downtrend

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
            # NU "or 10": zeroul explicit (= fara DCA, doar gestionare iesire) e valid
            max_dca_buys       = int(float_env("STRAT_MAX_DCA_BUYS")) if float_env("STRAT_MAX_DCA_BUYS") is not None else 10,
            enable_takeprofit  = (mode != "dca_only"),
            order_ttl_min      = float_env("STRAT_ORDER_TTL_MIN") or 10.0,
            stop_loss_pct      = float_env("STRAT_STOP_LOSS_PCT") or 0.0,
            adopt_cost         = float_env("STRAT_ADOPT_COST") or 0.0,
            adopt_qty          = float_env("STRAT_ADOPT_QTY") or 0.0,
            reentry_drop_pct   = float_env("STRAT_REENTRY_DROP_PCT") or 0.0,
            reentry_tolerance_pct = float_env("STRAT_REENTRY_TOLERANCE_PCT") or 0.0,
            reentry_adaptive   = os.environ.get("STRAT_REENTRY_ADAPTIVE", "false").strip().lower() == "true",
            reentry_sl_bounce_pct = float_env("STRAT_REENTRY_SL_BOUNCE_PCT") or 1.5,
            tp_tranches        = _parse_tranches(os.environ.get("STRAT_TP_TRANCHES", "")),
            tp_trend_hold      = os.environ.get("STRAT_TP_TREND_HOLD", "false").strip().lower() == "true",
            tp_trend_min_pct   = float_env("STRAT_TP_TREND_MIN_PCT") or 0.5,
            tp_trail_pct       = float_env("STRAT_TP_TRAIL_PCT") or 2.0,
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
            dca_trend_brake    = os.environ.get("STRAT_DCA_TREND_BRAKE", "false").strip().lower() == "true",
            dca_brake_min_pct  = float_env("STRAT_DCA_BRAKE_MIN_PCT") or 1.5,
        )


def _parse_tranches(spec: str) -> list:
    """"3:50,6:50" -> [(3.0, 50.0), (6.0, 50.0)]; cotele trebuie sa dea 100."""
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
        "cost": 0.0,            # baza de cost in valuta de cotare
        "spent": 0.0,           # desfasurat in ciclul curent (plafon)
        "dca_buys": 0,
        "entry_price": None,
        "last_buy_price": None,
        "cycle_fees": 0.0,      # fee real acumulat in ciclul curent
        "realized_gross": 0.0,
        "realized_net": 0.0,
        "fees_total": 0.0,
        "last_sell_price": None,  # pretul ultimei vanzari (regula de reintrare)
        "last_exit_kind": None,   # "TP"/"STOP"/... — cum s-a inchis ultimul ciclu (reintrare STOP-aware)
        "sl_low": None,           # minimul de pret dupa un stop-loss (pt reintrarea pe revenire)
        "trail_peak": None,       # varful urmarit dupa ce pretul a depasit nivelul TP
        "trend_mode": False,      # overlay: suntem intr-o pozitie de trend (hold+trailing)?
        "trend_peak": None,       # varful urmarit in modul trend
        "trend_confirm_count": 0, # bare consecutive de uptrend (confirmare semnal)
        "orders": [],           # {txid, side, vol, price, amount, kind, ts}
    }


class Strategy:
    def __init__(self, client: KrakenClient, pair: str, params: StratParams,
                 dry_run: bool = True, desktop: bool = False,
                 initial_state: dict | None = None,
                 replay_mode: bool = False):
        self.client = client
        self.pair = pair
        self.p = params
        self.ccy = params.currency
        self.dry_run = dry_run
        # dry_run înseamnă și paper-live, nu doar backtest. Replay-ul trebuie
        # identificat separat ca semnalul de trend să folosească barele injectate.
        self.replay_mode = replay_mode
        self.desktop = desktop
        self.state_file = state_path_for(pair)
        self.s = initial_state if initial_state is not None else self._load()
        self._paper_seq = 0
        # SHADOW vol-adaptiv (observational, plan 17 iul): istoric mic de pret in
        # memorie (tick ~2min -> ~3h) pentru sigma; NU intra in state-file, se
        # reconstruieste dupa restart (warm-up ~40min pana la >=20 puncte).
        self._shadow_prices = deque(maxlen=90)
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

    def _dust_safe_qty(self, qty: float) -> float:
        """Kraken raporteaza balanta ROTUNJITA: vinderea intregii cantitati
        poate depasi ledger-ul cu o zecimila -> 'EOrder:Insufficient funds',
        la nesfarsit (bot-ul reincearca aceeasi cantitate in fiecare ciclu,
        fara sa se corecteze singur — 21 iul, gasit ca sursa a 212 esecuri
        repetate 'sell TP esuat' pe HYPEUSD). Lasam un praf: o unitate la
        penultima zecimala (ex. 5.1715916 -> 5.1715914, ~$0.00001 dust) —
        cost neglijabil, elimina blocarea permanenta la TP/stop-loss.
        Acelasi calcul ca la _maybe_adopt (care avea deja fixul, dar doar
        pt pozitii adoptate, nu si pt cele acumulate prin fill-uri proprii)."""
        step = 10.0 ** -(max(self.vol_dec - 1, 1))
        return round(int((qty - step) / step) * step, self.vol_dec)

    def _has_open(self, side: str) -> bool:
        return any(o["side"] == side for o in self.s["orders"])

    def _find_open(self, side: str) -> dict | None:
        return next((o for o in self.s["orders"] if o["side"] == side), None)

    def _remove(self, o: dict) -> None:
        if o in self.s["orders"]:
            self.s["orders"].remove(o)

    # -- plasare ---------------------------------------------------------------
    def _place(self, side: str, vol: float, price: float, kind: str, amount: float = 0.0,
               market: bool = False) -> None:
        # market=True: iesire de piata (trailing/stop) — se executa imediat, NU ordin limita
        # care poate rata o cadere brusca. In backtest se umple la open-ul barei urmatoare.
        vol = round(vol, self.vol_dec)
        price = round(price, self.price_dec)
        if vol <= 0 or (self.ordermin and vol < self.ordermin):
            log(f"  ! [STRAT] volum {vol} < ordin minim {self.ordermin} — sar")
            return
        if self.dry_run:
            self._paper_seq += 1
            log(f"  [STRAT] [PAPER] {side.upper()} {kind}{' MKT' if market else ''} {vol} @ {price} {self.ccy}")
            self.s["orders"].append({"txid": f"PAPER-{self._paper_seq}", "side": side,
                                     "vol": vol, "price": price, "amount": amount,
                                     "kind": kind, "market": market, "ts": time.time()})
            return
        try:
            res = self.client.add_order(self.pair, side, vol, None if market else price,
                                        ordertype="market" if market else "limit")
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
                    if o.get("market"):
                        # În paper-live, un ordin MARKET trebuie executat la
                        # prețul observat acum, inclusiv într-o cădere sub
                        # prețul de referință al stopului/trailing-ului.
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
        # Orice fee este plătit o singură dată, chiar dacă poziția rămâne deschisă.
        # Astfel P&L-ul net mark-to-market nu supraestimează pozițiile cu BUY executat.
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
                # Ordin plasat != poziție de trend. Activăm modul numai după ce
                # exchange-ul/replay-ul confirmă fill-ul.
                self.s["trend_mode"] = True
                self.s["trend_peak"] = price
            avg = self._avg()
            log(f"  [STRAT] {tag}BUY FILLED {vol} @ {price} {self.ccy} ({o.get('kind')})  "
                f"qty={self.s['qty']:.8f} avg={avg:.{self.price_dec}f} fee={fee}")
            notify(title=f"{tag}{self.pair} BUY {vol:.2f}@{price:.2f}",
                   body=(f"{o.get('kind')} | q{self.s['qty']:.2f} a{avg:.2f} | "
                         f"desf{self.s['spent']:.0f}{self.ccy}"),
                   source="kraken", price=price, desktop=self.desktop)
            self._cancel_open("sell")
        else:  # sell
            avg = self._avg() or price
            gross = (price - avg) * vol
            net = gross - fee
            self.s["realized_gross"] += gross
            self.s["realized_net"] += gross
            self.s["qty"] -= vol
            # La un SELL parțial se descarcă proporțional și cost basis-ul. Fără
            # asta, costul întreg rămânea pe cantitatea redusă și media exploda.
            self.s["cost"] = max(0.0, self.s["cost"] - avg * vol)
            # dust-ul lasat la vanzare (_dust_safe_qty) ar ramane altfel ca un
            # rest infim, permanent, in qty -> ciclul urmator crede ca INCA are
            # o pozitie deschisa si nu reintra niciodata. Praguim la 0 real.
            # Marja x2 fata de pasul de praf (nu doar x1): reziduul teoretic e
            # ~1 pas, dar imprecizia in virgula mobila poate depasi usor pasul
            # exact (testat: 1.0000000028e-07 vs prag 1e-07 -> comparatia <
            # stricta rata din cauza asta).
            if abs(self.s["qty"]) < 2 * 10.0 ** -(max(self.vol_dec - 1, 1)):
                self.s["qty"] = 0.0
                self.s["cost"] = 0.0
            log(f"  [STRAT] {tag}SELL FILLED {vol} @ {price} {self.ccy}  "
                f"brut={gross:+.4f} fee_ciclu={self.s['cycle_fees']:.4f} net={net:+.4f}")
            notify(title=f"{tag}{self.pair} SELL {vol:.2f}@{price:.2f} N{net:+.2f}{self.ccy}",
                   body=(f"a{avg:.2f} · br{gross:+.2f} fee{self.s['cycle_fees']:.2f} N{net:+.2f} | "
                         f"Ntot{self.s['realized_net']:+.2f}{self.ccy}"),
                   source="kraken", price=price, desktop=self.desktop)
            if self.s["qty"] <= 1e-12:
                keep = (self.s["realized_gross"], self.s["realized_net"],
                        self.s["fees_total"], self.s.get("cycle", 1) + 1)
                self.s = _new_state()
                (self.s["realized_gross"], self.s["realized_net"],
                 self.s["fees_total"], self.s["cycle"]) = keep
                self.s["last_sell_price"] = price   # pt regula de reintrare (nu recumpara mai sus)
                self.s["last_exit_kind"] = o.get("kind")   # "TP"/"STOP" -> reintrare STOP-aware
                self.s["sl_low"] = price            # minim initial pt reintrarea pe revenire dupa SL
                log(f"  [STRAT] === ciclu inchis, reincep (ciclu {self.s['cycle']}) ===")

    # -- decizie ---------------------------------------------------------------
    def _check_stop_loss(self, price: float) -> bool:
        """Inchide TOT daca pierderea nerealizata depaseste pragul (anti-runaway DCA)."""
        if self.p.stop_loss_pct <= 0:
            return False
        avg = self._avg()
        if not avg:
            return False
        loss_pct = (avg - price) / avg * 100   # long: pierdem cand pretul < pret mediu (pt log)
        if sr.hit_stop(avg, price, self.p.stop_loss_pct):
            log(f"  🛑 [STRAT] STOP-LOSS: pierdere {loss_pct:.2f}% >= {self.p.stop_loss_pct}% — VAND TOT (taie pierderea)")
            for o in list(self.s["orders"]):           # anuleaza toate ordinele pendinte (si DCA-urile)
                if not self.dry_run and not str(o["txid"]).startswith("PAPER"):
                    try:
                        self.client.cancel_order(o["txid"])
                    except KrakenError:
                        pass
                self._remove(o)
            self._place("sell", self._dust_safe_qty(self.s["qty"]),
                        round(price * 0.995, self.price_dec), kind="STOP", market=True)
            notify(title=f"🛑 SL {self.pair} -{loss_pct:.1f}%",
                   body=f"pierdere {loss_pct:.1f}% ≥prag{self.p.stop_loss_pct}% — vand tot",
                   source="kraken", price=price, desktop=self.desktop)
            return True
        return False

    def _maybe_adopt(self) -> None:
        """Adopta o pozitie EXISTENTA din cont (ex. alocare IPO/xStock) in loc sa
        cumpere intrarea. Ruleaza o singura data, DOAR pe stare proaspata, ca sa
        nu strice un ciclu in curs (ex. botul de HYPE)."""
        if self.p.adopt_cost <= 0 or self.s.get("adopted"):
            return
        if (self.s["qty"] > 1e-12 or self.s["orders"]
                or self.s["cycle"] != 1 or self.s["spent"] > 0):
            log("  [STRAT] adopt: starea nu e proaspata — NU adopt (ciclu in curs)")
            return
        qty = self.p.adopt_qty
        if qty <= 0:  # citeste cantitatea din balanta (activul de baza al perechii)
            try:
                info = self.client.pair_info(self.pair) or {}
                base = info.get("base", "")
                qty = float(self.client.balance().get(base, 0) or 0)
            except KrakenError as e:
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
        notify(title=f"📥 {self.pair} ADOPTAT {qty:.2f}@{self.p.adopt_cost}",
               body=f"TP+{self.p.takeprofit_pct}% DCA-{self.p.dca_drop_pct}% SL{self.p.stop_loss_pct}%",
               source="kraken-bot", price=self.p.adopt_cost, desktop=self.desktop)

    # -- SHADOW vol-adaptiv (doar observatie/log, nu decide nimic) --------------
    def _shadow_vol_1h(self) -> float | None:
        """Volatilitate 1h (%) din istoricul propriu de tick-uri. None = warm-up."""
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
        """Trend scurt UP din istoricul propriu de preturi (self._shadow_prices):
        media jumatatii RECENTE > media jumatatii VECHI cu >= tp_trend_min_pct%
        (peste zgomot). Determinist -> IDENTIC in live si backtest (aceeasi serie de
        preturi intra in step()). False la warm-up (<min_pts puncte)."""
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
        """Pragul de reintrare EFECTIV folosit la decizie: adaptiv (K_REENTRY *
        vol_1h) daca reentry_adaptive e activat SI volatilitatea poate fi
        calculata; altfel cade pe reentry_drop_pct (fix) — fail-safe, ca
        gate-ul Kalman (nu opreste/altereaza trading-ul din cauza unui semnal
        indisponibil). Investigat 22-23 iul (offline/research/kraken_adaptive_thresholds/,
        vezi README.md): adaptiv bate fix pe HYPEUSD (TOTAL +3.26% vs +2.20%,
        ~30 zile), K=2.0 confirmat optim printr-un sweep dedicat."""
        if not self.p.reentry_adaptive:
            return self.p.reentry_drop_pct, "fix"
        try:
            k_re = float_env("SHADOW_K_REENTRY") or 2.0
            vol = self._shadow_vol_1h()
        except Exception as e:  # noqa: BLE001 — nu opreste trading-ul
            log(f"  [REINTRARE-ADAPTIV] eroare calcul ({e}) — fallback pe fix")
            return self.p.reentry_drop_pct, "fix (fallback, eroare)"
        if vol is None:
            return self.p.reentry_drop_pct, f"fix (fallback, warm-up {len(self._shadow_prices)}/20)"
        return k_re * vol, f"adaptiv (vol_1h {vol:.2f}% x k={k_re})"

    def _effective_trail_pct(self) -> float:
        """A: pullback-ul de trailing EFECTIV. Adaptiv (k×vol_1h, clamp) daca
        tp_trail_adaptive; altfel tp_trail_pct fix. Fail-safe pe warm-up/eroare
        (cade pe fix), ca gate-ul de reintrare adaptiva — nu altereaza trading-ul
        cand semnalul lipseste. Ideea: ride mai LARG in trend volatil, mai STRANS
        in chop, FARA a cumpara sus (defectul overlay-ului cu top-up)."""
        if not self.p.tp_trail_adaptive:
            return self.p.tp_trail_pct
        try:
            vol = self._shadow_vol_1h()
        except Exception:  # noqa: BLE001 — nu opreste trading-ul
            vol = None
        if vol is None:
            return self.p.tp_trail_pct
        return max(self.p.tp_trail_min, min(self.p.tp_trail_max, self.p.tp_trail_k * vol))

    def _trend_down(self, min_pts: int = 20) -> bool:
        """B: downtrend scurt confirmat din istoricul propriu de preturi (simetric
        cu _trend_up): media jumatatii RECENTE < media celei VECHI cu >= dca_brake_min_pct%.
        Determinist -> identic live/backtest. False la warm-up (fail-safe: DCA normal)."""
        pts = [p for _, p in self._shadow_prices]
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
        """Seria de INCHIDERI pt semnalul de trend LUNG, ACELASI timescale live si backtest:
        - BACKTEST (replay_mode): barele fed-uite in step() (_shadow_prices = inchiderile lor).
        - LIVE/PAPER-LIVE: OHLC Kraken pe trend_interval (fetch cache-uit 15min).
        Rezolva gap-ul de cadenta: SMA(N) inseamna acelasi lucru in ambele (240m×30 = ~5 zile)."""
        if self.replay_mode:
            return [p for _, p in self._shadow_prices]
        try:
            return self.client.ohlc_closes(self.pair, self.p.trend_interval)
        except Exception as e:  # noqa: BLE001 — fara semnal -> pur si simplu nu intra in trend
            log(f"  [STRAT] trend OHLC fetch esuat ({e}) — trend nedeterminat")
            return []

    def _trend_up_series(self, closes: list) -> bool:
        """UPTREND CONFIRMAT: ultimele `trend_confirm_bars` bare au close > SMA(N) SI SMA(N)
        in crestere. Determinist -> IDENTIC live/backtest (aceeasi serie de inchideri)."""
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
        """Overlay de regim. True = a gestionat tick-ul (nu mai rula logica de range).
        In UPTREND confirmat: TOP-UP mare + hold; iese pe trailing (A) sau trailing SAU
        pret<SMA=trend rupt (B). In range (fara uptrend): False -> cade pe DCA/TP clasic."""
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
                sells = [o for o in self.s["orders"] if o["side"] == "sell"]
                if not (len(sells) == 1 and abs(sells[0]["price"] - exit_px) / exit_px <= 0.001):
                    while self._find_open("sell"):
                        self._cancel_open("sell")
                    self._place("sell", self._dust_safe_qty(self.s["qty"]), exit_px, kind="TP", market=True)
                    log(f"  [STRAT] TREND EXIT ({'break' if broke else 'trailing'} "
                        f"{self.p.trend_trail_pct}%) varf {peak:.{self.price_dec}f} -> IES la {exit_px}")
            else:
                while self._find_open("sell"):     # ride: nu vinde
                    self._cancel_open("sell")
            return True
        if pending_trend_entry:
            if up:
                return True                         # așteaptă fill-ul top-up-ului
            self._cancel_open("buy")                # semnal dispărut înainte de fill
            log("  [STRAT] TREND ENTER anulat: semnalul a dispărut înainte de fill")
            return False                            # revine la strategia range
        if up and self.s["spent"] + self.p.trend_topup <= self.p.max_budget:
            while self._find_open("buy"):          # anuleaza ordine range pendinte
                self._cancel_open("buy")
            while self._find_open("sell"):
                self._cancel_open("sell")
            self._place("buy", self._qty_for(self.p.trend_topup, price), price,
                        kind="TREND_ENTRY", amount=self.p.trend_topup)
            log(f"  [STRAT] TREND ENTER pending: top-up {self.p.trend_topup} {self.ccy} @ {price} "
                f"(SMA{self.p.trend_sma_n} up, confirmat)")
            return True
        return False

    def step(self, price: float, timestamp: float | None = None) -> None:
        held = self.s["qty"]
        entry_px = sr.entry_price(price, self.p.entry_discount_pct)   # = price*(1 - disc%)
        # Live folosește ceasul real; replay-ul injectează timpul barei. Fără
        # asta un backtest de sute de bare rulat într-o secundă produce o
        # volatilitate orară absurdă și schimbă pragul adaptiv.
        tick_time = time.time() if timestamp is None else float(timestamp)
        self._shadow_prices.append((tick_time, price))

        # adoptare in asteptare: NU cumpara o intrare noua — alocarea e pe drum
        if self.p.adopt_cost > 0 and not self.s.get("adopted") and held <= 1e-12:
            self._maybe_adopt()
            if not self.s.get("adopted"):
                return
            held = self.s["qty"]

        # STOP-LOSS-ul este invariantă de siguranță și are prioritate față de
        # orice logică de regim, inclusiv hold/trailing din overlay.
        if held > 1e-12 and self._check_stop_loss(price):
            return

        # TREND OVERLAY: combina regim range (DCA/TP) cu regim trend (hold+trailing).
        if self.p.trend_overlay and self._overlay_step(price):
            return

        if held <= 1e-12:
            if self._has_open("buy"):
                return
            # REGULA DE REINTRARE — STOP-aware (4 aug):
            lsp = self.s.get("last_sell_price")
            if self.s.get("last_exit_kind") == "STOP" and self.p.reentry_sl_bounce_pct > 0 and lsp:
                # dupa un STOP-LOSS: reintra pe REVENIRE (bounce de la minimul de dupa vanzare),
                # NU asteptand o scadere si mai jos (regula veche lasa botul blocat afara cand
                # pretul isi revine — ex. vandut 51.19, HYPE la 55.6, prag vechi 50.06 de neatins).
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
                # dupa un TP (sau fara SL): nu recumpara mai sus decat ai vandut — asteapta
                # o scadere reala sub pretul vandut (anti "vand la 60.64, recumpar la 61.1")
                drop_pct, drop_source = self._effective_reentry_drop_pct()
                if drop_pct > 0 and lsp:
                    prag = lsp * (1 - drop_pct / 100)
                    # toleranta "aproape de prag" (botcore.are_close, determinist): pretul la
                    # tol% de prag conteaza ca atins — altfel ratam intrari la 2-3 centi de prag
                    if sr.reentry_drop_blocked(price, lsp, drop_pct, self.p.reentry_tolerance_pct):
                        log(f"  [STRAT] reintrare blocata: pret {price} > prag {prag:.2f} [{drop_source}]"
                            f"{f' (tol {self.p.reentry_tolerance_pct}%)' if self.p.reentry_tolerance_pct else ''} "
                            f"(vandut la {lsp}, astept -{drop_pct:.2f}%)")
                        if not self.p.reentry_adaptive:
                            self._shadow_reentry_line(price, lsp, prag)   # log comparativ DOAR cand fixul inca decide
                        return
            if self.s["spent"] + self.p.entry_amount > self.p.max_budget:
                log(f"  [STRAT] plafon {self.p.max_budget} {self.ccy} atins — nu intru")
                return
            self._place("buy", self._qty_for(self.p.entry_amount, entry_px),
                        entry_px, kind="ENTRY", amount=self.p.entry_amount)
            return

        avg = self._avg()
        trail_armed = self.s.get("trail_peak") is not None
        if (self.p.enable_takeprofit and avg and self.p.tp_trend_hold
                and (trail_armed or price >= sr.tp_price(avg, self.p.takeprofit_pct))):
            # v2 TRAILING: se ARMEAZA la prima depasire a TP-ului si ramane armat pana
            # la iesire. Evaluarea trebuie sa continue si daca pretul cade ulterior sub
            # TP; altfel varful s-ar reseta exact in pullback-ul pe care vrem sa-l vindem.
            peak = max(self.s.get("trail_peak") or price, price)
            self.s["trail_peak"] = peak
            eff_trail = self._effective_trail_pct()   # A: adaptiv pe vol daca activat, altfel fix
            trail_stop = peak * (1 - eff_trail / 100)
            while self._find_open("sell"):
                self._cancel_open("sell")
            if price <= trail_stop:
                exit_px = round(price * 0.999, self.price_dec)
                self._place("sell", self._dust_safe_qty(self.s["qty"]), exit_px, kind="TP", market=True)
                log(f"  [STRAT] trailing: pullback {eff_trail:.2f}% de la varf "
                    f"{peak:.{self.price_dec}f} -> IES la {exit_px} (calarit trendul)")
                # Nu deschide un DCA contradictoriu in acelasi tick in care iesim.
                return
            else:
                log(f"  [STRAT] peste TP, CALARESC (varf {peak:.{self.price_dec}f}, "
                    f"trail-stop {trail_stop:.{self.price_dec}f})")
        elif self.p.enable_takeprofit and avg and self.p.tp_trend_hold:
            # v2 SUB TP, ride ON: NU plasez TP fix (altfel se umple la TP si nu mai calaresc).
            # Astept sa DEPASESC nivelul TP ca sa pornesc trailing-ul; anulez orice sell fix.
            # Iesirea de siguranta ramane STOP-LOSS-ul (verificat mai sus in step()).
            self.s["trail_peak"] = None
            while self._find_open("sell"):
                self._cancel_open("sell")
        elif self.p.enable_takeprofit and avg:
            self.s["trail_peak"] = None   # sub TP / mod clasic -> reset varf
            # TP in TRANSE (optional, STRAT_TP_TRANCHES="3:50,6:50"): vinde gradual.
            # Fara tranче configurate = comportamentul CLASIC (un TP pe tot) — DEFAULT.
            tranches = self.p.tp_tranches or [(self.p.takeprofit_pct, 100.0)]
            desired, rem = [], held
            for i, (pct, share) in enumerate(tranches):
                # ultima transa = vinde tot ce-a ramas -> risc de "Insufficient funds"
                # (balanta reala de pe Kraken poate fi cu o zecimila mai mica decat
                # held-ul urmarit intern) — acelasi praf ca la adoptie.
                q = self._dust_safe_qty(rem) if i == len(tranches) - 1 \
                    else min(rem, round(held * share / 100, self.vol_dec))
                rem = round(rem - q, self.vol_dec)
                if q > 0:
                    desired.append((round(sr.tp_price(avg, pct), self.price_dec), q))
            if self.ordermin and any(q < self.ordermin for _, q in desired):
                desired = [(round(sr.tp_price(avg, tranches[0][0]), self.price_dec), held)]
            sells = [o for o in self.s["orders"] if o["side"] == "sell"]
            ok = (len(sells) == len(desired) and
                  all(abs(o["price"] - p) / p <= 0.001 and abs(o["vol"] - q) <= 1e-9
                      for o, (p, q) in zip(sorted(sells, key=lambda x: x["price"]),
                                           sorted(desired))))
            if not ok:
                while self._find_open("sell"):
                    self._cancel_open("sell")
                for p_, q_ in desired:
                    self._place("sell", q_, p_, kind="TP")

        if (self.s["dca_buys"] < self.p.max_dca_buys
                and self.s["last_buy_price"]
                # prag DCA + "aproape de prag" = atins (regula partajata cu backtest)
                and sr.dca_price_hit(price, self.s["last_buy_price"], self.p.dca_drop_pct, self.p.reentry_tolerance_pct)
                and self.s["spent"] + self.p.dca_amount <= self.p.max_budget
                and not (self.p.dca_trend_brake and self._trend_down())  # B: frana DCA in downtrend
                and not self._has_open("buy")):
            log(f"  [STRAT] dip {price} <= {self.s['last_buy_price']}×(1-{self.p.dca_drop_pct}%)"
                f" (tol {self.p.reentry_tolerance_pct}%) — DCA")
            self._place("buy", self._qty_for(self.p.dca_amount, entry_px),
                        entry_px, kind="DCA", amount=self.p.dca_amount)

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
        self._maybe_adopt()
        try:
            while True:
                price = get_price(self.client, self.pair)
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
                avg = self._avg()
                pos = f"qty={self.s['qty']:.8f} avg={avg:.{self.price_dec}f}" if avg else "qty=0 (astept intrare)"
                log(f"  [STRAT] pret={price}  {pos}  "
                    f"NET={self.s['realized_net']:+.2f} (brut {self.s['realized_gross']:+.2f}, "
                    f"fee {self.s['fees_total']:.2f}) {self.ccy}  ord={len(self.s['orders'])}")
                time.sleep(self.p.check_minutes * 60)
        except KeyboardInterrupt:
            log("  [STRAT] oprit manual.")
            self._save()
