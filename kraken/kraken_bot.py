#!/usr/bin/env python3
"""
kraken_bot.py — watcher + auto-trade DCA/take-profit pe Kraken (Spot).

Acelasi flux ca ipo.py (T212), dar pe Kraken. Perechea se configureaza in .env
(KRAKEN_PAIR). Daca perechea nu e inca listata pe Kraken (ex un SPCX viitor),
botul ASTEAPTA pana apare, apoi intra — identic cu logica SPCX de la T212.

Comenzi:
    python3 kraken_bot.py                 # ruleaza dupa .env
    python3 kraken_bot.py --paper         # forteaza PAPER (fara bani)
    python3 kraken_bot.py --pair HYPEEUR  # override pereche
    python3 kraken_bot.py --find-pair hype  # cauta perechea exacta pe Kraken
    python3 kraken_bot.py --price         # arata pretul curent
    python3 kraken_bot.py --balance       # arata soldurile (necesita chei)
    python3 kraken_bot.py --test-strategy HYPEEUR  # ruleaza strategia ACUM
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from common import load_dotenv, log, now_str, float_env
from kraken_client import KrakenClient, KrakenError
from market_data import get_price, pair_available
from notify import notify
from strategy import Strategy, StratParams

POLL_SECONDS = 60


def _build_client() -> KrakenClient:
    return KrakenClient(os.environ.get("KRAKEN_API_KEY"), os.environ.get("KRAKEN_API_SECRET"))


def main() -> int:
    env_file = os.environ.get("ENV_FILE", ".env")
    for i, a in enumerate(sys.argv):
        if a == "--env-file" and i + 1 < len(sys.argv):
            env_file = sys.argv[i + 1]
    load_dotenv(env_file)

    ap = argparse.ArgumentParser(description="Bot DCA+TP pe Kraken.")
    ap.add_argument("--env-file", default=env_file)
    ap.add_argument("--pair", help="Override pereche (altfel din .env KRAKEN_PAIR)")
    ap.add_argument("--interval", type=int, default=POLL_SECONDS)
    ap.add_argument("--desktop", action="store_true")
    ap.add_argument("--skip-wait", action="store_true", help="Sari peste asteptarea listarii")
    ap.add_argument("--paper", action="store_true", help="Forteaza PAPER (fara bani)")
    ap.add_argument("--find-pair", metavar="TERM", help="Cauta perechi pe Kraken")
    ap.add_argument("--price", action="store_true", help="Arata pretul curent si iese")
    ap.add_argument("--balance", action="store_true", help="Arata soldurile (necesita chei)")
    ap.add_argument("--test-strategy", metavar="PAIR", help="Ruleaza strategia ACUM pe perechea data")
    args = ap.parse_args()

    client = _build_client()

    pair        = (args.pair or os.environ.get("KRAKEN_PAIR") or "").strip()
    label       = os.environ.get("SYMBOL_LABEL") or pair
    strat_dry   = args.paper or not (os.environ.get("STRAT_EXECUTE", "false").lower() == "true")
    interval    = max(args.interval, 15)

    # --- comenzi one-shot ---
    if args.find_pair:
        return _cmd_find_pair(client, args.find_pair)
    if args.price:
        p = get_price(client, pair) if pair else None
        log(f"[PRICE] {pair} = {p}")
        return 0 if p else 1
    if args.balance:
        return _cmd_balance(client)
    if args.test_strategy:
        log(f"[TEST] strategie pe {args.test_strategy}  {'PAPER' if strat_dry else '⚠ REAL'}")
        Strategy(client, args.test_strategy, StratParams.from_env(),
                 dry_run=strat_dry, desktop=args.desktop).run()
        return 0

    if not pair:
        log("! KRAKEN_PAIR lipsa in .env (sau --pair). Nu stiu ce sa tranzactionez.")
        return 1

    log("=== Kraken bot ===")
    log(f"    pereche      : {label}  ({pair})")
    log(f"    chei         : {'da' if os.environ.get('KRAKEN_API_KEY') else 'NU (doar public/paper)'}")
    log(f"    executie     : {'PAPER (fara bani)' if strat_dry else '⚠ REAL — BANI ADEVARATI'}")
    log(f"    ntfy/email   : {os.environ.get('NTFY_TOPIC') or '-'} / {os.environ.get('ALERT_TO_EMAIL') or '-'}")

    # --- asteapta pana perechea e LISTATA si tranzactionabila (analog 'launch') ---
    if not args.skip_wait:
        if not _wait_for_listing(client, pair, label, interval, args.desktop):
            return 130

    # --- porneste strategia ---
    try:
        Strategy(client, pair, StratParams.from_env(), dry_run=strat_dry,
                 desktop=args.desktop).run()
        return 0
    except KeyboardInterrupt:
        log("Oprit manual.")
        return 130


def _wait_for_listing(client, pair, label, interval, desktop) -> bool:
    # pre-flight: daca e deja listata, pornim imediat
    info = pair_available(client, pair)
    if info:
        log(f"  [verify] {pair} e LISTAT si tranzactionabil — pornesc.")
        return True
    log(f"    {pair} inca nelistat pe Kraken — astept aparitia... (Ctrl+C ca sa opresc)")
    try:
        while True:
            info = pair_available(client, pair)
            if info:
                p = get_price(client, pair)
                body = f"{label} ({pair}) e disponibil pe Kraken. Pret: {p}\n{now_str()}"
                log("############################################")
                log(f">>> {label} LISTAT PE KRAKEN — pornesc <<<")
                log("############################################")
                notify(title=f"{label} listat pe Kraken!", body=body,
                       source="kraken", price=p, desktop=desktop)
                return True
            log(f"ping - astept listarea {pair}...")
            time.sleep(interval)
    except KeyboardInterrupt:
        log("Oprit manual.")
        return False


def _cmd_find_pair(client: KrakenClient, term: str) -> int:
    try:
        pairs = client.asset_pairs()
    except KrakenError as e:
        log(f"! {e}")
        return 1
    t = term.upper()
    hits = [(k, v) for k, v in pairs.items()
            if t in (k + str(v.get("altname")) + str(v.get("wsname")) + str(v.get("base"))).upper()]
    log(f"[FIND] '{term}' — {len(hits)} rezultate:")
    for k, v in hits[:20]:
        log(f"  altname={v.get('altname'):<12} wsname={v.get('wsname'):<14} "
            f"base={v.get('base')} quote={v.get('quote')} status={v.get('status')}")
    if not hits:
        log("  (niciuna)")
    return 0


def _cmd_balance(client: KrakenClient) -> int:
    try:
        bal = client.balance()
    except KrakenError as e:
        log(f"! balance: {e}")
        return 1
    log("=== Solduri Kraken ===")
    for asset, amt in bal.items():
        if float(amt) > 0:
            log(f"  {asset:<8} {amt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
