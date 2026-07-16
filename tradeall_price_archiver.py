#!/usr/bin/env python3
"""
tradeall_price_archiver.py — captureaza pretul LIVE (acelasi stream WS
public ca tradeall.py) intr-un cache24 SEPARAT, cu retentie LUNGA (implicit
6 luni, --months) in loc de 24h. Scop: incepand de ACUM, acumuleaza date de
pret la rezolutie DENSA (~1s, ca live), pentru backtesting viitor mult mai
fidel decat cache_price_{symbol}.jsonl (istoricul existent, mult mai rar —
vezi caveat-ul din plan, sectiunea A5).

NU atinge tradeall.py si NU scrie in cache_24price_{symbol}.json (fisierul
LIVE folosit de tradeall pentru decizii) — scrie separat, in
cachedb/cache_24price_long_{symbol}.json. Proces SEPARAT, cu propria
conexiune WS (stream public de piata, fara chei API) — sigur sa ruleze in
paralel cu tradeall.py; reutilizeaza Cache24PriceManager (cacheManager.py)
neschimbat, doar cu KEEP_HOURS suprascris per instanta (deja documentat in
cod ca fiind suportat: "configurabil per instanta daca e nevoie").

Rulare (lasa-l sa ruleze continuu, la fel ca tradeall.py insusi):
    ./tradeall_price_archiver.py --symbols BTCUSDC,TAOUSDC --months 6

Apoi, dupa ce s-a acumulat destul istoric dens:
    ./tradeall_backtest.py --symbol BTCUSDC --start <data> --source cache24 \\
        --cache24-file cachedb/cache_24price_long_BTCUSDC.json
"""
import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import cacheManager as cm
from binance_api import bapi_ws

CACHEDB_DIR = os.path.join(ROOT, "cachedb")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", default="BTCUSDC,TAOUSDC",
                    help="listă separată prin virgulă (implicit: BTCUSDC,TAOUSDC)")
    p.add_argument("--months", type=float, default=6.0, help="retentie, in luni (implicit 6)")
    p.add_argument("--sync-ts", type=float, default=0.8,
                    help="cadenta nominala de sampling, ca la tradeall.py (implicit 0.8s)")
    args = p.parse_args()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    keep_hours = args.months * 30 * 24

    os.makedirs(CACHEDB_DIR, exist_ok=True)
    cm.enable_real_ws_event_sync()
    current_price_mgr = cm.get_current_price_manager(
        ws_manager=bapi_ws.get_ws_manager(),
        sync_ts=args.sync_ts,
    )

    for symbol in symbols:
        filename = os.path.join(CACHEDB_DIR, f"cache_24price_long_{symbol}.json")
        cache = cm.Cache24PriceManager(sync_ts=args.sync_ts, symbols=[symbol], filename=filename)
        cache.KEEP_HOURS = keep_hours   # override per-instanta (suportat explicit in cod)
        cache.enable_save_state_to_file()   # implicit False la constructie — fara asta nu scrie pe disc
        current_price_mgr.subscribe_price(cache)

    print(f"[tradeall_price_archiver] pornit: {symbols} | retentie {args.months} luni "
          f"({keep_hours:.0f}h)")
    print("[tradeall_price_archiver] scriu in cachedb/cache_24price_long_<symbol>.json "
          "(separat de cache-ul live 24h al tradeall.py)")
    print("[tradeall_price_archiver] Ctrl+C opreste.")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[tradeall_price_archiver] oprit.")


if __name__ == "__main__":
    main()
