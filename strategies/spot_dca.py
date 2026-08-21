#!/usr/bin/env python3
"""Motor spot DCA + take-profit/trailing, independent de venue.

Motorul ia toate datele si executa toate ordinele prin ``StrategyExecutor``.
Launcherul venue-ului injecteaza providerul, directorul de stare si prezentarea;
regulile financiare raman o singura implementare pentru live si replay.
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
from providers.execution_audit import new_intent_id
from providers.strategy_executor import ProviderError, StrategyExecutor

from . import spot_dca_rules as sr
from .state_store import JsonStateStore

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LEGACY_STATE_DIR = os.path.join(_ROOT, "kraken")
_DEFAULT_FEE_NOTE = "fee Kraken ~0.26% taker / ~0.16% maker per leg"


notify = bind_notify(("SYMBOL_LABEL", "KRAKEN_PAIR"), "CRYPTO")


def state_path_for(pair: str, state_dir: str | None = None) -> str:
    """Returneaza calea starii; fallback-ul Kraken pastreaza upgrade-ul live compatibil."""
    safe = "".join(c for c in pair if c.isalnum() or c in "._-")
    directory = state_dir or _LEGACY_STATE_DIR
    return os.path.join(directory, f".state_{safe}.json")


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
    tp_trail_profit_floor_pct: float = 0.0  # 0=compatibil live. >0=trailing MARKET numai
                                      # dacă referința ordinului este >=avg*(1+floor%);
                                      # hard stop-ul rămâne MARKET și are prioritate.
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
    tp_trail_vol_interval: int = 240  # minute/bara pentru volatilitate; fix în live/replay
    dca_trend_brake: bool = False     # B: in DOWNTREND confirmat, NU face DCA (nu prinde cutitul care
                                      # cade) — ataca direct maxDD, overlay care REDUCE risc.
    dca_brake_min_pct: float = 1.5    # panta minima (%) recent/vechi ca sa considere downtrend
    dca_spacing_growth_pct: float = 0.0  # crește pragul după fiecare DCA executat;
                                         # 0 = comportamentul live byte-identical
    # --- #2: SIZING DCA scalat pe VOLATILITATE (default OFF) ---
    dca_vol_scale_k: float = 0.0      # 0=OFF. eff_dca = dca × (vol_ref/vol_1h)^k, clamp [0.3,3].
                                      # k>0 = DCA MAI MIC in vol mare (defensiv, minimizeaza pierderea);
                                      # k<0 = MAI MARE in vol (harvest agresiv). Fail-safe pe warm-up.
    dca_vol_ref: float = 2.0          # vol_1h (%) de referinta pt scalare
    dca_vol_interval: int = 240       # cadență OHLC identică în live și replay

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
            # Zero este o valoare explicita valida: dezactiveaza reintrarea pe
            # revenire dupa STOP si pastreaza regula clasica de reintrare.
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
        # dry_run înseamnă și paper-live, nu doar backtest. Replay-ul trebuie
        # identificat separat ca semnalul de trend să folosească barele injectate.
        self.replay_mode = replay_mode
        self.desktop = desktop
        self._notifier = notifier
        self.notification_source = notification_source
        self.venue_label = venue_label
        self.fee_note = fee_note
        # Apelul legacy cu un singur argument ramane intentionat: replay-urile
        # existente pot inlocui state_path_for pentru a garanta zero I/O.
        self.state_file = (
            state_path_for(pair) if state_dir is None else state_path_for(pair, state_dir)
        )
        self._state_write_failed = False
        self.s = initial_state if initial_state is not None else self._load()
        self._paper_seq = 0
        # SHADOW vol-adaptiv (observational, plan 17 iul): istoric mic de pret in
        # memorie (tick ~2min -> ~3h) pentru sigma; NU intra in state-file, se
        # reconstruieste dupa restart (warm-up ~40min pana la >=20 puncte).
        self._shadow_prices = deque(maxlen=90)
        # precizie pereche, normalizata de provider
        self.price_dec, self.vol_dec, self.ordermin = 5, 8, 0.0
        try:
            precision = client.pair_precision(pair)
            if precision:
                self.price_dec = precision.price_decimals
                self.vol_dec = precision.volume_decimals
                self.ordermin = precision.order_min
        except ProviderError:
            log("  ! nu pot citi precizia perechii — folosesc valori implicite")

    def _emit(self, **event) -> None:
        """Trimite un eveniment prin sink-ul venue-ului sau prin compatibilitatea veche."""
        # Replay/backtest foloseste aceeasi strategie ca live, dar nu are voie sa
        # produca efecte externe. ``dry_run`` nu este suficient: paper-live are
        # nevoie in continuare de alerte reale.
        if self.replay_mode:
            return
        (self._notifier or notify)(**event)

    # -- persistenta -----------------------------------------------------------
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

    # -- helperi ---------------------------------------------------------------
    def _avg(self) -> float | None:
        return self.s["cost"] / self.s["qty"] if self.s["qty"] > 1e-12 else None

    def _qty_for(self, amount: float, price: float) -> float:
        return round(amount / price, self.vol_dec) if price > 0 else 0.0

    def _dust_safe_qty(self, qty: float) -> float:
        """Un venue poate raporta balanta ROTUNJITA: vinderea intregii cantitati
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
               market: bool = False) -> bool:
        # market=True: iesire de piata (trailing/stop) — se executa imediat, NU ordin limita
        # care poate rata o cadere brusca. In backtest se umple la open-ul barei urmatoare.
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
        try:
            intent_id = new_intent_id(self.venue_label, self.pair, kind)
            submit_with_intent = getattr(type(self.client), "submit_order_with_intent", None)
            if callable(submit_with_intent):
                txid = submit_with_intent(
                    self.client, intent_id, self.pair, side, vol, None if market else price,
                    market=market, kind=kind,
                    reference_price=price if market else None,
                )
            else:
                txid = self.client.submit_order(
                    self.pair, side, vol, None if market else price,
                    market=market, kind=kind,
                )
            log(f"  [STRAT] {side.upper()} {kind} plasat txid={txid} {vol} @ {price}")
            self.s["orders"].append({"txid": txid, "side": side, "vol": vol, "price": price,
                                     "amount": amount, "kind": kind, "market": market,
                                     "intent_id": intent_id, "ts": time.time()})
            # Inchide cat mai mult fereastra de crash dintre acceptarea la venue
            # si snapshot-ul de la finalul tick-ului. In live, un order_id acceptat
            # trebuie sa fie durabil inaintea oricarei alte decizii.
            self._save()
            return True
        except ProviderError as e:
            log(f"  ! [STRAT] {side} {kind} esuat: {e}")
            return False

    def _cancel_order(self, o: dict) -> bool:
        """Solicita anularea fara sa uite ordinul inainte de confirmarea venue-ului.

        O anulare acceptata nu spune daca ordinul a avut un fill concurent chiar
        inainte de anulare. Il pastram pana cand providerul raporteaza o stare
        terminala, ca reconcilierea sa poata aplica acel fill final.
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
        # Pastreaza intentia de cancel peste restart; ordinul ramane urmarit pana
        # cand statusul terminal confirma inclusiv orice fill concurent.
        self._save()
        log(f"  [STRAT] cancel solicitat {o['side']} {o['txid']} — astept status terminal")
        return True

    def _cancel_open(self, side: str) -> bool:
        o = self._find_open(side)
        if not o:
            return True
        return self._cancel_order(o)

    def _cancel_orders(self, side: str | None = None, *, exclude_market: bool = False) -> bool:
        """Solicita anularea ordinelor selectate; False daca oricare cerere esueaza."""
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
                # REAL: providerul raporteaza qty/cost/fee CUMULATIV, inclusiv
                # cat timp ordinul este open. Aplicam doar delta fata de ultima
                # reconciliere salvata pe ordin.
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
                # Fee-ul este semnat: unele venue-uri raporteaza rebate maker
                # negativ. Diferenta semnata pastreaza acel venit in P&L.
                delta_fee = total_fee - applied_fee
                # Persistam markerii INAINTE de contabilizare: _apply_fill poate
                # inchide ciclul si inlocui intreg state-ul la un SELL final.
                o["applied_vol"] = total_vol
                o["applied_cost"] = total_cost
                o["applied_fee"] = total_fee

                if delta_vol > eps:
                    fill_order = dict(o)
                    if o["side"] == "buy":
                        # `amount` este plafonul nominal al ordinului; un fill
                        # partial consuma proportional din acel plafon.
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
                    # Rar, fee-ul final poate aparea cu un poll dupa ultimul
                    # volum. Il taxam fara sa simulam un fill de volum zero.
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
            # La un SELL parțial se descarcă proporțional și cost basis-ul. Fără
            # asta, costul întreg rămânea pe cantitatea redusă și media exploda.
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
        """Inchide ciclul o singura data, numai dupa status terminal al iesirii."""
        # Dust-ul lasat de _dust_safe_qty nu trebuie sa tina ciclul deschis.
        # Aplicam pragul numai la un ordin terminal: un partial fill inca open
        # poate avea legitim un rest foarte mic ce urmeaza sa fie executat.
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
            # Un exit MARKET deja trimis este in curs de reconciliere. Nu il
            # anula si nu trimite inca unul la fiecare tick/API timeout.
            if self._has_pending_market_exit():
                return True
            log(f"  🛑 [STRAT] STOP-LOSS: pierdere {loss_pct:.2f}% >= {self.p.stop_loss_pct}% — VAND TOT (taie pierderea)")
            # Nu uitam niciun ordin daca anularea esueaza: un DCA/TP "fantoma"
            # poate umple dupa iesire. Daca toate cancelarile sunt acceptate,
            # trimitem exit-ul imediat; statusurile terminale se confirma ulterior.
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
        """Prețul minim al ieșirii soft, rotunjit în sus la precizia venue-ului.

        Pragul este intenționat brut și simplu. Configurația trebuie să includă
        suficient buffer pentru fee-uri; benchmarkul central/stress măsoară apoi
        profitul net cu fee-urile și fill-urile scenariului. ``0`` păstrează exact
        trailing-ul MARKET existent.
        """
        pct = float(self.p.tp_trail_profit_floor_pct or 0.0)
        if pct <= 0 or avg <= 0:
            return None
        raw = sr.tp_price(avg, pct)
        scale = 10 ** self.price_dec
        return math.ceil(raw * scale - 1e-12) / scale

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

    # -- SHADOW vol-adaptiv (doar observatie/log, nu decide nimic) --------------
    def _effective_dca_amount(self) -> float:
        """#2: marimea DCA scalata pe volatilitate. dca_vol_scale_k=0 -> fix (dca_amount).
        Fail-safe pe warm-up/eroare (cade pe fix), ca reintrarea adaptiva."""
        scale_k = float(self.p.dca_vol_scale_k)
        vol_ref = float(self.p.dca_vol_ref)
        if not scale_k:
            return self.p.dca_amount
        if not math.isfinite(scale_k) or not math.isfinite(vol_ref) or vol_ref <= 0:
            return self.p.dca_amount
        try:
            vol = self._dca_vol_1h()
            if not vol or not math.isfinite(vol) or vol <= 0:
                return self.p.dca_amount
            scale = (vol_ref / vol) ** scale_k
        except (ArithmeticError, TypeError, ValueError):
            return self.p.dca_amount
        if not math.isfinite(scale):
            return self.p.dca_amount
        return self.p.dca_amount * max(0.3, min(3.0, scale))

    def _dca_vol_1h(self) -> float | None:
        """Volatilitate DCA din OHLC la aceeași cadență în live și replay."""
        if self.replay_mode:
            closes = [price for _, price in self._shadow_prices]
        else:
            closes = self.client.ohlc_closes(self.pair, self.p.dca_vol_interval)
        return self._hourly_vol_from_closes(closes, self.p.dca_vol_interval)

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
            vol = self._trail_vol_1h()
        except Exception as e:  # noqa: BLE001 — nu opreste trading-ul
            log(f"  [STRAT] trailing adaptiv: OHLC indisponibil ({e}) — fallback pe fix")
            vol = None
        if vol is None:
            return self.p.tp_trail_pct
        return max(self.p.tp_trail_min, min(self.p.tp_trail_max, self.p.tp_trail_k * vol))

    def _trail_vol_1h(self) -> float | None:
        """Volatilitate normalizată la 1h din aceeași cadență OHLC în live și replay.

        Tick-urile live de 2 minute versus close-urile de 4h din backtest produceau
        semnale diferite chiar după scalarea cu sqrt(t). Live citește barele providerului
        închise; replay-ul este validat separat să ruleze pe același interval.
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
        """Deviația log-return normalizată la o oră pentru o cadență fixă."""
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
        """B: downtrend pe OHLC fix, identic ca scară temporală în live și replay."""
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
        """Seria de INCHIDERI pt semnalul de trend LUNG, ACELASI timescale live si backtest:
        - BACKTEST (replay_mode): barele fed-uite in step() (_shadow_prices = inchiderile lor).
        - LIVE/PAPER-LIVE: OHLC provider pe trend_interval (fetch-ul poate fi cache-uit).
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
                if not self._has_pending_market_exit():
                    if not self._cancel_orders("sell", exclude_market=True):
                        log("  ! [STRAT] TREND EXIT amanat: un SELL nu a putut fi anulat")
                        return True
                    if self._place("sell", self._dust_safe_qty(self.s["qty"]), exit_px,
                                   kind="TP", market=True):
                        log(f"  [STRAT] TREND EXIT ({'break' if broke else 'trailing'} "
                            f"{self.p.trend_trail_pct}%) varf {peak:.{self.price_dec}f} -> IES la {exit_px}")
            else:
                self._cancel_orders("sell", exclude_market=True)  # ride: nu vinde
            return True
        if pending_trend_entry:
            if up:
                return True                         # așteaptă fill-ul top-up-ului
            self._cancel_open("buy")                # semnal dispărut înainte de fill
            log("  [STRAT] TREND ENTER anulat: semnalul a dispărut înainte de fill")
            return False                            # revine la strategia range
        if up and self.s["spent"] + self.p.trend_topup <= self.p.max_budget:
            self._cancel_orders("buy")              # anuleaza ordine range pendinte
            self._cancel_orders("sell")
            if self.s["orders"]:                    # REAL: asteapta confirmarile terminale
                return True
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
        # Un exit MARKET trimis intr-un tick anterior trebuie reconciliat inainte
        # de orice TP/DCA nou, chiar daca pretul a revenit intre timp.
        if self._has_pending_market_exit():
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
            # Aceeași referință conservatoare folosită la ordinul MARKET. Pragul
            # se aplică ei, nu prețului brut observat, ca bufferul de 0,1% să nu
            # transforme o ieșire exact la floor într-o pierdere implicită.
            exit_px = round(price * 0.999, self.price_dec)
            profit_floor = self._trail_profit_floor_price(avg)
            if price <= trail_stop and profit_floor is not None and exit_px < profit_floor:
                log(f"  [STRAT] trailing soft blocat: referința {exit_px:.{self.price_dec}f} sub pragul "
                    f"profitabil {profit_floor:.{self.price_dec}f}; hard stop rămâne MARKET")
                # Reevaluează la tickul următor. Nu lasă un ordin persistent și nu
                # deschide un DCA contradictoriu în tickul în care trailing-ul a fost atins.
                return
            if price <= trail_stop:
                if not self._cancel_orders("sell", exclude_market=True):
                    log("  ! [STRAT] trailing exit amanat: un SELL nu a putut fi anulat")
                    return
                if self._place("sell", self._dust_safe_qty(self.s["qty"]), exit_px,
                               kind="TP", market=True):
                    log(f"  [STRAT] trailing: pullback {eff_trail:.2f}% de la varf "
                        f"{peak:.{self.price_dec}f} -> IES la {exit_px} (calarit trendul)")
                # Nu deschide un DCA contradictoriu in acelasi tick in care iesim.
                return
            else:
                self._cancel_orders("sell", exclude_market=True)
                log(f"  [STRAT] peste TP, CALARESC (varf {peak:.{self.price_dec}f}, "
                    f"trail-stop {trail_stop:.{self.price_dec}f})")
        elif self.p.enable_takeprofit and avg and self.p.tp_trend_hold:
            # v2 SUB TP, ride ON: NU plasez TP fix (altfel se umple la TP si nu mai calaresc).
            # Astept sa DEPASESC nivelul TP ca sa pornesc trailing-ul; anulez orice sell fix.
            # Iesirea de siguranta ramane STOP-LOSS-ul (verificat mai sus in step()).
            self.s["trail_peak"] = None
            self._cancel_orders("sell", exclude_market=True)
        elif self.p.enable_takeprofit and avg:
            self.s["trail_peak"] = None   # sub TP / mod clasic -> reset varf
            # TP in TRANSE (optional, STRAT_TP_TRANCHES="3:50,6:50"): vinde gradual.
            # Fara tranче configurate = comportamentul CLASIC (un TP pe tot) — DEFAULT.
            tranches = self.p.tp_tranches or [(self.p.takeprofit_pct, 100.0)]
            desired, rem = [], held
            for i, (pct, share) in enumerate(tranches):
                # ultima transa = vinde tot ce-a ramas -> risc de "Insufficient funds"
                # (balanta reala de pe venue poate fi cu o zecimala mai mica decat
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
                and self.s["spent"] + effective_dca_amount <= self.p.max_budget
                and not (self.p.dca_trend_brake and self._trend_down())  # B: frana DCA in downtrend
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

    # -- bucla -----------------------------------------------------------------
    def run(self) -> None:
        mode = "avg_tp" if self.p.enable_takeprofit else "dca_only"
        log(f"  === STRATEGIE {self.venue_label.upper()} PORNITA ===")
        log(f"      pereche    : {self.pair}   {'[PAPER]' if self.dry_run else '⚠ REAL — BANI'}")
        log(f"      mod        : {mode}")
        log(f"      intrare    : {self.p.entry_amount} {self.ccy} @ market-{self.p.entry_discount_pct}%")
        log(f"      DCA        : {self.p.dca_amount} {self.ccy} la -{self.p.dca_drop_pct}% (max {self.p.max_dca_buys})")
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
        log(f"      PLAFON     : {self.p.max_budget} {self.ccy} / ciclu")
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
                    # Dupa o eroare de disc, nu mai luam decizii noi pana cand
                    # state-ul curent din memorie poate fi persistat din nou.
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
