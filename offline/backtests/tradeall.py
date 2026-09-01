#!/usr/bin/env python3
"""
offline.backtests.tradeall — reia istoricul de pret deja salvat
(cachedb/cache_price_{symbol}.jsonl, ~11 luni) prin EXACT acelasi model de
decizie ca tradeall.py (PriceWindow / WindowAnalyzer / TrendState / logic /
logic_small), cu place_order_smart inlocuit de un stub care simuleaza
execution instead of hitting the Binance network. It writes ONLY into a separate
logger/backtest/<run_id>/ — NICIODATA in logurile live folosite de
tradeall.py real (vezi plan A5).

Rulare:
    python3 -m offline.backtests.tradeall --symbol BTCUSDC --start 2026-06-01 --speed fast
    python3 -m offline.backtests.tradeall --symbol BTCUSDC --start 2026-06-01 --end 2026-06-08 --speed real

Visualisation (while running or afterwards): in another terminal,
    ./tradeall_observe.py --backtest-dir logger/backtest/<run_id> --symbols BTCUSDC
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import tradeall as ta  # reutilizam PriceWindow/WindowAnalyzer/TrendState/logic (A5) — nu reimplementam modelul
# 23 iul: SimClock extras in providers/replay_clock.py (partajat cu
# monitortrades — vezi UNIFIED_BACKTEST_PLAN.md §7/§8), pasat catre
# TrendState(now_fn=...) ca fast-forward sa fie corect (A5). Alias
# `_SimClock` pastrat pt orice referinta externa existenta (offline/research/*.py).
from providers.replay_clock import SimClock as _SimClock  # noqa: F401
from offline.research.monitortrades_backtest.replay_trend_source import (  # noqa: E402
    DynamicReplayTrendSource,
)


def _sanitize(value):
    return str(value).replace("|", "/").replace("\n", " ") if value is not None else ""


FEE_PCT = 0.1   # comision spot Binance ~0.1% per leg (taker)
# marime standard de pozitie pt simulare (kalman-primary + benchmark buy&hold) —
# 21 Jul: replaces ta.api.quantities[symbol] (removed from bapi.py, it was only
# un placeholder mereu taiat de weight-limit in live, dar aici chiar avem nevoie
# de un NUMAR REAL, fix, ca sa comparam onest intre variante pe acelasi volum.
BACKTEST_NOTIONAL_USD = 1000.0


class BacktestBroker:
    """Stub for po.place_order_smart: it simulates execution (no network),
    scrie in propriul folder de backtest, acelasi format pipe ca order_outcomes
    live (Pas A2) — asa incat tradeall_observe.py sa il poata randa identic.
    Tine si CONTABILITATE P&L: pozitie, cost mediu, realizat, comisioane."""
    def __init__(self, out_dir, clock, trend_gate=None, trend_gate_timeout_sec=None,
                 trend_gate_max_wait_sec=3600.0):
        self.clock = clock
        self.path = os.path.join(out_dir, "order_outcomes.log")
        self.n_buy = self.n_sell = 0
        self.pos_qty = 0.0
        self.pos_cost = 0.0       # cost total al pozitiei curente (fara fee)
        self.realized = 0.0       # realised profit/loss (excluding fees)
        self.fees = 0.0
        self.last_price = None
        # 29 iul (idee user): gate de "asteapta un pret mai bun". FIDEL cu live
        # (bapi_placeorder.__place_order -> _maybe_wait_trend -> wait_for_favorable_entry):
        # the gate does NOT block the order, it only DELAYS it until a favourable tick
        # (or until max_wait, then places it anyway). The P&L effect is a better
        # intrare/iesire mai bun, NU tranzactii evitate. Ordinele amanate stau in
        # self.pending si se executa pe tick-urile viitoare (process_pending, chemat
        # din bucla principala). trend_gate=None => OFF (comportament vechi).
        self.trend_gate = trend_gate
        self.trend_gate_timeout_sec = trend_gate_timeout_sec
        self.trend_gate_max_wait_sec = float(trend_gate_max_wait_sec)
        self.pending = []          # [{side, symbol, qty_arg, motivation, deadline}]
        self.n_waited = 0          # cate ordine au fost intarziate (apoi executate)

    def _execute(self, order_type, symbol, price, qty=None, motivation=None):
        """The real P&L accounting (no gate) — used directly or after waiting."""
        price = float(price)
        if qty is None:
            qty = BACKTEST_NOTIONAL_USD / price
        qty = float(qty)
        self.last_price = price
        if order_type == "BUY":
            self.n_buy += 1
            self.pos_qty += qty
            self.pos_cost += qty * price
            self.fees += qty * price * FEE_PCT / 100
        else:
            if self.pos_qty <= 1e-12:
                return None            # nu avem ce vinde (spot) — refuz ca in realitate
            sell_q = min(qty, self.pos_qty)
            avg = self.pos_cost / self.pos_qty
            self.realized += (price - avg) * sell_q
            self.fees += sell_q * price * FEE_PCT / 100
            self.pos_cost -= avg * sell_q
            self.pos_qty -= sell_q
            self.n_sell += 1
        cols = [self.clock(), symbol, order_type, price, qty, "executed", "", "backtest", motivation]
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("|".join(_sanitize(c) for c in cols) + "\n")
        return {"orderId": -1, "backtest": True}   # obiect truthy, ca in "if order:" din logic()

    def place_order_smart(self, order_type, symbol, price, qty=None, motivation=None, **kwargs):
        # Gate-ul se aplica NECONDITIONAT (si la force=True) — user (29 iul, "gatul
        # is always welcome"), consistent with live. If the direction is NOT favourable yet,
        # INTARZIE (nu blocheaza): inregistreaza ordinul in pending, il executa la
        # primul tick favorabil (sau la expirarea max_wait). Ca la live, apelantul
        # (tradeall) primeste un rezultat truthy imediat -> marcheaza fire confirmat,
        # exactly as after a wait_for_favorable_entry that ends in a placement.
        if (self.trend_gate is not None
                and self.trend_gate.should_wait(symbol, order_type, self.trend_gate_timeout_sec)):
            self.pending.append({
                "side": order_type, "symbol": symbol, "qty_arg": qty,
                "motivation": motivation, "deadline": self.clock() + self.trend_gate_max_wait_sec})
            self.n_waited += 1
            return {"orderId": -1, "backtest": True, "pending": True}   # truthy, ca la live dupa wait
        return self._execute(order_type, symbol, price, qty, motivation)

    def process_pending(self, symbol, ts, price):
        """Execute the delayed orders that became favourable OR expired
        (max_wait), la pretul tick-ului curent. Chemat pe FIECARE tick din bucla."""
        if not self.pending:
            return
        still = []
        for o in self.pending:
            if o["symbol"] != symbol:
                still.append(o)
                continue
            favorable = not self.trend_gate.should_wait(symbol, o["side"], self.trend_gate_timeout_sec)
            expired = ts >= o["deadline"]
            if favorable or expired:
                self._execute(o["side"], symbol, price, o["qty_arg"], o["motivation"])
            else:
                still.append(o)
        self.pending = still

    def sell_all(self, symbol, price, motivation):
        if self.pos_qty <= 1e-12:
            return None
        return self.place_order_smart("SELL", symbol, price, self.pos_qty, motivation=motivation)

    def pnl_summary(self):
        m2m = 0.0
        if self.pos_qty > 1e-12 and self.last_price:
            m2m = (self.last_price - self.pos_cost / self.pos_qty) * self.pos_qty
        return {"buys": self.n_buy, "sells": self.n_sell,
                "realized": round(self.realized, 2), "fees": round(self.fees, 2),
                "open_qty": round(self.pos_qty, 6), "mark_to_market": round(m2m, 2),
                "net_total": round(self.realized + m2m - self.fees, 2),
                "n_waited": self.n_waited}


def make_decision_logger(out_dir, clock):
    path = os.path.join(out_dir, "tradeall_decisions.log")

    def _log_decision(symbol, event, **fields):
        try:
            cols = [clock(), symbol, event, fields.get("state", ""), fields.get("old_state", ""),
                    fields.get("price", ""), fields.get("prev_confirm_count", "")]
            with open(path, "a", encoding="utf-8") as f:
                f.write("|".join(_sanitize(c) for c in cols) + "\n")
        except OSError as e:
            print(f"[tradeall_backtest] eroare log_decision: {e}")
    return _log_decision


def load_ticks_history(symbol, start_ts, end_ts):
    """Citeste cache_price_{symbol}.jsonl — istoric lung (~11 luni), dar RAR
    (interval variabil, recent ~7 min/tick) — vezi caveat-ul din plan (A5)."""
    path = os.path.join(ROOT, "cachedb", f"cache_price_{symbol}.jsonl")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Cannot find {path} (the price history for {symbol})")
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("s") != symbol:
                continue
            ts_ms, price = rec["i"]
            ts = ts_ms / 1000.0
            if ts < start_ts:
                continue
            if end_ts is not None and ts > end_ts:
                return
            yield ts, price


def load_ticks_cache24(symbol, start_ts, end_ts, filename=None):
    """Read cache_24price_{symbol}.json (or a cache24 with long retention,
    daca filename e dat) — rezolutie DENSA (~1s/tick, ca live), dar limitata
    la ce a retinut acel cache (implicit doar ultimele ~24h).
    .jsonl (21 iul: cache_24price_long_*.jsonl, arhivatorul — vezi
    Cache24LongPriceManager) — format linie-cu-linie, citit integral aici
    (backtest-ul oricum parcurge tot intervalul cerut, spre deosebire de
    tradeall_observe.py, which reads only the tail for a chart)."""
    path = filename or os.path.join(ROOT, "cachedb", f"cache_24price_{symbol}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Cannot find {path} (cache24 for {symbol})")
    if str(path).endswith(".jsonl"):
        entries = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("s") == symbol:
                    entries.append(rec["i"])
    else:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get("items", {}).get(symbol, [])
    for ts_ms, price in entries:
        ts = ts_ms / 1000.0
        if ts < start_ts:
            continue
        if end_ts is not None and ts > end_ts:
            return
        yield ts, price


def run_backtest(symbol, start_ts, end_ts, speed, run_id, source, cache24_file=None, quiet=False,
                 kalman_primary=False, threshold_provider=None, trend_gate_timeout_sec=None,
                 trend_gate_max_wait_sec=3600.0):
    """threshold_provider OPTIONAL (default None = comportament VECHI, neschimbat:
    uses the fixed ta.PRICE_CHANGE_THRESHOLD_EUR/_BIG_EUR). If given, it is a callable
    threshold_provider(window_small, window_big) -> (thr_small, thr_big), apelat la
    FIECARE tick INAINTE de check_price_change() — asa poate un experiment sa testeze
    praguri DINAMICE (ex. adaptive pe volatilitate) fara sa copieze bucla intreaga
    (23 iul: offline/research/tradeall_adaptive_thresholds/ facuse exact asta inainte, cu
    riscul ca bucla proprie sa diverga tacut de asta, "oficiala", in timp)."""
    out_dir = os.path.join(ROOT, "logger", "backtest", run_id)
    # 23 iul: curata folderul INAINTE de a rula — mai multe fisiere de aici
    # (order_outcomes.log, tradeall_decisions.log, tradeall_shadow.log,
    # tradeall_price_samples.log) are written with open(..., "a") (append), so without
    # asta o a DOUA rulare cu ACELASI run_id ar amesteca tacut istoricul vechi cu
    # cel nou. Pana acum, fiecare caller trebuia sa-si aminteasca sa faca
    # shutil.rmtree() singur (offline/research/tradeall_trigger_gate/experiment_quality_
    # signal_v2.py o facea manual) — mutat aici o data, pt toti apelantii.
    import shutil as _shutil
    _shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    price_path = os.path.join(out_dir, "tradeall_price_samples.log")

    if quiet:
        # tradeall.logic()/check_price_change() fac print() la fiecare tick, oglindit si pe
        # disc (log.py) — pe date DENSE (cache24, zeci de mii de tick-uri) asta domina timpul
        # de rulare. log.disable_print() suprima global print() (mesajele NOASTRE folosesc
        # sys.stderr.write, care ramane vizibil).
        ta.log.disable_print()

    # 29 iul (idee user): gate de "asteapta un moment favorabil" pt intentiile de
    # BUY/SELL in reincercare — DynamicReplayTrendSource izolat (nu atinge
    # cache_instant_trend.json), alimentat tick-cu-tick mai jos, in bucla principala.
    trend_gate = DynamicReplayTrendSource([symbol]) if trend_gate_timeout_sec is not None else None

    clock = _SimClock()
    broker = BacktestBroker(out_dir, clock, trend_gate=trend_gate,
                            trend_gate_timeout_sec=trend_gate_timeout_sec,
                            trend_gate_max_wait_sec=trend_gate_max_wait_sec)
    # 30 iul: tradeall._fire_order foloseste acum mkt.place(symbol, side, price, qty, ...)
    # (proxy unic), nu po.place_order_smart(side, symbol, ...). Stub-uim ta.mkt.place cu un
    # an adapter that swaps the order (symbol,side)->(side,symbol) and calls the simulated broker
    # -> it does NOT touch the network. (We also patch ta.po.place_order_smart as a safety net for
    # orice cale reziduala.)
    if kalman_primary:
        # PRIMARY KALMAN MODE: the old model only LOGS (its orders are not
        # se executa); broker-ul e condus exclusiv de tranzitiile Kalman.
        _old_attempts = {"n": 0}
        def _journal_only(order_type, symbol_, price_, qty_=None, motivation=None, **kw):
            _old_attempts["n"] += 1
            return None
        ta.po.place_order_smart = _journal_only
        ta.mkt.place = lambda symbol_, side_, price_, qty_=None, **kw: _journal_only(
            side_, symbol_, price_, qty_, **kw)
    else:
        ta.po.place_order_smart = broker.place_order_smart           # stub — NU atinge reteaua
        ta.mkt.place = lambda symbol_, side_, price_, qty_=None, **kw: broker.place_order_smart(
            side_, symbol_, price_, qty_, **kw)
    ta.log_decision = make_decision_logger(out_dir, clock)            # redirect — it does NOT write into the live logs

    window_small = ta.PriceWindow(symbol, 300, sample_rate_sec=ta.TIME_SLEEP_GET_PRICE,
                                   window_seconds=ta.WINDOW_SECONDS_SMALL)
    window_big = ta.PriceWindow(symbol, 300, sample_rate_sec=ta.TIME_SLEEP_GET_PRICE,
                                 window_seconds=ta.WINDOW_SECONDS_BIG)
    analyzer_small = ta.WindowAnalyzer(window_small)
    analyzer_big = ta.WindowAnalyzer(window_big)
    trend_state = ta.TrendState(max_duration_seconds=2.5 * 60 * 60, expiration_trend_time=2.7 * 60,
                                 fresh_trend_time=3.7 * 60, now_fn=clock)
    trend_state_big = ta.TrendState(max_duration_seconds=3 * 60 * 60, expiration_trend_time=2.7 * 60,
                                     fresh_trend_time=3.7 * 60, now_fn=clock)

    # SHADOW (observational, plan 17 iul): aceleasi obiecte ca live, cu ceasul
    # simulat; jurnal FLAT in folderul run-ului (monitorul de backtest il deseneaza).
    import shadow_signals
    shadow = shadow_signals.ShadowSet(
        journal=shadow_signals.ShadowJournal(fixed_path=os.path.join(out_dir, "tradeall_shadow.log")))
    # KALMAN GATE si in backtest (paritate cu live), dar cu jurnalul de blocari
    # redirectionat in folderul run-ului — NICIODATA in order_outcomes live (A5).
    ta._shadow_ref = shadow
    def _bt_gate_log(symbol_, side, price_, qty, outcome, reason, motivation):
        cols = [clock(), symbol_, side, price_, qty, outcome, reason, "backtest", motivation]
        with open(broker.path, "a", encoding="utf-8") as f:
            f.write("|".join(_sanitize(c) for c in cols) + "\n")
    ta.GATE_OUTCOME_LOG = _bt_gate_log

    if source == "cache24":
        tick_source = load_ticks_cache24(symbol, start_ts, end_ts, filename=cache24_file)
    else:
        tick_source = load_ticks_history(symbol, start_ts, end_ts)

    prev_ts = None
    n = 0
    first_price = None
    prev_ktrend = 0
    with open(price_path, "a", encoding="utf-8") as price_f:
        for ts, price in tick_source:
            clock.ts = ts   # ceasul simulat = timpul tick-ului REPLAY-uit, nu ceasul real (A5)
            if speed == "real" and prev_ts is not None:
                time.sleep(max(0.0, ts - prev_ts))
            dt = ts - prev_ts if prev_ts is not None else None
            prev_ts = ts

            if dt and dt > 0:
                window_small.set_sample_rate(dt)
                window_big.set_sample_rate(dt)
            window_small.process_price(price)
            window_big.process_price(price)
            if trend_gate is not None:
                trend_gate.advance(symbol, ts, price)
                broker.process_pending(symbol, ts, price)   # executa ordine intarziate devenite favorabile
            price_f.write(f"{ts}|{symbol}|{price}\n")

            if threshold_provider is not None:
                thr_small, thr_big = threshold_provider(window_small, window_big)
            else:
                thr_small, thr_big = ta.PRICE_CHANGE_THRESHOLD_EUR, ta.PRICE_CHANGE_THRESHOLD_BIG_EUR

            slope, _pos = analyzer_small.check_price_change(thr_small)
            gradient, _gc, _sf, _gr = window_small.get_instant_trend()
            ta.logic_small("SMALL", True, symbol, gradient, slope, trend_state, price)

            slope_big, _price_diff = analyzer_big.check_price_change(thr_big)
            ta.logic("BIG", True, symbol, gradient, slope_big, trend_state_big, price)

            # SHADOW: acelasi apel ca in TrendCoordinator.evaluate live, cu ceas simulat
            try:
                shadow_fields = shadow.update(symbol, ts, price,
                                               epsilon=window_small.get_noise_epsilon(),
                                               big_prices=list(window_big.prices),
                                               big_sample_rate=window_big.sample_rate_sec)
            except Exception:
                shadow_fields = {}

            if first_price is None:
                first_price = price
            # 23 iul: broker.last_price actualizat NECONDITIONAT, la fiecare tick — nu
            # only in kalman_primary mode. Bug found today: without it, mark_to_market()
            # uses the price of the LAST TRADE (not the current market price) for any
            # pozitie ramasa DESCHISA la final in modul normal (model_actual) — un BUY
            # la 100 urmat de o urcare la 150 fara alt trade raporta mark_to_market=$0
            # in loc de $50 (verificat izolat: net_total -$0.10 vs +$49.90 corect).
            broker.last_price = price
            if kalman_primary:
                ktrend = shadow_fields.get("kalman_trend", prev_ktrend)
                if ktrend != prev_ktrend:
                    if ktrend == 1:
                        broker.place_order_smart("BUY", symbol, price, BACKTEST_NOTIONAL_USD / price,
                                                  motivation="kalman_up")
                    elif ktrend == -1:
                        broker.sell_all(symbol, price, motivation="kalman_down")
                    prev_ktrend = ktrend

            n += 1
            if n % 100 == 0:
                # Starea analizei SIMULATE — cititita de tradeall_observe.py (hover pe grafic),
                # acelasi continut ca cache_instant_trend.json in live. La 100 tick-uri, nu
                # per-tick (I/O ieftin chiar si in fast-forward).
                try:
                    state = {symbol: {
                        "current_price": price, "final_trend": gradient,
                        "gradient_recent": _gr, "slope_small": slope, "slope_big": slope_big,
                        "epsilon": window_small.get_noise_epsilon(), "ts": ts,
                        **shadow_fields,
                    }}
                    with open(os.path.join(out_dir, "analysis_state.json"), "w", encoding="utf-8") as sf:
                        json.dump(state, sf)
                except Exception:
                    pass
            if n % 5000 == 0:
                # sys.stderr.write, nu print(): in modul --quiet, print() e suprimat global
                # (log.disable_print()) — mesajele NOASTRE de progres tot trebuie sa se vada.
                sys.stderr.write(f"[tradeall_backtest] {n} tick-uri, ultimul {datetime.fromtimestamp(ts)} "
                                 f"(BUY {broker.n_buy} / SELL {broker.n_sell})\n")

    pnl = broker.pnl_summary()
    if first_price and broker.last_price:
        # benchmark: buy&hold pe aceeasi cantitate standard, acelasi interval
        bh_qty = BACKTEST_NOTIONAL_USD / first_price if first_price else 0
        pnl["buy_hold_net"] = round((broker.last_price - first_price) * bh_qty
                                     - 2 * bh_qty * first_price * FEE_PCT / 100, 2)
    pnl["mode"] = "kalman_primary" if kalman_primary else "model_actual"
    try:
        with open(os.path.join(out_dir, "pnl.json"), "w", encoding="utf-8") as pf:
            json.dump(pnl, pf, indent=1)
    except OSError:
        pass
    sys.stderr.write(f"[tradeall_backtest] P&L: {pnl}\n")
    sys.stderr.write(f"[tradeall_backtest] GATA: {n} tick-uri, BUY={broker.n_buy} SELL={broker.n_sell}\n")
    sys.stderr.write(f"[tradeall_backtest] rezultate in: {out_dir}\n")
    sys.stderr.write(f"[tradeall_backtest] vizualizare: "
                      f"./tradeall_observe.py --backtest-dir {out_dir} --symbols {symbol}\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbol", required=True)
    p.add_argument("--start", required=True, help="YYYY-MM-DD — data de start a simularii")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (implicit: pana la capatul datelor salvate)")
    p.add_argument("--speed", choices=["real", "fast"], default="fast",
                   help="real = respecta intervalele istorice; fast = fara asteptare (implicit)")
    p.add_argument("--run-id", default=None, help="implicit: <symbol>_<start>_<timestamp>")
    p.add_argument("--source", choices=["history", "cache24"], default="history",
                   help="history = cache_price_*.jsonl (~11 months, dar RAR, vezi caveat in plan); "
                        "cache24 = cache_24price_*.json, rezolutie DENSA (~1s) dar doar ce a retinut "
                        "that cache (by default the last ~24h, or a file with long retention if "
                        "ruleaza deja tradeall_price_archiver.py)")
    p.add_argument("--cache24-file", default=None,
                   help="cale explicita catre un fisier cache24 (ex. cache_24price_long_BTCUSDC.jsonl); "
                        "implicit: cachedb/cache_24price_<symbol>.json")
    p.add_argument("--quiet", action="store_true",
                   help="suprima print()-urile zgomotoase ale tradeall.logic() (mult mai rapid pe "
                        "date dense/lungi); mesajele de progres proprii raman vizibile")
    p.add_argument("--kalman-primary", action="store_true",
                   help="Kalman conduce (BUY la ->UP, SELL tot la ->DOWN); modelul vechi doar "
                        "jurnalizeaza. Pentru A/B pe P&L fata de rularea normala.")
    p.add_argument("--trend-gate-timeout-sec", type=float, default=None,
                   help="29 iul: gate 'asteapta un pret mai bun' pt BUY/SELL (fidel live: "
                        "INTARZIE pana la tick favorabil, NU blocheaza). Fereastra SCURTA "
                        "dinamica de trend. Implicit None = OFF (comportament vechi).")
    p.add_argument("--trend-gate-max-wait-sec", type=float, default=3600.0,
                   help="cat asteapta max un ordin intarziat inainte sa se plaseze oricum "
                        "(implicit 3600 = valoarea live).")
    args = p.parse_args()

    start_ts = datetime.strptime(args.start, "%Y-%m-%d").timestamp()
    end_ts = datetime.strptime(args.end, "%Y-%m-%d").timestamp() if args.end else None
    run_id = args.run_id or f"{args.symbol}_{args.start}_{int(time.time())}"

    run_backtest(args.symbol, start_ts, end_ts, args.speed, run_id, args.source, args.cache24_file,
                 args.quiet, kalman_primary=args.kalman_primary,
                 trend_gate_timeout_sec=args.trend_gate_timeout_sec,
                 trend_gate_max_wait_sec=args.trend_gate_max_wait_sec)


if __name__ == "__main__":
    main()
