#!/usr/bin/env python3
"""
backtest_adaptive.py — test de DECIZIE (nu doar statistic): pragul de DCA fix (--drop,
azi 2.0% pe HYPEUSD) vs prag adaptiv pe volatilitate (K_DCA * vol_1h istoric — codul
DEJA scris in shadow_signals.py, dar inca doar LOG in kraken/strategy.py, niciodata
promovat) vs prag adaptiv pe volatilitate PREZISA de Chronos (zero-shot, vezi
forecast/vol_chronos.py — a batut persistenta pe MAE si directie pe date reale).

De ce exista scriptul asta: intrebarea care conteaza nu e "e predictia statistic mai
buna decat baseline-ul" (deja raspuns: da, modest), ci "ar fi schimbat ceva in bani REALI
daca il foloseam la reintrare/DCA". Refolosim EXACT motorul de simulare (DCA/TP/SL/fee/
buget) din backtest.py, schimband DOAR sursa pragului de drop — ca sa izolam efectul.

Chronos ruleaza la cadenta REDUSA (--refresh ore, implicit 6h) — asa ar rula si live,
intr-un proces separat (nu in kraken_bot), tocmai ca sa nu incarce memoria masinii care
tine bot-ii reali (3.8GB RAM, deja aproape de swap — vezi nota din sesiune).

  python3 backtest_adaptive.py --pair HYPEUSD --interval 60
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import fetch_candles  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "forecast"))
sys.path.insert(0, _ROOT)
import shadow_signals as ss  # noqa: E402  (K_DCA, vol_1h_pct — refolosim FORMULA exacta)

WIN = 24            # ore trailing pt volatilitatea realizata (acelasi WIN ca in vol_chronos.py)


def trailing_vol_series(closes: list[float]) -> np.ndarray:
    """vol_1h_pct la fiecare bara, folosind DOAR trecutul (cauzal) — formula exacta
    din shadow_signals.vol_1h_pct (sample_rate_sec=3600, bare de 1h)."""
    out = np.full(len(closes), np.nan)
    for i in range(WIN, len(closes)):
        window = closes[i - WIN:i + 1]
        v = ss.vol_1h_pct(window, sample_rate_sec=3600.0)
        out[i] = v if v is not None else np.nan
    return out


def chronos_forecast_series(vol_trail: np.ndarray, refresh: int, horizon: int) -> np.ndarray:
    """Prognoza Chronos a lui vol_trail, actualizata DOAR la fiecare `refresh` ore
    (cadenta redusa, ca intr-un daemon separat) — intre update-uri, ultima valoare
    prezisa ramane (exact ca un fisier de semnal citit de bot intre doua rulari)."""
    from chronos import ChronosPipeline
    import torch
    print("  incarc amazon/chronos-t5-tiny...")
    pipe = ChronosPipeline.from_pretrained("amazon/chronos-t5-tiny", device_map="cpu",
                                            torch_dtype=torch.float32)
    out = np.full(len(vol_trail), np.nan)
    last = None
    for i in range(len(vol_trail)):
        if np.isnan(vol_trail[i]):
            continue
        if last is None or i % refresh == 0:
            ctx = vol_trail[max(0, i - 511):i + 1]
            ctx = ctx[~np.isnan(ctx)]
            if len(ctx) < 8:
                continue
            fc = pipe.predict([torch.tensor(ctx, dtype=torch.float32)], prediction_length=horizon)
            last = float(np.median(fc[0].numpy()))
        out[i] = last
    return out


def simulate_variant(ohlc, P, drop_arr):
    """Identic cu backtest.simulate(), dar `drop` e citit per-bara din drop_arr
    (in loc de o constanta) — restul strategiei NEATINS."""
    disc, tp, sl = P["disc"]/100, P["tp"]/100, P["sl"]/100
    fee = P["fee"]/100
    qty = cost = spent = 0.0
    dca = 0; last_open = None
    realized = fees = 0.0
    cycles = wins = 0
    peak = eq = 0.0; maxdd = 0.0
    rest_buy = None; rest_sell = None

    for i, (o, h, l, c) in enumerate(ohlc):
        drop = drop_arr[i]/100 if not np.isnan(drop_arr[i]) else P["drop_fallback"]/100
        if rest_buy:
            px, sz = rest_buy
            if l <= px:
                qty += sz; cost += sz*px; spent += sz*px; last_open = px
                if qty > sz + 1e-9:
                    dca += 1
                fees += fee*sz*px
                rest_buy = None; rest_sell = None
        if rest_sell and qty > 1e-9:
            px, sz = rest_sell
            if h >= px:
                avg = cost/qty
                realized += (px-avg)*sz; fees += fee*sz*px
                cycles += 1; wins += 1 if px > avg else 0
                qty = cost = spent = 0.0; dca = 0; last_open = None
                rest_sell = None; rest_buy = None
        if qty > 1e-9 and sl > 0:
            avg = cost/qty
            if (avg - c)/avg >= sl:
                realized += (c-avg)*qty; fees += fee*qty*c
                cycles += 1
                qty = cost = spent = 0.0; dca = 0; last_open = None
                rest_sell = None; rest_buy = None
        if qty <= 1e-9:
            if rest_buy is None and spent + P["entry"] <= P["budget"]:
                px = c*(1-disc); rest_buy = (px, round(P["entry"]/px, 8))
        else:
            avg = cost/qty
            rest_sell = (avg*(1+tp), qty)
            if (dca < P["maxdca"] and last_open and c <= last_open*(1-drop)
                    and spent + P["dca"] <= P["budget"] and rest_buy is None):
                px = c*(1-disc); rest_buy = (px, round(P["dca"]/px, 8))
        upnl = (c - cost/qty)*qty if qty > 1e-9 else 0
        eq = realized - fees + upnl; peak = max(peak, eq); maxdd = max(maxdd, peak - eq)

    final_upnl = (ohlc[-1][3] - cost/qty)*qty if qty > 1e-9 else 0.0
    return {"realized": realized, "fees": fees, "net": realized - fees,
            "total": realized - fees + final_upnl, "final_upnl": final_upnl,
            "cycles": cycles, "wins": wins, "maxdd": maxdd, "open_qty": qty}


def report(name, m, budget):
    tot = m["total"]/budget*100
    wr = 100*m["wins"]/m["cycles"] if m["cycles"] else 0
    print(f"  [{name:16s}] TOTAL {tot:+7.2f}%  realizat ${m['realized']:+8.2f}  "
          f"cicluri={m['cycles']:<3} win-rate={wr:3.0f}%  maxDD=${m['maxdd']:.2f}  "
          f"pozitie deschisa={m['open_qty']:.4f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Fix vs adaptiv-shadow vs adaptiv-Chronos, pe DCA real.")
    ap.add_argument("--pair", default="HYPEUSD")
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--entry", type=float, default=100); ap.add_argument("--dca", type=float, default=50)
    ap.add_argument("--disc", type=float, default=0.2); ap.add_argument("--drop", type=float, default=2.0,
                    help="pragul FIX actual, live (baseline de batut)")
    ap.add_argument("--tp", type=float, default=1.9); ap.add_argument("--maxdca", type=int, default=10)
    ap.add_argument("--budget", type=float, default=1000); ap.add_argument("--fee", type=float, default=0.25)
    ap.add_argument("--sl", type=float, default=10.0)
    ap.add_argument("--refresh", type=int, default=6, help="ore intre update-uri Chronos (cadenta daemon)")
    ap.add_argument("--horizon", type=int, default=24)
    args = ap.parse_args()

    ohlc = fetch_candles(args.pair, args.interval)
    if len(ohlc) < 40:
        print(f"! prea putine date ({len(ohlc)})"); return 1
    closes = [x[3] for x in ohlc]
    bh = (closes[-1] - closes[0]) / closes[0] * 100
    print(f"=== {args.pair} interval={args.interval}m ({len(ohlc)} bare) — buy&hold {bh:+.1f}% ===")

    vol_trail = trailing_vol_series(closes)
    print(f"  vol_1h_pct trailing: disponibil din bara {WIN} (din {len(closes)})")
    chronos_vol = chronos_forecast_series(vol_trail, args.refresh, args.horizon)

    base = dict(entry=args.entry, dca=args.dca, disc=args.disc, tp=args.tp,
                maxdca=args.maxdca, budget=args.budget, fee=args.fee, sl=args.sl,
                drop_fallback=args.drop)

    fixed_arr = np.full(len(ohlc), args.drop)
    shadow_arr = np.array([ss.K_DCA * v if not np.isnan(v) else np.nan for v in vol_trail])
    chronos_arr = np.array([ss.K_DCA * v if not np.isnan(v) else np.nan for v in chronos_vol])

    m_fixed = simulate_variant(ohlc, base, fixed_arr)
    m_shadow = simulate_variant(ohlc, base, shadow_arr)
    m_chronos = simulate_variant(ohlc, base, chronos_arr)

    print(f"  (K_DCA={ss.K_DCA}, prag fix={args.drop}%, medie adaptiv-shadow="
          f"{np.nanmean(shadow_arr):.2f}%, medie adaptiv-chronos={np.nanmean(chronos_arr):.2f}%)")
    report("FIX (live azi)", m_fixed, args.budget)
    report("adaptiv-shadow", m_shadow, args.budget)
    report("adaptiv-chronos", m_chronos, args.budget)
    return 0


if __name__ == "__main__":
    sys.exit(main())
