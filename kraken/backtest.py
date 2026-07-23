#!/usr/bin/env python3
"""
backtest.py — backtester DCA + take-profit pentru Kraken (spot), pe OHLC Kraken.

Reia strategia bar-cu-bar (intrare la market-discount, DCA pe scadere, take-profit,
STOP-LOSS), cu fee Kraken (~0.25%/leg taker). Raport ONEST: TOTAL include pozitia
deschisa (mark-to-market), nu doar profitul realizat.

  python3 backtest.py --pair HYPEUSD --interval 60
  python3 backtest.py --mode sweep --pair HYPEUSD --interval 240
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request


def _are_close(v1, v2, tol_pct):
    """Aceeasi formula ca botcore.diff_percent/are_close (simetrica, pe media
    absoluta) — determinista, fara sa importe botcore (backtest ruleaza izolat)."""
    if tol_pct <= 0:
        return False
    denom = (abs(v1) + abs(v2)) / 2
    if denom == 0:
        return True
    return abs(v1 - v2) / denom * 100 <= tol_pct


def fetch_candles(pair, interval):
    url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (backtest)"})
    with urllib.request.urlopen(req, timeout=25) as r:
        data = json.loads(r.read())
    if data.get("error"):
        raise RuntimeError(", ".join(data["error"]))
    res = data.get("result", {})
    key = next((k for k in res if k != "last"), None)
    if not key:
        return []
    return [(float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in res[key]]


def simulate(ohlc, P, reentry_arr=None):
    """Motor DCA+TP+SL. `reentry_arr` OPTIONAL (default None = comportament VECHI,
    neschimbat): daca dat (secventa per-bara, NaN = foloseste P["reentry_fallback"]),
    activeaza bariera de reintrare dupa o inchidere de pozitie (TP/SL) — lipsea din
    versiunea originala (gasit in research/kraken_adaptive_thresholds/, 23 iul:
    strategia REALA, kraken/strategy.py step(), asteapta explicit sub
    last_sell_price*(1-reentry_pct/100) inainte sa reintre; simulatorul reintra
    imediat). P["reentry_tolerance_pct"] (implicit 0 = fara toleranta) controleaza
    cat de "aproape de prag" conteaza ca atins (are_close, determinist)."""
    disc, drop, tp, sl = P["disc"]/100, P["drop"]/100, P["tp"]/100, P["sl"]/100
    fee = P["fee"]/100
    reentry_tol = P.get("reentry_tolerance_pct", 0.0)
    qty = cost = spent = 0.0
    dca = 0; last_open = None
    realized = fees = 0.0
    cycles = wins = 0
    peak = eq = 0.0; maxdd = 0.0
    rest_buy = None; rest_sell = None
    last_sell_price = None
    blocked_ticks = 0

    for i, (o, h, l, c) in enumerate(ohlc):
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
                last_sell_price = px
                qty = cost = spent = 0.0; dca = 0; last_open = None
                rest_sell = None; rest_buy = None
        if qty > 1e-9 and sl > 0:                       # STOP-LOSS pe close
            avg = cost/qty
            if (avg - c)/avg >= sl:
                realized += (c-avg)*qty; fees += fee*qty*c
                cycles += 1
                last_sell_price = c
                qty = cost = spent = 0.0; dca = 0; last_open = None
                rest_sell = None; rest_buy = None
        if qty <= 1e-9:
            if rest_buy is None and spent + P["entry"] <= P["budget"]:
                blocked = False
                if reentry_arr is not None and last_sell_price:
                    r = reentry_arr[i]
                    reentry_pct = P.get("reentry_fallback", 0.0) if (isinstance(r, float) and math.isnan(r)) else r
                    if reentry_pct > 0:
                        prag = last_sell_price * (1 - reentry_pct / 100)
                        if c > prag and not _are_close(c, prag, reentry_tol):
                            blocked = True
                            blocked_ticks += 1
                if not blocked:
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
            "cycles": cycles, "wins": wins, "maxdd": maxdd, "open_qty": qty,
            "blocked_ticks": blocked_ticks}


def main() -> int:
    ap = argparse.ArgumentParser(description="Backtester DCA+TP Kraken (OHLC).")
    ap.add_argument("--mode", choices=["single", "sweep"], default="single")
    ap.add_argument("--pair", default="HYPEUSD")
    ap.add_argument("--interval", type=int, default=60, help="minute: 60=1h, 240=4h, 1440=1z")
    ap.add_argument("--entry", type=float, default=100); ap.add_argument("--dca", type=float, default=50)
    ap.add_argument("--disc", type=float, default=0.2); ap.add_argument("--drop", type=float, default=2.0)
    ap.add_argument("--tp", type=float, default=1.9); ap.add_argument("--maxdca", type=int, default=10)
    ap.add_argument("--budget", type=float, default=1000); ap.add_argument("--fee", type=float, default=0.25)
    ap.add_argument("--sl", type=float, default=10.0, help="stop-loss %% (0=oprit)")
    args = ap.parse_args()

    try:
        ohlc = fetch_candles(args.pair, args.interval)
    except Exception as e:  # noqa: BLE001
        print(f"! eroare date: {e}"); return 1
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
        print(f"=== BACKTEST KRAKEN {args.pair} interval={args.interval}m ({len(ohlc)} bare) ===")
        print(f"  params: entry={args.entry} dca={args.dca} drop={args.drop}% tp={args.tp}% sl={args.sl}% fee={args.fee}%/leg")
        print(f"  TOTAL REAL: {tot:+.2f}% din buget  ⇐ realizat ${m['realized']:+.2f} + pozitie deschisa ${m['final_upnl']:+.2f} - fee ${m['fees']:.2f}")
        print(f"  (realizat singur: {m['net']/args.budget*100:+.2f}%)")
        print(f"  cicluri: {m['cycles']}  win-rate {wr:.0f}%   max drawdown ${m['maxdd']:.2f}")
        print(f"  buy&hold: {bh:+.2f}%   pozitie la final: {m['open_qty']:.6f}")
        return 0

    print(f"=== SWEEP {args.pair} interval={args.interval}m  (buy&hold {bh:+.1f}%) ===")
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
