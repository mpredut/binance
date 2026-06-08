#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
ipo.py — watcher + auto-trade generic pe Trading 212 (orice simbol din .env).

Instrumentul se configureaza in .env (NU mai e hardcodat SPCX):
    T212_TICKER=NVDA_US_EQ     instrumentul exact pe T212 (obligatoriu)
    YAHOO_SYMBOL=NVDA          simbol Yahoo pt pret (optional; derivat din T212_TICKER)
    SYMBOL_LABEL=NVIDIA        eticheta pt afisare/notificari (optional)
    WAIT_FOR_LAUNCH=false      false = se tranzactioneaza deja; true = IPO (asteapta lansarea)
    EXPECTED_ISIN=US67066G1040 optional: refuza daca ISIN-ul nu se potriveste

Doua moduri de tranzactionare (dupa lansare / imediat):
    STRAT_ENABLED=true   -> strategie DCA + take-profit (vezi strategy.py)
    STRAT_ENABLED=false  -> un singur ordin LIMIT (vezi ORDER_* / order_manager.py)

Comenzi:
    python3 ipo.py                          # ruleaza dupa config-ul din .env
    python3 ipo.py --paper                  # forteaza PAPER (test sigur, fara bani)
    python3 ipo.py --symbol NVDA_US_EQ      # override instrument pt aceasta rulare
    python3 ipo.py --test-notify all        # testeaza notificarile
    python3 ipo.py --test-order NVDA_US_EQ  # testeaza un singur ordin
    python3 ipo.py --find-ticker nvidia     # gaseste ticker-ul exact in T212
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from ipo_common import load_dotenv, log, now_str, float_env, ET
from market_data import check_market, t212_to_yahoo
from t212_client import T212Client
from ipo_notify import notify
from order_manager import resolve_quantity, place_order_with_retry
from strategy import Strategy, StratParams

POLL_SECONDS = 60


# ---------------------------------------------------------------------------
# Helperi
# ---------------------------------------------------------------------------
def in_market_window() -> bool:
    """True intre ~9:00 si ~16:30 ET, zile lucratoare."""
    from datetime import datetime
    n = datetime.now(ET)
    if n.weekday() >= 5:
        return False
    minutes = n.hour * 60 + n.minute
    return 9 * 60 <= minutes <= 16 * 60 + 30


def verify_instrument(client: T212Client, ticker: str, expected_isin: str) -> bool:
    """Verificare best-effort: daca EXPECTED_ISIN e setat, confirma ca ticker-ul
    are acel ISIN. Returneaza False DOAR la nepotrivire dovedita (instrument gresit)."""
    if not expected_isin:
        return True
    instruments = client.list_instruments()
    if not instruments:
        log("  ! nu pot verifica ISIN (metadata indisponibila) — continui pe ticker explicit")
        return True
    match = next((i for i in instruments if str(i.get("ticker", "")).upper() == ticker.upper()), None)
    if not match:
        log(f"  ! {ticker} nu apare in metadata T212 inca — continui (poate fi intarziere)")
        return True
    if str(match.get("isin", "")) != expected_isin:
        log(f"  ! ISIN {match.get('isin')} != asteptat {expected_isin} — OPRESC (instrument gresit).")
        notify(title=f"⚠ {ticker}: ISIN nepotrivit!",
               body=f"Gasit isin={match.get('isin')}, asteptam {expected_isin}. Nu tranzactionez.",
               source="verify")
        return False
    log(f"  [verify] {ticker} confirmat (isin {expected_isin})")
    return True


# ---------------------------------------------------------------------------
# Pornire tranzactionare (strategie sau ordin unic)
# ---------------------------------------------------------------------------
def start_trading(client, t212_ticker, label, strat_enabled, strat_dry,
                  order_price, order_qty, order_budget_ron, order_validity,
                  order_dry, desktop) -> int:
    if strat_enabled:
        log(f"  Pornesc STRATEGIA pe {t212_ticker} ({'PAPER' if strat_dry else '⚠ REAL'})")
        Strategy(client, t212_ticker, StratParams.from_env(),
                 dry_run=strat_dry, desktop=desktop).run()
        return 0

    if order_price:
        qty = resolve_quantity(order_price, order_qty, order_budget_ron)
        if not qty or qty <= 0:
            log("  ! qty/budget invalid — ordin NESENT")
            return 1
        ok = place_order_with_retry(client, t212_ticker, qty, order_price,
                                    order_validity, order_dry, desktop=desktop)
        return 0 if ok else 1

    log("  ! nici STRAT_ENABLED, nici ORDER_PRICE — nimic de tranzactionat.")
    return 1


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    env_file = os.environ.get("ENV_FILE", ".env")
    for i, a in enumerate(sys.argv):
        if a == "--env-file" and i + 1 < len(sys.argv):
            env_file = sys.argv[i + 1]
    load_dotenv(env_file)

    ap = argparse.ArgumentParser(description="Watcher + auto-trade generic pe T212.")
    ap.add_argument("--env-file",          default=env_file)
    ap.add_argument("--symbol",            metavar="T212_TICKER",
                    help="Override instrument (altfel din .env T212_TICKER)")
    ap.add_argument("--interval",          type=int, default=POLL_SECONDS)
    ap.add_argument("--desktop",           action="store_true")
    ap.add_argument("--market-hours-only", action="store_true")
    ap.add_argument("--skip-wait",         action="store_true",
                    help="Sari peste verificarea de lansare (porneste direct chiar daca feed-ul zice ca nu se tranzactioneaza inca)")
    ap.add_argument("--paper",             action="store_true",
                    help="Forteaza PAPER (test sigur, fara bani), indiferent de .env")
    ap.add_argument("--execute",           action="store_true",
                    help="Override: ordin real (mod ordin-unic)")
    ap.add_argument("--test-notify",       choices=["market", "trade", "all"], metavar="WHAT")
    ap.add_argument("--test-order",        metavar="T212_TICKER",
                    help="Testeaza un singur ordin pe ticker dat si iese")
    ap.add_argument("--test-strategy",     metavar="T212_TICKER",
                    help="Ruleaza strategia ACUM pe ticker dat (paper daca STRAT_EXECUTE!=true)")
    ap.add_argument("--find-ticker",       metavar="NUME",
                    help="Cauta instrument in T212 dupa nume/simbol")
    args = ap.parse_args()

    # --- config din .env ---
    t212_key    = os.environ.get("T212_API_KEY")
    t212_secret = os.environ.get("T212_API_SECRET")
    t212_env    = os.environ.get("T212_ENV", "live").strip().lower()
    if not t212_key:
        log("! T212_API_KEY lipsa in .env — nu pot continua.")
        return 1
    client = T212Client(t212_key, t212_secret, env=t212_env)

    # instrument generic
    t212_ticker  = (args.symbol or os.environ.get("T212_TICKER") or "").strip()
    yahoo_symbol = os.environ.get("YAHOO_SYMBOL") or (t212_to_yahoo(t212_ticker) if t212_ticker else "")
    label        = os.environ.get("SYMBOL_LABEL") or yahoo_symbol or t212_ticker
    expected_isin = os.environ.get("EXPECTED_ISIN", "").strip()

    # strategie / ordin
    strat_enabled = os.environ.get("STRAT_ENABLED", "false").strip().lower() == "true"
    strat_dry     = args.paper or not (os.environ.get("STRAT_EXECUTE", "false").lower() == "true")
    order_price      = float_env("ORDER_PRICE")
    order_qty        = float_env("ORDER_QTY")
    order_budget_ron = float_env("ORDER_BUDGET_RON")
    _val             = os.environ.get("ORDER_VALIDITY", "DAY").strip().upper()
    order_validity   = "GOOD_TILL_CANCEL" if _val in ("GTC", "GOOD_TILL_CANCEL") else "DAY"
    order_dry        = args.paper or not (args.execute or
                                          os.environ.get("ORDER_EXECUTE", "false").lower() == "true")
    interval         = max(args.interval, 30)

    # --- comenzi one-shot ---
    if args.find_ticker:
        return _cmd_find_ticker(client, args.find_ticker)
    if args.test_notify:
        return _cmd_test_notify(args.test_notify, label, args.desktop)
    if args.test_order:
        return _cmd_test_order(client, args.test_order, order_price, order_qty,
                               order_budget_ron, order_validity, order_dry, args.desktop)
    if args.test_strategy:
        log(f"[TEST STRATEGY] {args.test_strategy}  {'PAPER' if strat_dry else '⚠ REAL'}")
        Strategy(client, args.test_strategy, StratParams.from_env(),
                 dry_run=strat_dry, desktop=args.desktop).run()
        return 0

    # --- banner ---
    if not t212_ticker:
        log("! T212_TICKER lipsa in .env (sau foloseste --symbol). Nu stiu ce sa tranzactionez.")
        return 1
    log("=== Watcher T212 ===")
    log(f"    instrument   : {label}  ({t212_ticker}, pret via {yahoo_symbol})")
    log(f"    mediu T212   : {t212_env.upper()}")
    log(f"    mod          : {'STRATEGIE (DCA+TP)' if strat_enabled else 'ordin unic'}")
    log(f"    executie     : {'PAPER (fara bani)' if (strat_dry if strat_enabled else order_dry) else '⚠ REAL — BANI ADEVARATI'}")
    log(f"    lansare      : verific pana {label} e lansat (deja-listat: imediat; IPO: la deschidere)")
    log(f"    ntfy/email   : {os.environ.get('NTFY_TOPIC') or '-'} / {os.environ.get('ALERT_TO_EMAIL') or '-'}")

    # --- PRE-FLIGHT: confirma instrumentul la PORNIRE (prinde erorile de config imediat,
    #     nu dupa zile de asteptare pana la lansare). ISIN gresit -> opreste acum.
    #     Daca tickerul nu e inca in metadata (IPO neinceput), doar avertizeaza si continua.
    log("    pre-flight: verific instrumentul pe T212...")
    if not verify_instrument(client, t212_ticker, expected_isin):
        return 1

    # --- Mecanism IDENTIC pentru ORICE simbol: astept pana instrumentul e LANSAT
    #     (are volum real). NVDA -> trece imediat; SPCX -> asteapta lansarea reala.
    #     --skip-wait sare peste (de urgenta).
    if not args.skip_wait:
        if not _wait_for_launch(args, yahoo_symbol, label, interval):
            return 130  # intrerupt

    # --- verificare FINALA dupa lansare (prinde reutilizarea de ticker), apoi tranzactioneaza ---
    if not verify_instrument(client, t212_ticker, expected_isin):
        return 1

    try:
        return start_trading(client, t212_ticker, label, strat_enabled, strat_dry,
                             order_price, order_qty, order_budget_ron, order_validity,
                             order_dry, args.desktop)
    except KeyboardInterrupt:
        log("Oprit manual.")
        return 130


def _wait_for_launch(args, yahoo_symbol, label, interval) -> bool:
    """Asteapta pana cand simbolul se tranzactioneaza cu adevarat. False daca intrerupt."""
    log(f"    Astept lansarea {label}... (Ctrl+C ca sa opresc)")
    try:
        while True:
            if args.market_hours_only and not in_market_window():
                time.sleep(min(interval * 5, 600))
                continue
            m = check_market(yahoo_symbol)
            # 'launched' = instrumentul a tranzactionat real (are volum), chiar daca
            # piata e inchisa acum. Asa, o actiune deja listata (NVDA) porneste imediat,
            # iar un placeholder de IPO (SPCX, volum 0) e asteptat pana se deschide.
            if m and m.get("launched"):
                ts = now_str()
                now_open = "se tranzactioneaza ACUM" if m.get("trading") else f"piata {m.get('state')}"
                body = (f"{label} e DISPONIBIL pe {m.get('exchange')} ({now_open}).\n"
                        f"Pret: {m['price']} {m.get('currency') or ''}  "
                        f"(vol {m.get('volume')}, {m.get('state')})\n{ts}")
                log("############################################")
                log(f">>> {label} E DISPONIBIL — pornesc tranzactionarea <<<")
                log(body.replace("\n", " | "))
                log("############################################")
                notify(title=f"{label} disponibil — pornesc!", body=body,
                       source=m.get("exchange") or "market", price=m.get("price"),
                       desktop=args.desktop)
                return True
            if m:
                log(f"ping - astept lansarea  |  pret={m.get('price')} vol={m.get('volume')} "
                    f"state={m.get('state')} age={m.get('age_min')}min")
            else:
                log("ping - simbol indisponibil pe feed")
            time.sleep(interval)
    except KeyboardInterrupt:
        log("Oprit manual.")
        return False


# ---------------------------------------------------------------------------
# Comenzi one-shot
# ---------------------------------------------------------------------------
def _cmd_find_ticker(client: T212Client, query: str) -> int:
    log(f"[FIND] Caut '{query}' in instrumentele T212...")
    instruments = client.list_instruments()
    if instruments is None:
        log("! nu pot lista instrumentele (auth/retea)")
        return 1
    q = query.lower()
    hits = [i for i in instruments
            if q in str(i.get("ticker", "")).lower()
            or q in str(i.get("name", "")).lower()
            or q in str(i.get("shortName", "")).lower()]
    for h in hits:
        log(f"  ticker={h.get('ticker'):<20} name={h.get('name')}  "
            f"currency={h.get('currencyCode')}  isin={h.get('isin')}")
    if not hits:
        log(f"  Niciun rezultat pentru '{query}'")
    return 0


def _cmd_test_notify(what: str, label: str, desktop: bool) -> int:
    ts = now_str()
    if what in ("market", "all"):
        notify(title=f"[TEST] {label} a inceput tranzactionarea!",
               body=f"{label} SE TRANZACTIONEAZA.\nLast price: 99.99 USD\n{ts}",
               source="market", price=99.99, desktop=desktop)
    if what in ("trade", "all"):
        notify(title=f"[TEST] Ordin {label} plasat pe T212!",
               body=f"LIMIT qty=0.5 @ 99 USD\n{ts}", source="trade", desktop=desktop)
    log("[TEST] Gata.")
    return 0


def _cmd_test_order(client, ticker, order_price, order_qty, order_budget_ron,
                    order_validity, order_dry, desktop) -> int:
    if not order_price:
        log("! ORDER_PRICE lipsa in .env"); return 1
    if not order_qty and not order_budget_ron:
        log("! ORDER_QTY sau ORDER_BUDGET_RON lipsa in .env"); return 1
    qty = resolve_quantity(order_price, order_qty, order_budget_ron)
    if not qty or qty <= 0:
        log("! cantitate invalida"); return 1
    ok = place_order_with_retry(client, ticker, qty, order_price, order_validity,
                                order_dry, desktop=desktop, max_retries=1, write_marker=False)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
