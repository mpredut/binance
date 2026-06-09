#!/usr/bin/env python3
"""
backtest.py — backtester DCA + take-profit pentru Trading 212 (actiuni), pe date Yahoo.

Reia strategia bar-cu-bar (intrare la market-discount, DCA pe scadere, take-profit,
STOP-LOSS), cu taxa FX 0.15%/leg (~0.30% round-trip). Raport ONEST: TOTAL include
pozitia deschisa (mark-to-market), nu doar profitul realizat — ca sa nu te pacaleasca
"100% win-rate" cu o pozitie pierzatoare blocata.

  python3 backtest.py --sym NVDA --range 1y --interval 1d
  python3 backtest.py --mode sweep --sym NVDA --range 6mo --interval 1d
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request


def fetch_candles(sym, rng, interval):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (backtest)"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read())
    res = (data.get("chart", {}).get("result") or [None])[0]
    if not res:
        return []
    ts = res.get("timestamp") or []
    q = (res.get("indicators", {}).get("quote") or [{}])[0]
    out = []
    for i in range(len(ts)):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        out.append((o, h, l, c))
    return out


def simulate(ohlc, P):
    disc, drop, tp, sl = P["disc"]/100, P["drop"]/100, P["tp"]/100, P["sl"]/100
    fee = P["fee"]/100
    qty = cost = spent = 0.0
    dca = 0; last_open = None
    realized = fees = 0.0
    cycles = wins = 0
    peak = eq = 0.0; maxdd = 0.0
    rest_buy = None; rest_sell = None

    for (o, h, l, c) in ohlc:
        # 1. fill cumparare (limit sub piata -> fill daca bara coboara la el)
        if rest_buy:
            px, sz = rest_buy
            if l <= px:
                qty += sz; cost += sz*px; spent += sz*px; last_open = px
                if qty > sz + 1e-9:
                    dca += 1
                fees += fee*sz*px
                rest_buy = None; rest_sell = None
        # 2. fill vanzare TP (limit peste piata -> fill daca bara urca la el)
        if rest_sell and qty > 1e-9:
            px, sz = rest_sell
            if h >= px:
                avg = cost/qty
                realized += (px-avg)*sz; fees += fee*sz*px
                cycles += 1; wins += 1 if px > avg else 0
                qty = cost = spent = 0.0; dca = 0; last_open = None
                rest_sell = None; rest_buy = None
        # 3. STOP-LOSS pe close
        if qty > 1e-9 and sl > 0:
            avg = cost/qty
            if (avg - c)/avg >= sl:
                realized += (c-avg)*qty; fees += fee*qty*c
                cycles += 1
                qty = cost = spent = 0.0; dca = 0; last_open = None
                rest_sell = None; rest_buy = None
        # 4. decizii pe close
        if qty <= 1e-9:
            if rest_buy is None and spent + P["entry"] <= P["budget"]:
                px = c*(1-disc); rest_buy = (px, round(P["entry"]/px, 6))
        else:
            avg = cost/qty
            rest_sell = (avg*(1+tp), qty)
            if (dca < P["maxdca"] and last_open and c <= last_open*(1-drop)
                    and spent + P["dca"] <= P["budget"] and rest_buy is None):
                px = c*(1-disc); rest_buy = (px, round(P["dca"]/px, 6))
        # equity curve (realized + unrealized)
        upnl = (c - cost/qty)*qty if qty > 1e-9 else 0
        eq = realized - fees + upnl; peak = max(peak, eq); maxdd = max(maxdd, peak - eq)

    final_upnl = (ohlc[-1][3] - cost/qty)*qty if qty > 1e-9 else 0.0
    return {"realized": realized, "fees": fees, "net": realized - fees,
            "total": realized - fees + final_upnl, "final_upnl": final_upnl,
            "cycles": cycles, "wins": wins, "maxdd": maxdd, "open_qty": qty}


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtester DCA+TP T212 (Yahoo).")
    ap.add_argument("--mode", choices=["single", "sweep"], default="single")
    ap.add_argument("--sym", default="NVDA")
    ap.add_argument("--range", default="1y", help="ex: 1mo,3mo,6mo,1y,2y")
    ap.add_argument("--interval", default="1d", help="ex: 1h,1d")
    ap.add_argument("--entry", type=float, default=100); ap.add_argument("--dca", type=float, default=80)
    ap.add_argument("--disc", type=float, default=0.2); ap.add_argument("--drop", type=float, default=2.0)
    ap.add_argument("--tp", type=float, default=1.5); ap.add_argument("--maxdca", type=int, default=10)
    ap.add_argument("--budget", type=float, default=2000); ap.add_argument("--fee", type=float, default=0.15)
    ap.add_argument("--sl", type=float, default=10.0, help="stop-loss %% (0=oprit)")
    args = ap.parse_args()

    ohlc = fetch_candles(args.sym, args.range, args.interval)
    if len(ohlc) < 20:
        print(f"! prea putine date ({len(ohlc)})"); return 1
    closes = [x[3] for x in ohlc]
    bh = (closes[-1] - closes[0]) / closes[0] * 100
    base = dict(entry=args.entry, dca=args.dca, disc=args.disc, drop=args.drop,
                tp=args.tp, maxdca=args.maxdca, budget=args.budget, fee=args.fee, sl=args.sl)

    if args.mode == "single":
        m = simulate(ohlc, base)
        tot = m["total"]/args.budget*100
        wr = 100*m["wins"]/m["cycles"] if m["cycles"] else 0
        print(f"=== BACKTEST T212 {args.sym} {args.range}/{args.interval} ({len(ohlc)} bare) ===")
        print(f"  params: entry={args.entry} dca={args.dca} drop={args.drop}% tp={args.tp}% sl={args.sl}% fee={args.fee}%/leg")
        print(f"  TOTAL REAL: {tot:+.2f}% din buget  ⇐ realizat ${m['realized']:+.2f} + pozitie deschisa ${m['final_upnl']:+.2f} - fee ${m['fees']:.2f}")
        print(f"  (realizat singur: {m['net']/args.budget*100:+.2f}% — inselator daca ramane pozitie pierzatoare)")
        print(f"  cicluri: {m['cycles']}  win-rate {wr:.0f}%   max drawdown ${m['maxdd']:.2f}")
        print(f"  buy&hold: {bh:+.2f}%   pozitie la final: {m['open_qty']:.4f}")
        return 0

    print(f"=== SWEEP {args.sym} {args.range}/{args.interval}  (buy&hold {bh:+.1f}%) ===")
    rows = []
    for tp in (1.0, 1.5, 2.0, 3.0, 5.0):
        for drop in (1.0, 2.0, 3.0, 5.0):
            for sl in (8.0, 15.0):
                P = dict(base); P["tp"] = tp; P["drop"] = drop; P["sl"] = sl
                m = simulate(ohlc, P)
                rows.append((m["total"]/args.budget*100, tp, drop, sl, m["cycles"], m["maxdd"]))
    rows.sort(reverse=True)
    print("  top 8 (total% | tp | drop | sl | cicluri | maxDD$):")
    for tot, tp, drop, sl, cyc, dd in rows[:8]:
        print(f"    {tot:+7.2f}%  tp={tp:<4} drop={drop:<4} sl={sl:<4} cic={cyc:<3} dd=${dd:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
