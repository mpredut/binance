#!/usr/bin/env python3
"""
sweep_k_multiplier.py — inainte de a promova pragul adaptiv de reintrare (sau
DCA) la decizie reala, testeaza daca multiplicatorul K curent (K_REENTRY=2.0,
K_DCA=1.0, hardcodate in shadow_signals.py) e chiar cel mai bun, sau daca un
K mai mare/mai mic ar da un rezultat superior — cerere user dupa rezultatele
initiale (reintrare adaptiv castiga cu K=2.0, DCA adaptiv pierde cu K=1.0).

Refoloseste EXACT motoarele de simulare din verify_adaptive_dca.py si
verify_adaptive_reentry.py (nimic reimplementat) — doar variaza K inainte de
a inmulti cu vol_1h.

Rulare:  python3 research/kraken_adaptive_thresholds/sweep_k_multiplier.py
"""
import sys, os

ROOT = "/home/predut/binance"
sys.path.insert(0, os.path.join(ROOT, "kraken"))
sys.path.insert(0, os.path.join(ROOT, "research", "kraken_adaptive_thresholds"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np
from backtest import fetch_candles
from backtest_adaptive import trailing_vol_series, simulate_variant
from verify_adaptive_reentry import simulate_with_reentry_gate, REAL as REAL_REENTRY, REENTRY_TOLERANCE_PCT
from verify_adaptive_dca import REAL as REAL_DCA

PAIR = "HYPEUSD"
INTERVAL = 60

K_REENTRY_SWEEP = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
K_DCA_SWEEP = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5]


def main():
    ohlc = fetch_candles(PAIR, INTERVAL)
    closes = [x[3] for x in ohlc]
    bh = (closes[-1] - closes[0]) / closes[0] * 100
    print(f"=== {PAIR} interval={INTERVAL}m ({len(ohlc)} bare = ~{len(ohlc)/24:.1f} zile) — buy&hold {bh:+.1f}% ===\n")
    vol_trail = trailing_vol_series(closes)

    print(f"--- SWEEP REINTRARE (fix live = {REAL_REENTRY['reentry_fallback']}%) ---")
    fixed_arr_re = np.full(len(ohlc), REAL_REENTRY["reentry_fallback"])
    m_fix_re = simulate_with_reentry_gate(ohlc, REAL_REENTRY, fixed_arr_re)
    tot_fix_re = m_fix_re["total"] / REAL_REENTRY["budget"] * 100
    print(f"  FIX               TOTAL {tot_fix_re:+7.2f}%  realizat ${m_fix_re['realized']:+8.2f}  "
          f"cicluri={m_fix_re['cycles']:<3} maxDD=${m_fix_re['maxdd']:.2f}")
    best_k_re, best_tot_re = None, -1e18
    for k in K_REENTRY_SWEEP:
        arr = np.array([k * v if not np.isnan(v) else np.nan for v in vol_trail])
        m = simulate_with_reentry_gate(ohlc, REAL_REENTRY, arr)
        tot = m["total"] / REAL_REENTRY["budget"] * 100
        marker = " <- curent (2.0)" if k == 2.0 else ""
        print(f"  K_REENTRY={k:<4} TOTAL {tot:+7.2f}%  realizat ${m['realized']:+8.2f}  "
              f"cicluri={m['cycles']:<3} maxDD=${m['maxdd']:.2f}  medie_prag={np.nanmean(arr):.2f}%{marker}")
        if tot > best_tot_re:
            best_k_re, best_tot_re = k, tot
    print(f"  => cel mai bun K_REENTRY din sweep: {best_k_re} (TOTAL {best_tot_re:+.2f}%)")

    print(f"\n--- SWEEP DCA (fix live = {REAL_DCA['drop_fallback']}%) ---")
    fixed_arr_dca = np.full(len(ohlc), REAL_DCA["drop_fallback"])
    m_fix_dca = simulate_variant(ohlc, REAL_DCA, fixed_arr_dca)
    tot_fix_dca = m_fix_dca["total"] / REAL_DCA["budget"] * 100
    print(f"  FIX               TOTAL {tot_fix_dca:+7.2f}%  realizat ${m_fix_dca['realized']:+8.2f}  "
          f"cicluri={m_fix_dca['cycles']:<3} maxDD=${m_fix_dca['maxdd']:.2f}")
    best_k_dca, best_tot_dca = None, -1e18
    for k in K_DCA_SWEEP:
        arr = np.array([k * v if not np.isnan(v) else np.nan for v in vol_trail])
        m = simulate_variant(ohlc, REAL_DCA, arr)
        tot = m["total"] / REAL_DCA["budget"] * 100
        marker = " <- curent (1.0)" if k == 1.0 else ""
        print(f"  K_DCA={k:<7} TOTAL {tot:+7.2f}%  realizat ${m['realized']:+8.2f}  "
              f"cicluri={m['cycles']:<3} maxDD=${m['maxdd']:.2f}  medie_prag={np.nanmean(arr):.2f}%{marker}")
        if tot > best_tot_dca:
            best_k_dca, best_tot_dca = k, tot
    print(f"  => cel mai bun K_DCA din sweep: {best_k_dca} (TOTAL {best_tot_dca:+.2f}%)")

    print(f"\n=== REZUMAT ===")
    print(f"Reintrare: FIX {tot_fix_re:+.2f}%  |  cel mai bun adaptiv (K={best_k_re}) {best_tot_re:+.2f}%")
    print(f"DCA:       FIX {tot_fix_dca:+.2f}%  |  cel mai bun adaptiv (K={best_k_dca}) {best_tot_dca:+.2f}%")


if __name__ == "__main__":
    main()
