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
    ./offline/backtests/tradeall.py --symbol BTCUSDC --start <data> --source cache24 \\
        --cache24-file cachedb/cache_24price_long_BTCUSDC.jsonl
"""
import argparse
import math
import os
import re
import signal
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

import cacheManager as cm
from binance_api import bapi_ws
from botcore import single_instance

CACHEDB_DIR = os.path.join(ROOT, "cachedb")
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{3,24}$")


def _positive_finite(value, name, *, minimum=0.001):
    value = float(value)
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} trebuie sa fie finit si >= {minimum}")
    return value


def _symbols(raw):
    values = []
    for item in str(raw).split(","):
        symbol = item.strip().upper()
        if not symbol:
            continue
        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError(f"simbol invalid: {symbol!r}")
        if symbol not in values:
            values.append(symbol)
    if not values:
        raise ValueError("lista de simboluri nu poate fi goala")
    return values


def _shutdown(caches, current_price_mgr, ws_module=bapi_ws):
    """Stop producers, flush every pending tick, then close shared resources."""
    for cache in caches:
        current_price_mgr.unsubscribe_price(cache)
    for cache in caches:
        cache.shutdown()
        cache.save_state_to_file()
    current_price_mgr.shutdown()
    ws_module.bapi_ws_manager.stop()


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
    try:
        symbols = _symbols(args.symbols)
        months = _positive_finite(args.months, "--months")
        sync_ts = _positive_finite(args.sync_ts, "--sync-ts", minimum=0.1)
        save_every = _positive_finite(args.save_every, "--save-every", minimum=1.0)
    except (TypeError, ValueError) as exc:
        p.error(str(exc))
    keep_hours = months * 30 * 24

    single_instance("tradeall_price_archiver")
    stop_event = threading.Event()

    def request_stop(signum, _frame):
        print(f"[tradeall_price_archiver] semnal {signum}; flush si oprire...", flush=True)
        stop_event.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    os.makedirs(CACHEDB_DIR, exist_ok=True)
    cm.enable_real_ws_event_sync()
    current_price_mgr = cm.get_current_price_manager(
        ws_manager=bapi_ws.get_ws_manager(),
        sync_ts=sync_ts,
    )

    caches = []
    for symbol in symbols:
        filename = os.path.join(CACHEDB_DIR, f"cache_24price_long_{symbol}.jsonl")
        cache = cm.Cache24LongPriceManager(sync_ts=save_every, symbols=[symbol], filename=filename)
        cache.KEEP_HOURS = keep_hours   # per-instance override explicitly supported by the class
        cache.RETENTION_DAYS = keep_hours / 24.0
        cache.enable_save_state_to_file()   # defaults to False; required for disk writes
        cache.periodic_sync(save_every, save_state=True)
        current_price_mgr.subscribe_price(cache)
        caches.append(cache)

    print(f"[tradeall_price_archiver] pornit: {symbols} | retentie {months} luni "
          f"({keep_hours:.0f}h) | scriere pe disc la {save_every:.0f}s", flush=True)
    print("[tradeall_price_archiver] scriu in cachedb/cache_24price_long_<symbol>.jsonl "
          "(separat de cache-ul live 24h al tradeall.py)")
    print("[tradeall_price_archiver] Ctrl+C opreste.")

    try:
        while not stop_event.wait(60):
            print("[tradeall_price_archiver] heartbeat", flush=True)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        _shutdown(caches, current_price_mgr)
        print("\n[tradeall_price_archiver] oprit dupa flush.", flush=True)


if __name__ == "__main__":
    main()
