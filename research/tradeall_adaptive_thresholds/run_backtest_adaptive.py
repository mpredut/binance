#!/usr/bin/env python3
"""
Investigatie (23 iul 2026): are sens sa promovam vol_1h_pct din shadow_signals
la o decizie REALA in tradeall.py, inlocuind pragurile FIXE de detectie a
miscarii de pret (PRICE_CHANGE_THRESHOLD_EUR / PRICE_CHANGE_THRESHOLD_BIG_EUR,
folosite de check_price_change() pt ferestrele SMALL/BIG) cu praguri ADAPTIVE
(K * vol_1h_pct, aceeasi formula ca shadow_signals.vol_1h_pct)?

Context (raspuns la intrebarea user "nu am facut asta din cauza backtestului?"):
NU exista niciun backtest anterior care sa testeze EXACT aceasta idee.
research/tradeall_trigger_gate/ (21-22 iul) a testat ALTE tipuri de schimbari —
relaxarea conditiilor de start/confirmare ale unui trend deja pornit, cooldown,
un semnal de calitate pe regresie 24h — si niciuna nu a batut varianta
actuala + buy&hold pe 329 de zile. Dar NICIUNA din acele variante nu a inlocuit
pragurile FIXE cu unele scalate pe volatilitatea REALIZATA — aceasta e o idee
noua, analoaga cu promovarea reusita a pragului de reintrare adaptiv pe Kraken
(research/kraken_adaptive_thresholds/), doar aplicata altui mecanism.

Metodologie (aceeasi rigoare ca research/kraken_adaptive_thresholds/ si
Experimentul 6 din tradeall_trigger_gate/):
  - Istoric REAL, 329 zile (cache_price_{symbol}.jsonl, ~7 min/tick).
  - check_price_change() insasi NU e modificata — doar valoarea threshold-ului
    trimisa e inlocuita cu K*vol_1h_pct, calculat din EXACT aceeasi fereastra
    BIG folosita si de shadow_signals.vol_1h_pct (istoric real, nu simulat separat).
  - FALLBACK pe pragurile FIXE reale (din config, NU valori arbitrare) in
    warm-up (<20 puncte in fereastra BIG) — acelasi tipar fail-safe ca la
    Kraken (STRAT_REENTRY_ADAPTIVE): un semnal indisponibil nu opreste/altereaza
    trading-ul, doar cade pe valoarea fixa.
  - K_BIG = K_SMALL * RATIO, unde RATIO = pragul BIG fix / pragul SMALL fix de
    azi (~4.79) — pastram raportul dintre ferestre ca sa nu introducem o a doua
    dimensiune netestata in sweep (acelasi principiu ca la Kraken: reentry si
    DCA testate SEPARAT, fiecare cu un singur multiplicator).
  - Comparatie: PnL net (realizat + mark-to-market - comisioane) vs varianta
    FIXA (K implicit) si vs buy&hold, pe AMBELE simboluri (BTC, TAO).

Refoloseste tradeall_backtest.run_backtest() prin hook-ul `threshold_provider`
(adaugat 23 iul, ca parte a acestei investigatii) — NU mai copiaza bucla de
tick. Inainte de acest hook, acest fisier avea propria copie a buclei din
run_backtest() (risc de derapaj tacut fata de motorul "oficial" daca acesta
se schimba ulterior — vezi research/BACKTEST_CANDIDATES.md si discutia din
sesiune despre unificarea backtest-urilor). Refactorizarea a fost verificata
sa reproduca BIT-FOR-BIT rezultatele buclei vechi, pe date reale, inainte de
a inlocui vechea implementare.
"""
import os
import sys
import json
import time
from datetime import datetime

ROOT = "/home/predut/binance"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import tradeall_backtest as tb
import shadow_signals

# Valorile FIXE REALE de azi (din tradeall_config.env, nu constante arbitrare) —
# folosite ca (a) fallback in warm-up si (b) baseline "FIX" de comparat.
FIXED_SMALL = 0.5180048459
FIXED_BIG = 2.4809130428
RATIO = FIXED_BIG / FIXED_SMALL   # ~4.79


def run_adaptive(symbol, start_ts, end_ts, k_small, run_id):
    """Wrapper subtire peste tradeall_backtest.run_backtest(): construieste un
    threshold_provider care intoarce K*vol_1h_pct (adaptiv) in loc de pragul fix,
    cu fallback pe FIXED_SMALL/FIXED_BIG in warm-up. k_small=None => rulare de
    control cu pragurile FIXE (echivalent cu run_backtest() normal, fara hook)."""
    k_big = None if k_small is None else k_small * RATIO
    warmup = {"n": 0}

    def _threshold_provider(window_small, window_big):
        if k_small is None:
            return FIXED_SMALL, FIXED_BIG
        vol1h = shadow_signals.vol_1h_pct(list(window_big.prices), window_big.sample_rate_sec)
        if vol1h is None:
            warmup["n"] += 1
            return FIXED_SMALL, FIXED_BIG
        return k_small * vol1h, k_big * vol1h

    tb.run_backtest(symbol, start_ts, end_ts, "fast", run_id, "history",
                     quiet=True, kalman_primary=False, threshold_provider=_threshold_provider)

    pnl_path = os.path.join(ROOT, "logger", "backtest", run_id, "pnl.json")
    pnl = json.load(open(pnl_path, encoding="utf-8")) if os.path.exists(pnl_path) else {}
    pnl["warmup_ticks"] = warmup["n"]
    pnl["k_small"] = k_small
    pnl["k_big"] = round(k_big, 4) if k_big is not None else None
    with open(pnl_path, "w", encoding="utf-8") as pf:
        json.dump(pnl, pf, indent=1)
    sys.stderr.write(f"[{run_id}] P&L: {pnl}\n")
    return pnl


if __name__ == "__main__":
    hist_start = datetime.strptime("2025-08-27", "%Y-%m-%d").timestamp()
    # 23 iul: redus de la [None,0.5,1.0,1.5,2.0,2.5,3.0,4.0] dupa ce arhiva reala
    # s-a dovedit mult mai densa decat estimat (~888k tick-uri/simbol, nu ~66k) —
    # un sweep de 8 valori x 2 simboluri ar fi durat ore. 4 valori (control + 3
    # multiplicatori raspanditi) tot acopera intervalul relevant, in ~1/2 din timp.
    K_SWEEP = [None, 1.0, 2.0, 3.0]   # None = control (praguri fixe, prin aceeasi bucla)

    results = {}
    t_all = time.time()
    for symbol in ("BTCUSDC", "TAOUSDC"):
        for k in K_SWEEP:
            tag = "FIX" if k is None else f"k{k}"
            run_id = f"tradeall_adaptive_{symbol}_{tag}"
            t0 = time.time()
            pnl = run_adaptive(symbol, hist_start, None, k, run_id)
            results[(symbol, tag)] = pnl
            sys.stderr.write(f"  ({time.time()-t0:.1f}s)\n")

    sys.stderr.write(f"\n\n===== REZUMAT (wall total {time.time()-t_all:.1f}s) =====\n")
    for symbol in ("BTCUSDC", "TAOUSDC"):
        sys.stderr.write(f"\n--- {symbol} ---\n")
        for k in K_SWEEP:
            tag = "FIX" if k is None else f"k{k}"
            pnl = results[(symbol, tag)]
            sys.stderr.write(
                f"  {tag:6s}: net_total={pnl.get('net_total'):>10} buy_hold={pnl.get('buy_hold_net'):>10} "
                f"buys={pnl.get('buys')} sells={pnl.get('sells')} warmup_ticks={pnl.get('warmup_ticks')}\n")
