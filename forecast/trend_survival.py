#!/usr/bin/env python3
"""
trend_survival.py — EMPIRICAL analysis of trend durations using a survival curve.

Validate Marius's hypothesis that a trend reaching the Gaussian midpoint tends to
continue well beyond it, corresponding to decreasing hazard (the Lindy effect):
P(trend lasts another day | it already lasted t days) does not fall after midlife.

Extract historical trend episodes with the SAME definition as the production detector:
time-window slopes, eight-hour steps, noise tolerance 2, and at least three confirmed
blocks. Measure duration distribution (median, P75, P90) and conditional continuation
P_cont(t) = P(duration > t+1 day | duration > t).

  python3 trend_survival.py --symbol BTCUSDC --days 700
  python3 trend_survival.py --symbol TAOUSDC --days 400 --window 16
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

import numpy as np


def fetch_klines(symbol: str, days: int, interval: str = "1h") -> tuple[np.ndarray, np.ndarray]:
    """Return paginated Binance history with 1,000 candles per request."""
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
    """Return slope signs for each [t-window, t] window stepped by step_h."""
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
    """Return trend episodes in hours using the detector's semantics."""
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
        # Resume at the first block after the latest confirmation; noise belongs to
        # the following episode.
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
    """Compare YOUNG (t < mid) and OLD (t >= mid) trend continuation."""
    young = [p for t, p in cont.items() if t < mid]
    old = [p for t, p in cont.items() if t >= mid]
    if not young or not old:
        return "date insuficiente"
    ym, om = float(np.mean(young)), float(np.mean(old))
    print(f"    continuare medie: TANAR(<{mid:.0f}z)={ym:.2f}  BATRAN(>={mid:.0f}z)={om:.2f}")
    if om >= ym - 0.05:
        return "VALIDAT: trendul batran continua la fel de probabil ca la mijloc (Lindy) — plafonare dupa varf justificata"
    return "INVALIDAT: trendurile batrane mor mai repede — caderea gaussienei e justificata"


# ---------------------------------------------------------------------------
# EMPIRICAL + HYBRID estimation of horizon T instead of hard-coded T=14
# ---------------------------------------------------------------------------
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # forecast/ -> repository root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from state_io import atomic_write_json  # noqa: E402
T_CACHE_FILE = os.path.join(_ROOT, "cachedb", "cache_T_trend.json")


def hybrid_T(dur_hours, prior_T=14.0, k=30.0, t_min=4, t_max=30) -> dict:
    """Return hybrid T by combining favored empirical data with the prior.

    Calibrate the Gaussian peak T/2 to a TYPICAL trend age. Set T_emp to
    max(P90, 2*median) of real durations: the peak approximates the median and the end
    of Zone 1 approximates P90, leaving the persistent top decile in Zone 2.

    Hybrid formula: T = w*T_emp + (1-w)*prior, w=n/(n+k). Few episodes remain near
    the prior; empirical evidence dominates at n>=100.
    """
    n = len(dur_hours)
    if n == 0:
        return {"T": int(round(prior_T)), "n": 0, "w": 0.0,
                "median_d": None, "p90_d": None, "T_emp": None}
    d = np.asarray(dur_hours, dtype=float) / 24.0
    med, p90 = float(np.median(d)), float(np.percentile(d, 90))
    t_emp = max(p90, 2.0 * med)
    w = n / (n + k)
    T = min(max(w * t_emp + (1 - w) * prior_T, t_min), t_max)
    return {"T": int(round(T)), "n": n, "w": round(w, 2),
            "median_d": round(med, 1), "p90_d": round(p90, 1), "T_emp": round(t_emp, 1)}


def estimate_T(symbol: str, days: int = 540, window_h: int = 24, step_h: int = 8,
               prior_T: float = 14.0, ttl_days: float = 7.0) -> dict:
    """Estimate a coin-specialized T empirically from a symbol's history.
    Blend with the prior and cache on disk until ttl_days. Fall back to stale cache or
    the prior when network/data is unavailable."""
    cache = {}
    try:
        with open(T_CACHE_FILE) as f:
            cache = json.load(f)
    except (OSError, ValueError):
        pass
    ent = cache.get(symbol)
    if ent and time.time() - ent.get("ts", 0) < ttl_days * 86400:
        return ent

    # Use only the explicitly requested USDC data pair.
    candidates = [symbol]
    ts = px = None
    used = None
    for sym in candidates:
        try:
            ts, px = fetch_klines(sym, days)
            if len(ts) >= 100:
                used = sym
                break
        except Exception:  # noqa: BLE001
            continue
    if used is None:
        if ent:
            return ent                                  # stale cache is better than no estimate
        return {"T": int(round(prior_T)), "n": 0, "w": 0.0, "source_symbol": None,
                "median_d": None, "p90_d": None, "T_emp": None, "ts": 0}

    bt, bs = block_slopes(ts, px, window_h, step_h)
    eps = episodes(bt, bs, window_h)
    durs = [e["dur_h"] for e in eps]
    res = hybrid_T(durs, prior_T=prior_T)
    # Per-coin empirical continuation curve: P(last one more day | already lasted t days).
    # Retain the remaining survival curve for future weighting.
    d = np.asarray(durs, dtype=float)
    p_cont = {}
    for t_days in range(1, 15):
        alive = d > t_days * 24.0
        if alive.sum() < 10:
            break
        p_cont[str(t_days)] = round(float((d > (t_days + 1) * 24.0).sum() / alive.sum()), 3)
    res["p_cont"] = p_cont
    res["source_symbol"] = used
    res["ts"] = time.time()
    cache[symbol] = res
    try:
        atomic_write_json(T_CACHE_FILE, cache, indent=2)
    except OSError:
        pass
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Supravietuirea empirica a trendurilor.")
    ap.add_argument("--symbol", default="BTCUSDC")
    ap.add_argument("--days", type=int, default=700)
    ap.add_argument("--window", type=int, default=24, help="ore/fereastra (productie: 16)")
    ap.add_argument("--step", type=int, default=8)
    ap.add_argument("--T", type=float, default=14.0, help="orizontul gaussienei (zile), mid=T/2")
    ap.add_argument("--estimate", action="store_true",
                    help="only estimate T (empirical+hybrid, cached) and exit")
    args = ap.parse_args()

    if args.estimate:
        est = estimate_T(args.symbol, days=args.days, window_h=args.window,
                         step_h=args.step, prior_T=args.T)
        print(f"[{args.symbol}] T estimat = {est['T']} zile  "
              f"(empiric {est.get('T_emp')}z din n={est['n']} episoade, pondere empiric w={est['w']}, "
              f"mediana {est.get('median_d')}z, P90 {est.get('p90_d')}z, sursa {est.get('source_symbol')})")
        return 0

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
