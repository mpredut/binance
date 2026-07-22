#!/usr/bin/env python3
"""
Experiment 5 (izolat, NU modifica tradeall.py) — cerere user, dupa Experimentele
1-4: "daca sunt pe un trend general mai mare de crestere si cumpar, ar trebui
sa fiu in castig, nu? testeaza asta."

Experimentele 1-4 au aratat ca gradient/slope_big/gradient_big sunt toate
regresii pe ferestre de MINUTE-ORE, recalculate la FIECARE TICK — deci
zgomotoase la aceeasi scara de timp, oricat de "mare" suna fereastra (2.5h).
Niciunul nu era un trend GENUIN de lunga durata.

Testam aici un semnal calitativ DIFERIT:
  - LongTrendTracker: regresie liniara pe o fereastra de `window_hours` ore
    (implicit 24h), dar RECALCULATA doar o data la `reeval_sec` (implicit
    1800s = 30 min), nu la fiecare tick. Asta il face STABIL ore intregi.
  - Cooldown pe EXECUTIE CONFIRMATA (nu pe incercare): "deja am actionat in
    acest regim" se seteaza DOAR daca _fire_order intoarce un rezultat real
    (ordin plasat), nu doar pentru ca a fost apelat. Daca o incercare e
    respinsa (gate Kalman, in live ar fi si weight-limit/buget), NU blocam
    reincercari viitoare — exact cerinta user din discutia anterioara.

Reguli de trading: BUY cand semnalul e pozitiv (SI n-am mai executat cu succes
in acest regim), SELL cand e negativ (simetric). Fara TrendState-ul complex
din tradeall.py (confirm_count/expirare) — acela era parte din problema
(vezi Experimentele 1/3), inlocuit complet aici cu logica de regim de mai sus.

Caveat onest: fara pozitie existenta, un SELL respins de broker (spot, nimic
de vandut) NU se marcheaza ca "reincercare blocata" separat — va reincerca la
fiecare tick pana cand semnul se schimba SAU apare o pozitie (dintr-un BUY
anterior). In acest backtest reincercarile respinse nu costa comision (doar
in productie ar consuma weight-limit/timp API) — deci nu afecteaza P&L-ul aici,
dar merita mentionat ca simplificare fata de productie.
"""
import os
import sys
import time
from collections import deque

ROOT = "/home/predut/binance"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import tradeall_backtest as tb
import tradeall as ta

STATS = {}


class LongTrendTracker:
    def __init__(self, tag, window_hours, reeval_sec, min_points=30):
        self.window_sec = window_hours * 3600
        self.reeval_sec = reeval_sec
        self.min_points = min_points
        self.buf = deque()
        self.last_eval_ts = None
        self.sign = 0
        self.fired_up = False
        self.fired_down = False
        self.stats = STATS.setdefault(tag, {
            "sign_changes": 0,
            "fire_attempts_up": 0, "fire_confirmed_up": 0, "fire_blocked_up": 0,
            "fire_attempts_down": 0, "fire_confirmed_down": 0, "fire_blocked_down": 0,
        })

    def update(self, ts, price):
        self.buf.append((ts, price))
        cutoff = ts - self.window_sec
        while self.buf and self.buf[0][0] < cutoff:
            self.buf.popleft()
        if self.last_eval_ts is None or (ts - self.last_eval_ts) >= self.reeval_sec:
            self.last_eval_ts = ts
            if len(self.buf) >= self.min_points:
                prices = [p for _, p in self.buf]
                analyzer = ta.PriceTrendAnalyzer(prices)
                _, slope, _ = analyzer.linear_regression_trend()
                new_sign = 0
                if slope is not None:
                    new_sign = 1 if slope > 0 else (-1 if slope < 0 else 0)
                if new_sign != self.sign:
                    self.sign = new_sign
                    self.fired_up = False
                    self.fired_down = False
                    self.stats["sign_changes"] += 1
        return self.sign


_trackers = {}


def make_quality_logic(tag, window_hours, reeval_sec):
    def logic_variant(win, enable, symbol, gradient, slope, trend_state, current_price):
        tr = _trackers.get(symbol)
        if tr is None:
            tr = _trackers[symbol] = LongTrendTracker(tag, window_hours, reeval_sec)
        ts = trend_state._now()
        sign = tr.update(ts, current_price)
        stats = tr.stats

        if sign > 0 and not tr.fired_up:
            stats["fire_attempts_up"] += 1
            if enable:
                result = ta._fire_order(symbol, "BUY", current_price, f"{tag}_long_trend_up",
                                         safeback_seconds=14 * 24 * 3600 + 60, force=False,
                                         cancelorders=True, hours=1)
                if result is not None:
                    tr.fired_up = True
                    tr.fired_down = False
                    stats["fire_confirmed_up"] += 1
                else:
                    stats["fire_blocked_up"] += 1

        if sign < 0 and not tr.fired_down:
            stats["fire_attempts_down"] += 1
            if enable:
                result = ta._fire_order(symbol, "SELL", current_price, f"{tag}_long_trend_down",
                                         safeback_seconds=14 * 24 * 3600 + 60, force=False,
                                         cancelorders=True, hours=1)
                if result is not None:
                    tr.fired_down = True
                    tr.fired_up = False
                    stats["fire_confirmed_down"] += 1
                else:
                    stats["fire_blocked_down"] += 1

    return logic_variant


def run_variant(tag, window_hours, reeval_sec, symbol, start_ts, end_ts):
    _trackers.clear()
    ta.logic = make_quality_logic(tag, window_hours, reeval_sec)
    tb.ta.logic = ta.logic

    run_id = f"experiment5_{tag}_{symbol}"
    import shutil
    out_dir = os.path.join(ROOT, "logger", "backtest", run_id)
    shutil.rmtree(out_dir, ignore_errors=True)

    t0 = time.time()
    tb.run_backtest(symbol, start_ts, end_ts, "fast", run_id, "cache24",
                     cache24_file=os.path.join(ROOT, "cachedb", f"cache_24price_long_{symbol}.jsonl"),
                     quiet=True, kalman_primary=False)
    elapsed = time.time() - t0

    import json
    pnl_path = os.path.join(out_dir, "pnl.json")
    pnl = json.load(open(pnl_path)) if os.path.exists(pnl_path) else {}
    sys.stderr.write(f"\n=== {tag} / {symbol} === (wall {elapsed:.1f}s)\n")
    sys.stderr.write(f"stats: {STATS[tag]}\n")
    sys.stderr.write(f"pnl: {pnl}\n")
    return STATS[tag], pnl


if __name__ == "__main__":
    from datetime import datetime

    btc_start = datetime.strptime("2026-07-14", "%Y-%m-%d").timestamp()
    tao_start = datetime.strptime("2026-07-14 19:40:00", "%Y-%m-%d %H:%M:%S").timestamp()

    # 7 zile complete pt ambele simboluri — un semnal de 24h are nevoie de
    # suficient istoric ca sa arate mai mult de 1-2 schimbari de regim.
    run_variant("V6_trend24h_reeval30min", 24, 1800, "BTCUSDC", btc_start, None)
    run_variant("V6_trend24h_reeval30min", 24, 1800, "TAOUSDC", tao_start, None)
