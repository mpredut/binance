#!/usr/bin/env python3
"""
tradeall_backtest.py — reia istoricul de pret deja salvat
(cachedb/cache_price_{symbol}.jsonl, ~11 luni) prin EXACT acelasi model de
decizie ca tradeall.py (PriceWindow / WindowAnalyzer / TrendState / logic /
logic_small), cu place_order_smart inlocuit de un stub care simuleaza
executia in loc sa bata reteaua Binance. Scrie DOAR intr-un folder separat
logger/backtest/<run_id>/ — NICIODATA in logurile live folosite de
tradeall.py real (vezi plan A5).

Rulare:
    ./tradeall_backtest.py --symbol BTCUSDC --start 2026-06-01 --speed fast
    ./tradeall_backtest.py --symbol BTCUSDC --start 2026-06-01 --end 2026-06-08 --speed real

Vizualizare (in timp ce ruleaza sau dupa): intr-un alt terminal,
    ./tradeall_observe.py --backtest-dir logger/backtest/<run_id> --symbols BTCUSDC
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import tradeall as ta  # reutilizam PriceWindow/WindowAnalyzer/TrendState/logic (A5) — nu reimplementam modelul


def _sanitize(value):
    return str(value).replace("|", "/").replace("\n", " ") if value is not None else ""


class _SimClock:
    """Timpul SIMULAT (al tick-ului replay-uit curent), NU ceasul real —
    pasat catre TrendState(now_fn=...) ca fast-forward sa fie corect (A5)."""
    def __init__(self):
        self.ts = time.time()

    def __call__(self):
        return self.ts


FEE_PCT = 0.1   # comision spot Binance ~0.1% per leg (taker)
# marime standard de pozitie pt simulare (kalman-primary + benchmark buy&hold) —
# 21 iul: inlocuieste ta.api.quantities[symbol] (eliminat din bapi.py, era doar
# un placeholder mereu taiat de weight-limit in live, dar aici chiar avem nevoie
# de un NUMAR REAL, fix, ca sa comparam onest intre variante pe acelasi volum.
BACKTEST_NOTIONAL_USD = 1000.0


class BacktestBroker:
    """Stub pentru po.place_order_smart: simuleaza executia (fara retea),
    scrie in propriul folder de backtest, acelasi format pipe ca order_outcomes
    live (Pas A2) — asa incat tradeall_observe.py sa il poata randa identic.
    Tine si CONTABILITATE P&L: pozitie, cost mediu, realizat, comisioane."""
    def __init__(self, out_dir, clock):
        self.clock = clock
        self.path = os.path.join(out_dir, "order_outcomes.log")
        self.n_buy = self.n_sell = 0
        self.pos_qty = 0.0
        self.pos_cost = 0.0       # cost total al pozitiei curente (fara fee)
        self.realized = 0.0       # profit/pierdere realizata (fara fee)
        self.fees = 0.0
        self.last_price = None

    def place_order_smart(self, order_type, symbol, price, qty, motivation=None, **kwargs):
        price = float(price); qty = float(qty)
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
                "net_total": round(self.realized + m2m - self.fees, 2)}


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
        raise FileNotFoundError(f"Nu gasesc {path} (istoricul de pret pentru {symbol})")
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
    """Citeste cache_24price_{symbol}.json (sau un cache24 cu retentie lunga,
    daca filename e dat) — rezolutie DENSA (~1s/tick, ca live), dar limitata
    la ce a retinut acel cache (implicit doar ultimele ~24h)."""
    path = filename or os.path.join(ROOT, "cachedb", f"cache_24price_{symbol}.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Nu gasesc {path} (cache24 pentru {symbol})")
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
                 kalman_primary=False):
    out_dir = os.path.join(ROOT, "logger", "backtest", run_id)
    os.makedirs(out_dir, exist_ok=True)
    price_path = os.path.join(out_dir, "tradeall_price_samples.log")

    if quiet:
        # tradeall.logic()/check_price_change() fac print() la fiecare tick, oglindit si pe
        # disc (log.py) — pe date DENSE (cache24, zeci de mii de tick-uri) asta domina timpul
        # de rulare. log.disable_print() suprima global print() (mesajele NOASTRE folosesc
        # sys.stderr.write, care ramane vizibil).
        ta.log.disable_print()

    clock = _SimClock()
    broker = BacktestBroker(out_dir, clock)
    if kalman_primary:
        # MODUL KALMAN-PRIMAR: modelul vechi doar JURNALIZEAZA (ordinele lui nu
        # se executa); broker-ul e condus exclusiv de tranzitiile Kalman.
        _old_attempts = {"n": 0}
        def _journal_only(order_type, symbol_, price_, qty_, motivation=None, **kw):
            _old_attempts["n"] += 1
            return None
        ta.po.place_order_smart = _journal_only
    else:
        ta.po.place_order_smart = broker.place_order_smart           # stub — NU atinge reteaua
    ta.log_decision = make_decision_logger(out_dir, clock)            # redirect — NU scrie in logurile live

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
            price_f.write(f"{ts}|{symbol}|{price}\n")

            slope, _pos = analyzer_small.check_price_change(ta.PRICE_CHANGE_THRESHOLD_EUR)
            gradient, _gc, _sf, _gr = window_small.get_instant_trend()
            ta.logic_small("SMALL", True, symbol, gradient, slope, trend_state, price)

            slope_big, _price_diff = analyzer_big.check_price_change(ta.PRICE_CHANGE_THRESHOLD_BIG_EUR)
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
            if kalman_primary:
                ktrend = shadow_fields.get("kalman_trend", prev_ktrend)
                if ktrend != prev_ktrend:
                    if ktrend == 1:
                        broker.place_order_smart("BUY", symbol, price, BACKTEST_NOTIONAL_USD / price,
                                                  motivation="kalman_up")
                    elif ktrend == -1:
                        broker.sell_all(symbol, price, motivation="kalman_down")
                    prev_ktrend = ktrend
                broker.last_price = price

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
                   help="history = cache_price_*.jsonl (~11 luni, dar RAR, vezi caveat in plan); "
                        "cache24 = cache_24price_*.json, rezolutie DENSA (~1s) dar doar ce a retinut "
                        "acel cache (implicit ultimele ~24h, sau un fisier cu retentie lunga daca "
                        "ruleaza deja tradeall_price_archiver.py)")
    p.add_argument("--cache24-file", default=None,
                   help="cale explicita catre un fisier cache24 (ex. cache_24price_long_BTCUSDC.json); "
                        "implicit: cachedb/cache_24price_<symbol>.json")
    p.add_argument("--quiet", action="store_true",
                   help="suprima print()-urile zgomotoase ale tradeall.logic() (mult mai rapid pe "
                        "date dense/lungi); mesajele de progres proprii raman vizibile")
    p.add_argument("--kalman-primary", action="store_true",
                   help="Kalman conduce (BUY la ->UP, SELL tot la ->DOWN); modelul vechi doar "
                        "jurnalizeaza. Pentru A/B pe P&L fata de rularea normala.")
    args = p.parse_args()

    start_ts = datetime.strptime(args.start, "%Y-%m-%d").timestamp()
    end_ts = datetime.strptime(args.end, "%Y-%m-%d").timestamp() if args.end else None
    run_id = args.run_id or f"{args.symbol}_{args.start}_{int(time.time())}"

    run_backtest(args.symbol, start_ts, end_ts, args.speed, run_id, args.source, args.cache24_file,
                 args.quiet, kalman_primary=args.kalman_primary)


if __name__ == "__main__":
    main()
