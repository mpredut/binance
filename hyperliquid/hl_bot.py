#!/usr/bin/env python3
"""
hl_bot.py — watcher + auto-trade DCA/take-profit pe Hyperliquid (PERP long-only).

IMPORTANT: ruleaza cu python-ul din venv-ul cu SDK:
    /home/mariusp/binance/.venv/bin/python hl_bot.py
(sau: source ../.venv/bin/activate; python hl_bot.py)

Comenzi:
    ...python hl_bot.py                  # ruleaza dupa .env
    ...python hl_bot.py --paper          # PAPER (fara bani, fara wallet)
    ...python hl_bot.py --price          # pretul HYPE (public)
    ...python hl_bot.py --balance        # USDC disponibil (necesita HL_ACCOUNT_ADDRESS)
    ...python hl_bot.py --positions      # pozitia curenta
    ...python hl_bot.py --test-strategy HYPE
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from common import load_dotenv, log, now_str, float_env
from hl_client import HLClient, HLError
from market_data import get_price, coin_available
from notify import notify
from strategy import Strategy, StratParams

POLL_SECONDS = 60


def _build_client(need_wallet: bool) -> HLClient:
    mainnet = os.environ.get("HL_MAINNET", "true").strip().lower() != "false"
    secret = os.environ.get("HL_SECRET_KEY") if need_wallet else None
    addr = os.environ.get("HL_ACCOUNT_ADDRESS")
    return HLClient(secret_key=secret, account_address=addr, mainnet=mainnet)


def main() -> int:
    env_file = os.environ.get("ENV_FILE", ".env")
    for i, a in enumerate(sys.argv):
        if a == "--env-file" and i + 1 < len(sys.argv):
            env_file = sys.argv[i + 1]
    load_dotenv(env_file)

    ap = argparse.ArgumentParser(description="Bot DCA+TP pe Hyperliquid (perp long-only).")
    ap.add_argument("--env-file", default=env_file)
    ap.add_argument("--coin", help="Override moneda (altfel din .env HL_COIN)")
    ap.add_argument("--interval", type=int, default=POLL_SECONDS)
    ap.add_argument("--desktop", action="store_true")
    ap.add_argument("--skip-wait", action="store_true")
    ap.add_argument("--paper", action="store_true", help="PAPER (fara bani, fara wallet)")
    ap.add_argument("--price", action="store_true")
    ap.add_argument("--balance", action="store_true")
    ap.add_argument("--positions", action="store_true")
    ap.add_argument("--test-strategy", metavar="COIN")
    args = ap.parse_args()

    coin      = (args.coin or os.environ.get("HL_COIN") or "HYPE").strip()
    label     = os.environ.get("SYMBOL_LABEL") or coin
    leverage  = int(float_env("HL_LEVERAGE") or 1)
    strat_dry = args.paper or not (os.environ.get("STRAT_EXECUTE", "false").lower() == "true")
    interval  = max(args.interval, 15)

    # clientul: wallet necesar doar pt tranzactionare reala
    need_wallet = not strat_dry or args.balance or args.positions
    try:
        client = _build_client(need_wallet)
    except HLError as e:
        log(f"! {e}")
        return 1

    if args.price:
        p = get_price(client, coin); log(f"[PRICE] {coin} = {p}")
        return 0 if p else 1
    if args.balance:
        log(f"[BALANCE] USDC disponibil: {client.withdrawable()}")
        return 0
    if args.positions:
        szi, entry = client.position(coin)
        log(f"[POSITION] {coin}: size={szi} entryPx={entry}")
        return 0
    if args.test_strategy:
        log(f"[TEST] strategie {args.test_strategy}  {'PAPER' if strat_dry else '⚠ REAL'}")
        Strategy(client, args.test_strategy, StratParams.from_env(),
                 dry_run=strat_dry, desktop=args.desktop, leverage=leverage).run()
        return 0

    log("=== Hyperliquid bot ===")
    log(f"    coin         : {label}  ({coin} perp, levier {leverage}x)")
    log(f"    wallet       : {'da' if os.environ.get('HL_SECRET_KEY') else 'NU (doar public/paper)'}")
    log(f"    executie     : {'PAPER (fara bani)' if strat_dry else '⚠ REAL — BANI ADEVARATI'}")
    log(f"    ntfy/email   : {os.environ.get('NTFY_TOPIC') or '-'} / {os.environ.get('ALERT_TO_EMAIL') or '-'}")

    if not args.skip_wait:
        if not _wait_for_listing(client, coin, label, interval, args.desktop):
            return 130

    try:
        Strategy(client, coin, StratParams.from_env(), dry_run=strat_dry,
                 desktop=args.desktop, leverage=leverage).run()
        return 0
    except KeyboardInterrupt:
        log("Oprit manual."); return 130


def _wait_for_listing(client, coin, label, interval, desktop) -> bool:
    if coin_available(client, coin):
        log(f"  [verify] {coin} e disponibil pe Hyperliquid — pornesc.")
        return True
    log(f"    {coin} indisponibil pe Hyperliquid — astept... (Ctrl+C ca sa opresc)")
    try:
        while True:
            if coin_available(client, coin):
                p = get_price(client, coin)
                log(f">>> {label} disponibil pe Hyperliquid (pret {p}) — pornesc <<<")
                notify(title=f"{label} disponibil pe Hyperliquid!",
                       body=f"{coin} pret {p}\n{now_str()}", source="hyperliquid", price=p, desktop=desktop)
                return True
            log(f"ping - astept {coin}...")
            time.sleep(interval)
    except KeyboardInterrupt:
        log("Oprit manual."); return False


if __name__ == "__main__":
    raise SystemExit(main())
