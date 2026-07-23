#!/usr/bin/env python3
"""
Investigheaza daca KALMAN_SAMPLE_SEC=60 (rata de esantionare a filtrului Kalman
din shadow_signals.py, care alimenteaza KALMAN-PRIMAR + gate-ul din tradeall.py)
introduce o intarziere prea mare pt miscari rapide de pret (23 iul 2026).

Context: analiza unui episod REAL (BTC, 23 iul, 10:10:13-10:12:47) a aratat ca
o scadere de -0.53% s-a produs aproape integral INAINTE ca Kalman sa confirme
"DOWN" (~90% din miscare deja consumata cand a tranzitionat), iar "DOWN" a
durat doar 62s inainte sa revina la "FLAT" chiar la minimul local -- exact
inainte de o revenire a pretului (order_outcomes_2026-07-23.log confirma o
incercare reala de SELL "kalman_primary_down" la acel moment, refuzata
"no_fill" -- nu s-au miscat bani reali de data asta, dar mecanismul e viu).

Cauza structurala: la 60s/esantion, o miscare care se consuma in ~90-150s
(cazul de mai sus) nu poate fi vazuta mai devreme de ~1-2 perioade de
esantionare, indiferent de pragurile de incredere (CONF_ENTER/CONF_EXIT).
KALMAN_SAMPLE_SEC=60 a fost ales explicit pe 17 iul ca sa reduca zgomotul
(la 1s: 2868 tranzitii/zi pe BTC -- prea multe, palpaituri). Reducerea lui
risca sa reintroduca acel zgomot -- acest script cuantifica exact acel
compromis (latenta vs. zgomot) pe date REALE, inainte de orice schimbare.

Refoloseste modul kalman_primary DEJA EXISTENT si validat in
tradeall_backtest.py (Kalman conduce direct BUY/SELL la tranzitii, prin
broker.place_order_smart/sell_all) -- nu se construieste nicio bucla noua,
doar se monkeypatch-uieste shadow_signals.KALMAN_SAMPLE_SEC inaintea fiecarei
rulari (functiile citesc global-ul la fiecare apel, deci schimbarea are efect
imediat, fara sa fie nevoie de reload de modul).

Metodologie: istoric REAL 329 zile (cache_price_{symbol}.jsonl), aceeasi
arhiva ca research/tradeall_trigger_gate/Experimentul 6 si
research/tradeall_adaptive_thresholds/. Sweep pe KALMAN_SAMPLE_SEC, comparat
pe PnL net (kalman_primary) + numarul de tranzitii Kalman logate (proxy
direct pt zgomot/palpait) + buy&hold pe acelasi interval.

NU modifica tradeall.py, tradeall_backtest.py sau shadow_signals.py pe disc --
doar monkeypatch in memorie, pt durata scriptului. Nu ruleaza niciodata
impotriva retelei reale.
"""
import os
import sys
import json
import shutil
import time
from datetime import datetime

ROOT = "/home/predut/binance"
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault("BINANCE_AUTO_START_WEBSOCKETS", "0")

import tradeall_backtest as tb
import shadow_signals

# 60.0 = valoarea LIVE de azi (control). Restul: mai rapid (20) si mai lent
# (90/150), ca sa vedem in ce directie se muta compromisul latenta<->zgomot.
# Redus la 4 valori (de la 5) dupa ce sanity-check-ul a aratat ~40 min/rulare
# pe istoricul complet (888k tick-uri/simbol) -- 4x2=8 rulari, nu 10.
SAMPLE_SEC_SWEEP = [20.0, 60.0, 90.0, 150.0]


def run_variant(symbol, start_ts, end_ts, sample_sec, run_id):
    shadow_signals.KALMAN_SAMPLE_SEC = sample_sec
    out_dir = os.path.join(ROOT, "logger", "backtest", run_id)
    shutil.rmtree(out_dir, ignore_errors=True)

    t0 = time.time()
    tb.run_backtest(symbol, start_ts, end_ts, "fast", run_id, "history",
                     quiet=True, kalman_primary=True)
    elapsed = time.time() - t0

    pnl_path = os.path.join(out_dir, "pnl.json")
    pnl = json.load(open(pnl_path)) if os.path.exists(pnl_path) else {}

    # numara tranzitiile Kalman logate (proxy direct pt zgomot/instabilitate)
    shadow_log = os.path.join(out_dir, "tradeall_shadow.log")
    n_transitions = 0
    if os.path.exists(shadow_log):
        with open(shadow_log, encoding="utf-8") as f:
            n_transitions = sum(1 for _ in f)

    pnl["sample_sec"] = sample_sec
    pnl["n_kalman_transitions"] = n_transitions
    with open(pnl_path, "w", encoding="utf-8") as pf:
        json.dump(pnl, pf, indent=1)
    sys.stderr.write(f"[{run_id}] ({elapsed:.1f}s) PnL: {pnl}\n")
    return pnl


if __name__ == "__main__":
    hist_start = datetime.strptime("2025-08-27", "%Y-%m-%d").timestamp()
    results = {}
    t_all = time.time()
    for symbol in ("BTCUSDC", "TAOUSDC"):
        for sec in SAMPLE_SEC_SWEEP:
            run_id = f"tradeall_kalman_lag_{symbol}_s{int(sec)}"
            pnl = run_variant(symbol, hist_start, None, sec, run_id)
            results[(symbol, sec)] = pnl

    sys.stderr.write(f"\n\n===== REZUMAT (wall total {time.time()-t_all:.1f}s) =====\n")
    for symbol in ("BTCUSDC", "TAOUSDC"):
        sys.stderr.write(f"\n--- {symbol} ---\n")
        for sec in SAMPLE_SEC_SWEEP:
            pnl = results[(symbol, sec)]
            sys.stderr.write(
                f"  sample_sec={sec:>5}: net_total={pnl.get('net_total')} buy_hold={pnl.get('buy_hold_net')} "
                f"buys={pnl.get('buys')} sells={pnl.get('sells')} transitions={pnl.get('n_kalman_transitions')}\n")
