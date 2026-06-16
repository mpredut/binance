#!/usr/bin/env python3
"""Verifica DOAR ca modulele importa curat (fara a porni nimic). Pt validarea
refactor-urilor de importuri inainte de restart-ul flotei. Ruleaza cu venv-ul:
  ~/binance/myenv/bin/python verify_imports.py
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MODS = [
    "binance_api.bapi", "binance_api.bapi_client", "binance_api.bapi_placeorder",
    "binance_api.bapi_trades", "binance_api.bapi_allorders", "binance_api.bapi_ws",
    "cacheManager", "pricefetcher", "tradeall", "monitortrades", "monitortrades_legacy",
    "assetguardian", "rtrade", "tradeCacheManager", "monitororder", "trade_watch", "symbols",
]

fails = 0
for m in MODS:
    try:
        importlib.import_module(m)
        print(f"OK   {m}")
    except Exception as e:  # noqa: BLE001
        fails += 1
        print(f"FAIL {m}: {e.__class__.__name__}: {e}")

print(f"\n=== {len(MODS)-fails}/{len(MODS)} OK, {fails} esuate ===")
sys.exit(1 if fails else 0)
