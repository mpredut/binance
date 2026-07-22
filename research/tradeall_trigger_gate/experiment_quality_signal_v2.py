#!/usr/bin/env python3
"""
Experiment 6 (izolat, NU modifica tradeall.py) — continuarea Experimentului 5,
pe cererea userului: "hai sa facem 3 [repara defectul de cooldown] dar cu
mentiunea ca tb partial limitat. sa testez pe mult mai multe zile din nou?"

Doua schimbari fata de Experimentul 5:

1. COOLDOWN REPARAT (limitat, nu nelimitat): Experimentul 5 a aratat ca
   "reincearca doar daca nu a fost inca CONFIRMAT" e insuficient — fara pozitie
   de vandut, un SELL respins se reincerca la FIECARE tick (16683/3962
   incercari inutile intr-o saptamana). Fix: adaugat MIN_RETRY_INTERVAL_SEC
   intre incercari BLOCATE (nu si intre cele confirmate — o executie reusita
   tot blocheaza pana la schimbarea de regim, ca inainte). Practic: "incearca,
   daca esueaza asteapta macar X minute inainte sa reincerci", nu "niciodata"
   si nu "la fiecare tick".

2. ISTORIC MULT MAI LUNG: in loc de arhiva densa de 7 zile (cache24, ~1s/tick),
   folosim istoricul SPARS (`cache_price_{symbol}.jsonl`, ~7 min/tick, 329 de
   zile disponibile — verificat: 2025-08-27 -> azi). Semnalul testat (regresie
   pe 12-24h, recalculata rar) nu are nevoie de rezolutie de secunda — 7 min e
   suficient de fin pt o fereastra de ore. Scop: un singur test de 7 zile poate
   fi noroc/ghinion; 329 de zile acopera multe regimuri diferite de piata.

Variante (pe BTC si TAO, istoric COMPLET disponibil):
  V7_trend24h_reeval30min_retrylimit  : fereastra 24h, reevaluare 30 min (ca Exp5)
  V7_trend12h_reeval15min_retrylimit  : fereastra 12h, reevaluare 15 min (mai rapid)

MIN_RETRY_INTERVAL_SEC = 1800 (30 min) pt incercarile blocate, pe ambele variante.
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

MIN_RETRY_INTERVAL_SEC = 1800.0   # 30 min intre incercari BLOCATE (nu si intre cele confirmate)


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
        self.last_attempt_up_ts = None
        self.last_attempt_down_ts = None
        self.stats = STATS[tag]

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


def _make_stats(tag):
    return STATS.setdefault(tag, {
        "sign_changes": 0,
        "fire_attempts_up": 0, "fire_confirmed_up": 0, "fire_blocked_up": 0, "fire_skipped_cooldown_up": 0,
        "fire_attempts_down": 0, "fire_confirmed_down": 0, "fire_blocked_down": 0, "fire_skipped_cooldown_down": 0,
    })


_trackers = {}


def make_quality_logic(tag):
    def logic_variant(win, enable, symbol, gradient, slope, trend_state, current_price):
        tr = _trackers.get((tag, symbol))
        ts = trend_state._now()
        sign = tr.update(ts, current_price)
        stats = tr.stats

        if sign > 0 and not tr.fired_up:
            can_retry = (tr.last_attempt_up_ts is None
                         or (ts - tr.last_attempt_up_ts) >= MIN_RETRY_INTERVAL_SEC)
            if not can_retry:
                stats["fire_skipped_cooldown_up"] += 1
            else:
                tr.last_attempt_up_ts = ts
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
            can_retry = (tr.last_attempt_down_ts is None
                         or (ts - tr.last_attempt_down_ts) >= MIN_RETRY_INTERVAL_SEC)
            if not can_retry:
                stats["fire_skipped_cooldown_down"] += 1
            else:
                tr.last_attempt_down_ts = ts
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
    _make_stats(tag)
    _trackers[(tag, symbol)] = LongTrendTracker(tag, window_hours, reeval_sec)
    ta.logic = make_quality_logic(tag)
    tb.ta.logic = ta.logic

    run_id = f"experiment6_{tag}_{symbol}"
    import shutil
    out_dir = os.path.join(ROOT, "logger", "backtest", run_id)
    shutil.rmtree(out_dir, ignore_errors=True)

    t0 = time.time()
    tb.run_backtest(symbol, start_ts, end_ts, "fast", run_id, "history",
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

    hist_start = datetime.strptime("2025-08-27", "%Y-%m-%d").timestamp()

    for symbol in ("BTCUSDC", "TAOUSDC"):
        run_variant("V7_trend24h_reeval30min_retrylimit", 24, 1800, symbol, hist_start, None)
        run_variant("V7_trend12h_reeval15min_retrylimit", 12, 900, symbol, hist_start, None)

    sys.stderr.write("\n\n===== TOATE RULARILE TERMINATE =====\n")
    for tag, st in STATS.items():
        sys.stderr.write(f"{tag}: {st}\n")
