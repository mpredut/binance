#!/usr/bin/env python3
"""
verify_adaptive_reentry.py — merita promovat pragul de REINTRARE adaptiv
(shadow, K_REENTRY * vol_1h — vezi _shadow_reentry_line din kraken/strategy.py,
azi DOAR log) la decizie REALA, in locul pragului FIX (STRAT_REENTRY_DROP_PCT)?

Gasit in timpul investigatiei: NICI backtest.py, NICI backtest_adaptive.py nu
modeleaza bariera de reintrare — dupa ce o pozitie se inchide (take-profit sau
stop-loss), simulatoarele originale reintra IMEDIAT la urmatoarea bara, fara
nicio asteptare. Strategia REALA (kraken/strategy.py, functia step()) asteapta
explicit ca pretul sa scada sub last_sell_price*(1-reentry_pct/100) (cu
toleranta are_close) inainte sa reintre. Acest script adauga acel mecanism
LIPSA (fidel formulei din botcore.diff_percent/are_close), variind DOAR
pragul de reintrare — pragul de DCA ramane fix la valoarea reala din
config.env, ca sa izolam STRICT efectul reintrarii (aceeasi metodologie ca in
research/tradeall_trigger_gate/: o singura variabila schimbata per test).

Parametri = valorile REALE din kraken/config.env.

Rulare:  python3 research/kraken_adaptive_thresholds/verify_adaptive_reentry.py
"""
import sys, os

ROOT = "/home/predut/binance"
sys.path.insert(0, os.path.join(ROOT, "kraken"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np
from backtest import fetch_candles
from backtest_adaptive import trailing_vol_series, report
import shadow_signals as ss

PAIR = "HYPEUSD"
INTERVAL = 60

REAL = dict(
    entry=650, dca=325, disc=0.8, tp=5.0, maxdca=10, budget=3900, fee=0.26, sl=7.0,
    drop=1.0,               # STRAT_DCA_DROP_PCT — fix, neschimbat (nu e subiectul testului asta)
    reentry_fallback=2.2,   # STRAT_REENTRY_DROP_PCT actual
)
REENTRY_TOLERANCE_PCT = 0.05  # STRAT_REENTRY_TOLERANCE_PCT actual


def _are_close(v1, v2, tol_pct):
    """Aceeasi formula ca botcore.diff_percent/are_close (simetrica, pe media absoluta)."""
    if tol_pct <= 0:
        return False
    denom = (abs(v1) + abs(v2)) / 2
    if denom == 0:
        return True
    return abs(v1 - v2) / denom * 100 <= tol_pct


def simulate_with_reentry_gate(ohlc, P, reentry_arr):
    """Motor identic cu backtest.simulate(), PLUS bariera de reintrare (lipsa din
    unealta originala) dupa fiecare inchidere de pozitie (TP sau SL)."""
    disc, drop, tp, sl = P["disc"]/100, P["drop"]/100, P["tp"]/100, P["sl"]/100
    fee = P["fee"]/100
    qty = cost = spent = 0.0
    dca = 0; last_open = None
    realized = fees = 0.0
    cycles = wins = 0
    peak = eq = 0.0; maxdd = 0.0
    rest_buy = None; rest_sell = None
    last_sell_price = None
    blocked_ticks = 0   # cate bare a stat "in asteptare de reintrare" (informativ)

    for i, (o, h, l, c) in enumerate(ohlc):
        reentry_pct = reentry_arr[i] if not np.isnan(reentry_arr[i]) else P["reentry_fallback"]

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
        if qty > 1e-9 and sl > 0:
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
                if reentry_pct > 0 and last_sell_price:
                    prag = last_sell_price * (1 - reentry_pct / 100)
                    if c > prag and not _are_close(c, prag, REENTRY_TOLERANCE_PCT):
                        blocked = True
                        blocked_ticks += 1
                if not blocked:
                    px = c * (1 - disc); rest_buy = (px, round(P["entry"] / px, 8))
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


def run():
    ohlc = fetch_candles(PAIR, INTERVAL)
    print(f"=== {PAIR} interval={INTERVAL}m ({len(ohlc)} bare = ~{len(ohlc)/24:.1f} zile) ===")
    closes = [x[3] for x in ohlc]
    bh = (closes[-1] - closes[0]) / closes[0] * 100
    print(f"buy&hold pe perioada: {bh:+.1f}%")

    vol_trail = trailing_vol_series(closes)
    fixed_arr = np.full(len(ohlc), REAL["reentry_fallback"])
    shadow_arr = np.array([ss.K_REENTRY * v if not np.isnan(v) else np.nan for v in vol_trail])

    print(f"\n(K_REENTRY={ss.K_REENTRY}, prag FIX real={REAL['reentry_fallback']}%, "
          f"toleranta={REENTRY_TOLERANCE_PCT}%, medie adaptiv-shadow={np.nanmean(shadow_arr):.2f}%, "
          f"min={np.nanmin(shadow_arr):.2f}% max={np.nanmax(shadow_arr):.2f}%)")

    m_fixed = simulate_with_reentry_gate(ohlc, REAL, fixed_arr)
    m_shadow = simulate_with_reentry_gate(ohlc, REAL, shadow_arr)

    for name, m in (("FIX (live azi)", m_fixed), ("adaptiv-shadow", m_shadow)):
        tot = m["total"]/REAL["budget"]*100
        wr = 100*m["wins"]/m["cycles"] if m["cycles"] else 0
        print(f"  [{name:16s}] TOTAL {tot:+7.2f}%  realizat ${m['realized']:+8.2f}  "
              f"cicluri={m['cycles']:<3} win-rate={wr:3.0f}%  maxDD=${m['maxdd']:.2f}  "
              f"bare-blocate={m['blocked_ticks']:<4} pozitie deschisa={m['open_qty']:.4f}")
    return m_fixed, m_shadow


if __name__ == "__main__":
    run()
