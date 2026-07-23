#!/usr/bin/env python3
"""
verify_adaptive_dca.py — merita promovat pragul de DCA adaptiv (shadow, K_DCA *
vol_1h realizat, deja calculat in shadow_signals.py dar folosit DOAR ca log in
kraken/strategy.py) la decizie REALA, in locul pragului FIX (STRAT_DCA_DROP_PCT)?

Refoloseste EXACT motorul de simulare din kraken/backtest_adaptive.py
(simulate_variant/trailing_vol_series/fetch_candles) — nu rescrie logica.
SARE peste partea Chronos (model ML — ar incarca memorie pe masina cu botii
reali activi, inutil pt aceasta intrebare specifica: fix vs shadow).

Parametri = valorile REALE din kraken/config.env (NU default-urile scriptului
original, care sunt ghicite/vechi — exact greseala prinsa intr-o sesiune
anterioara: prima rulare cu parametri gresiti a aratat un castig mare pt
adaptiv, apoi complet inversat cu valorile reale).

Rulare:  python3 research/kraken_adaptive_thresholds/verify_adaptive_dca.py
"""
import sys, os

ROOT = "/home/predut/binance"
sys.path.insert(0, os.path.join(ROOT, "kraken"))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import numpy as np
from backtest import fetch_candles
from backtest_adaptive import trailing_vol_series, simulate_variant, report
import shadow_signals as ss

PAIR = "HYPEUSD"
INTERVAL = 60  # minute per bara (1h) — rezolutia formulei vol_1h_pct

# Valori REALE din kraken/config.env (NU default-urile scriptului!)
REAL = dict(
    entry=650, dca=325, disc=0.8, tp=5.0, maxdca=10, budget=3900, fee=0.26, sl=7.0,
    drop_fallback=1.0,   # STRAT_DCA_DROP_PCT actual
)


def run():
    ohlc = fetch_candles(PAIR, INTERVAL)
    print(f"=== {PAIR} interval={INTERVAL}m ({len(ohlc)} bare = ~{len(ohlc)/24:.1f} zile) ===")
    closes = [x[3] for x in ohlc]
    bh = (closes[-1] - closes[0]) / closes[0] * 100
    print(f"buy&hold pe perioada: {bh:+.1f}%")

    vol_trail = trailing_vol_series(closes)
    n_valid = np.sum(~np.isnan(vol_trail))
    print(f"vol_1h_pct trailing disponibil pt {n_valid}/{len(closes)} bare (primele 24 sunt warm-up)")

    fixed_arr = np.full(len(ohlc), REAL["drop_fallback"])
    shadow_arr = np.array([ss.K_DCA * v if not np.isnan(v) else np.nan for v in vol_trail])

    print(f"\n(K_DCA={ss.K_DCA}, prag FIX real={REAL['drop_fallback']}%, "
          f"medie adaptiv-shadow={np.nanmean(shadow_arr):.2f}%, "
          f"min={np.nanmin(shadow_arr):.2f}% max={np.nanmax(shadow_arr):.2f}%)")

    m_fixed = simulate_variant(ohlc, REAL, fixed_arr)
    m_shadow = simulate_variant(ohlc, REAL, shadow_arr)

    report("FIX (live azi)", m_fixed, REAL["budget"])
    report("adaptiv-shadow", m_shadow, REAL["budget"])
    return m_fixed, m_shadow


if __name__ == "__main__":
    run()
