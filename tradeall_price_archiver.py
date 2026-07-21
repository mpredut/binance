#!/usr/bin/env python3
"""
tradeall_price_archiver.py — captureaza pretul LIVE (acelasi stream WS
public ca tradeall.py) intr-un cache24 SEPARAT, cu retentie LUNGA (implicit
12 luni, --months) in loc de 24h. Scop: incepand de ACUM, acumuleaza date de
pret la rezolutie DENSA (~1s, ca live), pentru backtesting viitor mult mai
fidel decat cache_price_{symbol}.jsonl (istoricul existent, mult mai rar —
vezi caveat-ul din plan, sectiunea A5).

NU atinge tradeall.py si NU scrie in cache_24price_{symbol}.json (fisierul
LIVE folosit de tradeall pentru decizii) — scrie separat, in
cachedb/cache_24price_long_{symbol}.jsonl. Proces SEPARAT, cu propria
conexiune WS (stream public de piata, fara chei API) — sigur sa ruleze in
paralel cu tradeall.py.

21 iul: foloseste Cache24LongPriceManager (cacheManager.py) — clasa DEDICATA
acestui script, separata de Cache24PriceManager (cea din tradeall.py, complet
neatinsa). Persista JSONL (scriere incrementala) in loc de JSON complet
rescris la fiecare salvare — arhiva a ajuns la ~20MB/simbol si tot creste
spre cateva sute de MB la tinta de 6 luni; rescrierea completa avea un cost
care creste o data cu arhiva, JSONL scrie doar tick-urile noi.

Rulare (lasa-l sa ruleze continuu, la fel ca tradeall.py insusi):
    ./tradeall_price_archiver.py --symbols BTCUSDC,TAOUSDC --months 12

Apoi, dupa ce s-a acumulat destul istoric dens:
    ./tradeall_backtest.py --symbol BTCUSDC --start <data> --source cache24 \\
        --cache24-file cachedb/cache_24price_long_BTCUSDC.jsonl
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
    p.add_argument("--months", type=float, default=12.0,
                    help="retentie, in luni (implicit 12 — 21 iul, ridicat de la 6: dupa migrarea "
                         "la JSONL costul de scriere nu mai creste cu arhiva, iar spatiul disponibil "
                         "(14GB liber dupa curatarea logurilor) permite un istoric mai lung)")
    p.add_argument("--sync-ts", type=float, default=0.8,
                    help="cadenta nominala de sampling, ca la tradeall.py (implicit 0.8s)")
    p.add_argument("--save-every", type=float, default=60.0,
                    help="cadenta de SCRIERE pe disc a cache24_long (implicit 60s; NU sync_ts — "
                         "acela ramane rapid pt fallback-ul HTTP al pretului curent). "
                         "21 iul: la 0.8s (=sync_ts reutilizat gresit) rescria fisierul intreg "
                         "(19.6MB BTC) de ~75x/minut -> 72%% CPU sustinut, crescand pe masura ce "
                         "arhiva creste spre 6 luni.")
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
        filename = os.path.join(CACHEDB_DIR, f"cache_24price_long_{symbol}.jsonl")
        cache = cm.Cache24LongPriceManager(sync_ts=args.save_every, symbols=[symbol], filename=filename)
        cache.KEEP_HOURS = keep_hours   # override per-instanta (suportat explicit in cod)
        cache.enable_save_state_to_file()   # implicit False la constructie — fara asta nu scrie pe disc
        current_price_mgr.subscribe_price(cache)

    print(f"[tradeall_price_archiver] pornit: {symbols} | retentie {args.months} luni "
          f"({keep_hours:.0f}h) | scriere pe disc la {args.save_every:.0f}s")
    print("[tradeall_price_archiver] scriu in cachedb/cache_24price_long_<symbol>.jsonl "
          "(separat de cache-ul live 24h al tradeall.py)")
    print("[tradeall_price_archiver] Ctrl+C opreste.")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[tradeall_price_archiver] oprit.")


if __name__ == "__main__":
    main()
