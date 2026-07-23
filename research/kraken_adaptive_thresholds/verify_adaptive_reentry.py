#!/usr/bin/env python3
"""
verify_adaptive_reentry.py — merita promovat pragul de REINTRARE adaptiv
(shadow, K_REENTRY * vol_1h — vezi _shadow_reentry_line din kraken/strategy.py,
azi DOAR log) la decizie REALA, in locul pragului FIX (STRAT_REENTRY_DROP_PCT)?

Gasit in timpul investigatiei: NICI backtest.py, NICI backtest_adaptive.py nu
modelau bariera de reintrare — dupa ce o pozitie se inchide (take-profit sau
stop-loss), simulatoarele originale reintrau IMEDIAT la urmatoarea bara, fara
nicio asteptare. Strategia REALA (kraken/strategy.py, functia step()) asteapta
explicit ca pretul sa scada sub last_sell_price*(1-reentry_pct/100) (cu
toleranta are_close) inainte sa reintre.

23 iul: mecanismul (scris initial DOAR aici, ca sa nu bage in unealta oficiala
ceva netestat) a fost MERGE-uit inapoi in backtest.simulate() ca parametru
OPTIONAL `reentry_arr` (default None = comportamentul vechi, neschimbat) —
motivul e sa nu mai existe DOUA copii ale motorului DCA/TP/SL care pot diverge
in timp (vezi research/BACKTEST_CANDIDATES.md si discutia din sesiune despre
unificarea backtest-urilor). Acest script ramane un wrapper subtire peste
unealta oficiala, cu valorile REALE din kraken/config.env — nu mai
redefineste motorul de simulare.

Rulare:  python3 research/kraken_adaptive_thresholds/verify_adaptive_reentry.py
"""
import sys, os

ROOT = "/home/predut/binance"
sys.path.insert(0, os.path.join(ROOT, "kraken"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np
from backtest import fetch_candles, simulate as simulate_with_reentry_gate
from backtest_adaptive import trailing_vol_series, report
import shadow_signals as ss

PAIR = "HYPEUSD"
INTERVAL = 60

REAL = dict(
    entry=650, dca=325, disc=0.8, tp=5.0, maxdca=10, budget=3900, fee=0.26, sl=7.0,
    drop=1.0,                     # STRAT_DCA_DROP_PCT — fix, neschimbat (nu e subiectul testului asta)
    reentry_fallback=2.2,         # STRAT_REENTRY_DROP_PCT actual
    reentry_tolerance_pct=0.05,   # STRAT_REENTRY_TOLERANCE_PCT actual (trecut acum prin P, nu global)
)
REENTRY_TOLERANCE_PCT = REAL["reentry_tolerance_pct"]  # pastrat ca nume, folosit doar in printul de mai jos


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
