#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
ipo.py — watcher SpaceX (SPCX) cu auto-buy pe Trading 212.

Cum porneste, verifica periodic daca SPCX s-a LANSAT (a inceput sa se
tranzactioneze cu adevarat) si, in acel moment, plaseaza automat un ordin
LIMIT de cumparare pe Trading 212, la un pret maxim fix (plafon).

De ce "tranzactionare reala" si nu "apare pe T212":
    SPCX_US_EQ exista DEJA in metadata T212 ca placeholder de IPO (pret fix
    135 USD, volum 0). Deci simpla prezenta in T212 nu inseamna ca poti
    cumpara. Declansatorul corect e momentul cand piata se deschide efectiv
    (volum > 0, pret proaspat, stare activa) — atunci T212 accepta ordinul.

Module:
    ipo_common.py     utilitare (log, .env, http)
    market_data.py    pret/curs/detectie tranzactionare (Yahoo)
    t212_client.py    client API Trading 212
    ipo_notify.py     notificari (ntfy + email via AlertNotifier)
    order_manager.py  calcul cantitate, plasare cu retry, polling status

Config: totul in .env (vezi cheile ORDER_*). Comenzi:
    python3 ipo.py                          # porneste watcher-ul
    python3 ipo.py --test-notify all        # testeaza notificarile
    python3 ipo.py --test-order NVDA_US_EQ  # testeaza un ordin real (proba)
    python3 ipo.py --find-ticker nvidia     # gaseste ticker-ul exact in T212
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from ipo_common import load_dotenv, log, now_str, float_env
from market_data import check_market
from t212_client import T212Client
from ipo_notify import notify
from order_manager import (
    resolve_quantity,
    place_order_with_retry,
    order_already_placed,
    ORDER_MARKER,
)
from strategy import Strategy, StratParams

# --- config SPCX ---
TICKER = "SPCX"
NAME_PATTERNS = ("spacex", "space exploration")
POLL_SECONDS = 90


# ---------------------------------------------------------------------------
# Selectie determinista a instrumentului SPCX (refuza daca e ambiguu)
# ---------------------------------------------------------------------------
# Produse derivate care contin "SpaceX/Space Exploration" in nume dar NU sunt
# actiunea reala (leverage / short / optiuni). Le excludem ferm.
_DERIVATIVE_KEYWORDS = (
    "leverage", "short", "long", "graniteshares", "incomeshares",
    "options", "3x", "2x", "1x", "-1x", "-3x", "etp", "etn",
)


def pick_spcx(hits: list[dict]) -> dict | None:
    """Alege instrumentul SPCX REAL din lista de match-uri.

    Reguli stricte (e vorba de bani reali):
      * ticker-ul trebuie sa inceapa cu 'SPCX' (actiunea reala, nu ETF derivat),
      * numele NU contine cuvinte de produs derivat (leverage/short/options...),
      * se prefera USD; daca raman mai multe candidate, alege unicul *_US_EQ,
        altfel refuza (None) ca sa nu ghicim.
    """
    def is_real(h: dict) -> bool:
        t  = str(h.get("ticker", "")).upper()
        nm = (str(h.get("name", "")) + " " + str(h.get("shortName", ""))).lower()
        if not t.startswith("SPCX"):
            return False
        if any(k in nm for k in _DERIVATIVE_KEYWORDS):
            return False
        return True

    candidates = [h for h in hits if is_real(h)]
    if not candidates:
        return None

    usd = [h for h in candidates if h.get("currencyCode") == "USD"]
    pool = usd or candidates
    if len(pool) == 1:
        return pool[0]

    # mai multe ramase -> accepta doar daca exact unul e *_US_EQ
    us_eq = [h for h in pool if str(h.get("ticker", "")).upper().endswith("_US_EQ")]
    return us_eq[0] if len(us_eq) == 1 else None


# ---------------------------------------------------------------------------
# Fereastra orelor de piata US
# ---------------------------------------------------------------------------
def in_market_window() -> bool:
    from ipo_common import ET
    from datetime import datetime
    n = datetime.now(ET)
    if n.weekday() >= 5:
        return False
    minutes = n.hour * 60 + n.minute
    return 9 * 60 <= minutes <= 16 * 60 + 30


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    env_file = os.environ.get("ENV_FILE", ".env")
    for i, a in enumerate(sys.argv):
        if a == "--env-file" and i + 1 < len(sys.argv):
            env_file = sys.argv[i + 1]
    load_dotenv(env_file)

    ap = argparse.ArgumentParser(description="Watcher SpaceX (SPCX) cu auto-buy pe T212.")
    ap.add_argument("--env-file",          default=env_file)
    ap.add_argument("--ticker",            default=TICKER)
    ap.add_argument("--interval",          type=int, default=POLL_SECONDS)
    ap.add_argument("--desktop",           action="store_true")
    ap.add_argument("--market-hours-only", action="store_true")
    ap.add_argument("--execute",           action="store_true",
                    help="Override: ordin real (suprascrie ORDER_EXECUTE din .env)")
    ap.add_argument("--test-notify",       choices=["market", "t212", "all"], metavar="WHAT")
    ap.add_argument("--test-order",        metavar="T212_TICKER",
                    help="Testeaza un ordin pe ticker dat si iese (ex: NVDA_US_EQ)")
    ap.add_argument("--test-strategy",     metavar="T212_TICKER",
                    help="Ruleaza strategia ACUM pe ticker dat (ex: NVDA_US_EQ), "
                         "paper daca STRAT_EXECUTE!=true. Pentru validare pe live.")
    ap.add_argument("--find-ticker",       metavar="NUME",
                    help="Cauta instrument in T212 dupa nume/simbol")
    args = ap.parse_args()

    # --- config din .env ---
    t212_key         = os.environ.get("T212_API_KEY")
    t212_secret      = os.environ.get("T212_API_SECRET")
    t212_env         = os.environ.get("T212_ENV", "live").strip().lower()
    order_price      = float_env("ORDER_PRICE")
    order_qty        = float_env("ORDER_QTY")
    order_budget_ron = float_env("ORDER_BUDGET_RON")
    _val             = os.environ.get("ORDER_VALIDITY", "DAY").strip().upper()
    order_validity   = "GOOD_TILL_CANCEL" if _val in ("GTC", "GOOD_TILL_CANCEL") else "DAY"
    dry_run          = not (args.execute or
                            os.environ.get("ORDER_EXECUTE", "false").lower() == "true")
    interval         = max(args.interval, 60)

    if not t212_key:
        log("! T212_API_KEY lipsa in .env — nu pot continua.")
        return 1
    client = T212Client(t212_key, t212_secret, env=t212_env)

    log("=== SpaceX (SPCX) watcher ===")
    log(f"    ticker       : {args.ticker}")
    log(f"    interval     : {interval}s")
    log(f"    mediu T212   : {t212_env.upper()}{'  ⚠ BANI REALI' if t212_env != 'demo' and not dry_run else ''}")
    log(f"    ntfy         : {os.environ.get('NTFY_TOPIC') or '(dezactivat)'}")
    log(f"    email        : {os.environ.get('ALERT_TO_EMAIL') or '(dezactivat)'}")
    if order_price:
        log(f"    plafon ordin : max {order_price} USD/actiune  "
            f"({'REAL' if not dry_run else 'DRY-RUN'})  validity={order_validity}")
    else:
        log("    plafon ordin : (ORDER_PRICE nesetat — doar notificare, fara auto-buy)")

    # --- moduri one-shot ---
    if args.find_ticker:
        return _cmd_find_ticker(client, args.find_ticker)
    if args.test_notify:
        return _cmd_test_notify(args.test_notify, args.desktop)
    if args.test_order:
        return _cmd_test_order(client, args, order_price, order_qty,
                               order_budget_ron, order_validity, dry_run)
    if args.test_strategy:
        strat_dry = not (os.environ.get("STRAT_EXECUTE", "false").lower() == "true")
        log(f"[TEST STRATEGY] {args.test_strategy}  {'PAPER' if strat_dry else '⚠ REAL'}")
        Strategy(client, args.test_strategy, StratParams.from_env(),
                 dry_run=strat_dry, desktop=args.desktop).run()
        return 0

    # --- watcher principal ---
    return _watch_loop(client, args, order_price, order_qty, order_budget_ron,
                       order_validity, dry_run, interval)


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


def _cmd_test_notify(what: str, desktop: bool) -> int:
    ts = now_str()
    if what in ("market", "all"):
        log("[TEST] notificare market...")
        notify(title="[TEST] SpaceX (SPCX) a inceput tranzactionarea!",
               body=f"SPCX SE TRANZACTIONEAZA pe NASDAQ.\nLast price: 99.99 USD\n{ts}",
               source="NASDAQ", price=99.99, desktop=desktop)
    if what in ("t212", "all"):
        log("[TEST] notificare T212/order...")
        notify(title="[TEST] Ordin SPCX plasat pe T212!",
               body=f"LIMIT SPCX_US_EQ qty=0.73 @ 150 USD\n{ts}",
               source="T212 order", desktop=desktop)
    log("[TEST] Gata.")
    return 0


def _cmd_test_order(client, args, order_price, order_qty, order_budget_ron,
                    order_validity, dry_run) -> int:
    if not order_price:
        log("! ORDER_PRICE lipsa in .env"); return 1
    if not order_qty and not order_budget_ron:
        log("! ORDER_QTY sau ORDER_BUDGET_RON lipsa in .env"); return 1
    qty = resolve_quantity(order_price, order_qty, order_budget_ron)
    if not qty or qty <= 0:
        log("! cantitate invalida"); return 1
    # proba: NU scriem markerul, o singura incercare
    ok = place_order_with_retry(
        client, args.test_order, qty, order_price, order_validity, dry_run,
        desktop=args.desktop, max_retries=1, write_marker=False,
    )
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Bucla watcher
# ---------------------------------------------------------------------------
def _watch_loop(client, args, order_price, order_qty, order_budget_ron,
                order_validity, dry_run, interval) -> int:
    strat_enabled = os.environ.get("STRAT_ENABLED", "false").strip().lower() == "true"

    # marker-ul anti-dublura e doar pentru modul "un singur ordin".
    # In modul strategie, persistenta o face strategy.py (state file propriu).
    if not strat_enabled and order_already_placed():
        log(f"    [state] marker {ORDER_MARKER} existent — ordin deja plasat anterior.")
        log("    Sterge fisierul marker daca vrei sa re-armezi auto-buy. Ies.")
        return 0

    if strat_enabled:
        log("    Mod: STRATEGIE (DCA + take-profit) dupa lansare")
    else:
        log("    Mod: un singur ordin LIMIT la lansare")
    log("    Astept lansarea SPCX... (Ctrl+C ca sa opresc)")
    market_announced = False
    ordered = False

    try:
        while True:
            if args.market_hours_only and not in_market_window():
                time.sleep(min(interval * 5, 600))
                continue

            m = check_market(args.ticker)
            trading = bool(m and m.get("trading"))

            # anunta o singura data ca s-a deschis piata
            if trading and not market_announced:
                market_announced = True
                ts = now_str()
                body = (f"SPCX SE TRANZACTIONEAZA pe {m.get('exchange')}.\n"
                        f"Pret: {m['price']} {m.get('currency') or ''}  "
                        f"(vol {m.get('volume')}, {m.get('state')})\nMoment: {ts}")
                log("############################################")
                log(">>> SPCX S-A LANSAT — TRANZACTIONARE REALA <<<")
                log(body.replace("\n", " | "))
                log("############################################")
                notify(title="SpaceX (SPCX) a inceput tranzactionarea!",
                       body=body, source=m.get("exchange") or "NASDAQ",
                       price=m.get("price"), desktop=args.desktop)

            # plaseaza ordinul / porneste strategia cand piata s-a deschis
            if trading and not ordered and (order_price or strat_enabled):
                hits = client.search_instruments(TICKER, NAME_PATTERNS)
                chosen = pick_spcx(hits) if hits else None
                expected_isin = os.environ.get("ORDER_EXPECTED_ISIN", "").strip()
                if chosen and expected_isin and str(chosen.get("isin", "")) != expected_isin:
                    log(f"  ! [ORDER] ISIN {chosen.get('isin')} != asteptat {expected_isin} "
                        f"— REFUZ ordinul (posibil instrument gresit).")
                    notify(title="⚠ SPCX: ISIN nepotrivit — verifica manual!",
                           body=f"Gasit {chosen.get('ticker')} isin={chosen.get('isin')}, "
                                f"asteptam {expected_isin}. Ordin automat anulat.",
                           source="T212 order", desktop=args.desktop)
                    chosen = None
                if not chosen:
                    log("  ! SPCX tranzactionabil pe piata, dar T212 nu-l listeaza clar inca — reincerc.")
                elif strat_enabled:
                    # preda controlul motorului de strategie (ruleaza pana la Ctrl+C)
                    t212_ticker = chosen.get("ticker", "SPCX")
                    log(f"  [ORDER] Instrument T212 confirmat: {t212_ticker} — pornesc STRATEGIA")
                    strat_dry = not (os.environ.get("STRAT_EXECUTE", "false").lower() == "true")
                    strat = Strategy(client, t212_ticker, StratParams.from_env(),
                                     dry_run=strat_dry, desktop=args.desktop)
                    strat.run()
                    return 0  # strategia s-a oprit (Ctrl+C)
                else:
                    t212_ticker = chosen.get("ticker", "SPCX")
                    log(f"  [ORDER] Instrument T212 confirmat: {t212_ticker}")
                    qty = resolve_quantity(order_price, order_qty, order_budget_ron)
                    if qty and qty > 0:
                        ok = place_order_with_retry(
                            client, t212_ticker, qty, order_price, order_validity,
                            dry_run, desktop=args.desktop,
                        )
                        if ok:
                            ordered = True
                    else:
                        log("  ! qty/budget lipsesc — ordin NESENT")

            # conditii de oprire (mod un-singur-ordin)
            if not strat_enabled and order_price and ordered:
                break
            if not strat_enabled and not order_price and market_announced:
                break  # mod notificare-only: gata dupa ce am anuntat lansarea

            # heartbeat
            if m and not trading:
                log(f"ping - astept lansarea  |  pret={m.get('price')} "
                    f"vol={m.get('volume')} state={m.get('state')} age={m.get('age_min')}min")
            elif not m:
                log("ping - simbol indisponibil pe feed")
            time.sleep(interval)

    except KeyboardInterrupt:
        log("Oprit manual.")
        return 130

    log("=== Gata. ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
