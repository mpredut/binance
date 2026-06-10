#!/usr/bin/env python3
"""
trend_survival.py — analiza EMPIRICA a duratelor de trend (curba de supravietuire).

Valideaza ipoteza lui Marius: "un trend care a ajuns la jumatatea gaussienei
tinde sa continue mult in afara ei" = hazard descrescator (efect Lindy):
P(trendul mai tine o zi | a tinut deja t zile) NU scade dupa mijlocul vietii.

Extrage episoadele de trend din istoric cu ACEEASI definitie ca detectorul de
productie (pante pe ferestre de timp, pas 8h, toleranta la zgomot 2, minim 3
blocuri confirmate) si masoara:
  - distributia duratelor (mediana, P75, P90)
  - P_cont(t) = P(durata > t+1zi | durata > t)  — continuarea conditionata

  python3 trend_survival.py --symbol BTCUSDT --days 700
  python3 trend_survival.py --symbol TAOUSDT --days 400 --window 16
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

import numpy as np


def fetch_klines(symbol: str, days: int, interval: str = "1h") -> tuple[np.ndarray, np.ndarray]:
    """Istoric Binance paginat (1000 lumanari/cerere)."""
    end = int(time.time() * 1000)
    start = end - days * 86400 * 1000
    ts, px = [], []
    cur = start
    while cur < end:
        url = (f"https://api.binance.com/api/v3/klines?symbol={symbol}"
               f"&interval={interval}&startTime={cur}&limit=1000")
        rows = json.loads(urllib.request.urlopen(url, timeout=25).read())
        if not rows:
            break
        for r in rows:
            ts.append(r[0] / 1000.0)
            px.append(float(r[4]))
        cur = rows[-1][0] + 1
        if len(rows) < 1000:
            break
    return np.array(ts), np.array(px)


def block_slopes(ts, px, window_h, step_h):
    """Panta (semnul) pe fiecare fereastra [t-window, t], pas step_h. -> (t_end_bloc, semn)"""
    out_t, out_s = [], []
    w, s = window_h * 3600.0, step_h * 3600.0
    t = ts[0] + w
    while t <= ts[-1]:
        lo = int(np.searchsorted(ts, t - w, "left"))
        hi = int(np.searchsorted(ts, t, "right"))
        if hi - lo >= 5:
            sl, _ = np.polyfit(ts[lo:hi] - ts[lo], px[lo:hi], 1)
            out_t.append(t)
            out_s.append(1.0 if sl > 0 else -1.0)
        t += s
    return np.array(out_t), np.array(out_s)


def episodes(bt, bs, window_h, noise_tolerance=2, min_confirm=3):
    """Episoade de trend (aceeasi semantica cu detectorul): durate in ore."""
    eps = []
    i = 0
    n = len(bs)
    while i < n:
        sign = bs[i]
        start = bt[i] - window_h * 3600.0
        last_confirm = bt[i]
        confirms, noise = 1, 0
        j = i + 1
        while j < n:
            if bs[j] == sign:
                confirms += 1; noise = 0; last_confirm = bt[j]
            elif noise < noise_tolerance:
                noise += 1
            else:
                break
            j += 1
        if confirms >= min_confirm:
            eps.append({"dir": "up" if sign > 0 else "down",
                        "dur_h": (last_confirm - start) / 3600.0})
        # reluam de la primul bloc de dupa ultima confirmare (zgomotul apartine
        # episodului urmator)
        nxt = int(np.searchsorted(bt, last_confirm, "right"))
        i = max(nxt, i + 1)
    return eps


def survival_report(durs_h: list[float], label: str, t_grid_days, horizon_h=24.0):
    d = np.array(durs_h)
    print(f"--- {label}: {len(d)} episoade ---")
    if len(d) < 15:
        print("    (prea putine episoade pt concluzii)")
        return None
    print(f"    durate: mediana={np.median(d)/24:.1f}z  medie={d.mean()/24:.1f}z  "
          f"P75={np.percentile(d,75)/24:.1f}z  P90={np.percentile(d,90)/24:.1f}z  max={d.max()/24:.1f}z")
    cont = {}
    print(f"    P(mai tine inca 1 zi | a tinut t zile):")
    row = []
    for t_days in t_grid_days:
        t_h = t_days * 24.0
        alive = d > t_h
        if alive.sum() < 10:
            break
        p = float((d > t_h + horizon_h).sum() / alive.sum())
        cont[t_days] = p
        row.append(f"t={t_days}z:{p:.2f}(n={alive.sum()})")
    print("      " + "  ".join(row))
    return cont


def verdict(cont: dict, mid: float) -> str:
    """Compara continuarea TANAR (t < mid) vs BATRAN (t >= mid)."""
    young = [p for t, p in cont.items() if t < mid]
    old = [p for t, p in cont.items() if t >= mid]
    if not young or not old:
        return "date insuficiente"
    ym, om = float(np.mean(young)), float(np.mean(old))
    print(f"    continuare medie: TANAR(<{mid:.0f}z)={ym:.2f}  BATRAN(>={mid:.0f}z)={om:.2f}")
    if om >= ym - 0.05:
        return "VALIDAT: trendul batran continua la fel de probabil ca la mijloc (Lindy) — plafonare dupa varf justificata"
    return "INVALIDAT: trendurile batrane mor mai repede — caderea gaussienei e justificata"


def main() -> int:
    ap = argparse.ArgumentParser(description="Supravietuirea empirica a trendurilor.")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--days", type=int, default=700)
    ap.add_argument("--window", type=int, default=24, help="ore/fereastra (productie: 16)")
    ap.add_argument("--step", type=int, default=8)
    ap.add_argument("--T", type=float, default=14.0, help="orizontul gaussienei (zile), mid=T/2")
    args = ap.parse_args()

    ts, px = fetch_klines(args.symbol, args.days)
    if len(ts) < 100:
        print(f"! prea putine date pt {args.symbol}"); return 1
    print(f"=== {args.symbol}: {len(ts)} lumanari 1h ({(ts[-1]-ts[0])/86400:.0f} zile), "
          f"fereastra={args.window}h pas={args.step}h ===")
    bt, bs = block_slopes(ts, px, args.window, args.step)
    eps = episodes(bt, bs, args.window)
    t_grid = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 17, 21]

    all_d = [e["dur_h"] for e in eps]
    cont = survival_report(all_d, "TOATE trendurile", t_grid)
    if cont:
        print("    " + verdict(cont, args.T / 2))
    for direction in ("up", "down"):
        dd = [e["dur_h"] for e in eps if e["dir"] == direction]
        c = survival_report(dd, f"doar {direction.upper()}", t_grid)
        if c:
            print("    " + verdict(c, args.T / 2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
