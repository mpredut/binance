#!/usr/bin/env python3
# check_cache_coherence.py — verifica PROSPETIMEA + coerenta cache-urilor de trend/pret
# pt TOATE simbolurile (Binance + non-Binance din instruments.conf). Exit 1 daca ceva e stale.
# Rulat periodic (cron/loop) ca sa prinzi cache inghetat.
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHEDB = os.path.join(ROOT, "cachedb")
STALE_S = 600.0  # >10 min fara update = STALE (instant-trend se reimprospateaza la secunde / 20s)


def load(name):
    p = os.path.join(CACHEDB, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        print(f"  [ERR] {name}: {e}")
        return None


def expected_symbols():
    """sym.symbols (Binance) + simbolurile non-Binance enabled din instruments.conf."""
    syms = []
    try:
        sys.path.insert(0, ROOT)
        import symbols as sym
        syms = list(sym.symbols)
        from instruments_config import load_for
        for inst in load_for("mt").values():
            if inst.provider_name != "binance" and inst.symbol not in syms:
                syms.append(inst.symbol)
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] nu pot deriva simbolurile asteptate: {e}")
    return syms


now = time.time()
problems = []
print(f"=== cache coherence @ {time.strftime('%Y-%m-%d %H:%M:%S')} (stale>{int(STALE_S)}s) ===")
exp = expected_symbols()
print(f"simboluri asteptate: {exp}")

# 1. instant trend
it = load("cache_instant_trend.json") or {}
print(f"-- cache_instant_trend.json: {sorted(it.keys())}")
for s in exp:
    e = it.get(s)
    if not e:
        problems.append(f"instant_trend LIPSA {s}")
        print(f"   {s:10} LIPSESTE")
        continue
    ts = e.get("ts", 0)
    age = now - ts if ts else 1e9
    px = e.get("current_price")
    ok = age < STALE_S and px
    if not ok:
        problems.append(f"instant_trend {s} age={age:.0f}s px={px}")
    print(f"   {s:10} age={age:6.0f}s gradient_recent={e.get('gradient_recent')} price={px} -> {'OK' if ok else 'STALE/INVALID'}")

# 2. current price
cp = load("cache_currentprice.json") or {}
print(f"-- cache_currentprice.json: {sorted(cp.keys())}")
for s in exp:
    entries = cp.get(s)
    if not entries or not isinstance(entries, list):
        problems.append(f"currentprice LIPSA {s}")
        print(f"   {s:10} LIPSESTE")
        continue
    ts_ms, price = entries[-1][0], entries[-1][1]
    age = now - ts_ms / 1000.0
    ok = age < STALE_S
    if not ok:
        problems.append(f"currentprice {s} age={age:.0f}s")
    print(f"   {s:10} age={age:6.0f}s price={price} -> {'OK' if ok else 'STALE'}")

print()
if problems:
    print(f"PROBLEME ({len(problems)}): {problems}")
    sys.exit(1)
print("OK — toate cache-urile sunt PROASPETE si coerente pt toate simbolurile")
sys.exit(0)
