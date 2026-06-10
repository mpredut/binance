#!/usr/bin/env python3
"""
xstock_watch.py — watcher pt alocarea xStocks (ex. SPCX) pe Kraken.

Ce face, la fiecare verificare:
  1. BALANTA (privat): detecteaza ORICE activ NOU aparut in cont (alocarea poate
     veni sub orice cod — SPCXx, xSPCX...). Alerta speciala daca se potriveste
     XSTOCK_REGEX, alerta informativa altfel.
  2. PERECHI (public): detecteaza cand o pereche SPCX-like devine tranzactionabila
     prin API -> alerta "LISTAT" + instructiuni de pornire a botului cu adoptare.
  3. NIVELE DE PRET (dupa alocare, daca XSTOCK_ALLOC_PRICE e setat): alerta la
     +XSTOCK_TP_ALERT_PCT% / -XSTOCK_SL_ALERT_PCT% fata de pretul alocarii.
     Pret: perechea Kraken daca e listata, altfel subiacentul de pe Yahoo.

  python3 xstock_watch.py            # bucla continua
  python3 xstock_watch.py --once     # o singura verificare (test)
  python3 xstock_watch.py --status   # arata snapshot-ul curent si iese
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.request

from common import log, load_dotenv, float_env
from notify import notify
from kraken_client import KrakenClient, KrakenError

_HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(_HERE, "xstock_state.json")


# -- stare -------------------------------------------------------------------
def _load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError) as e:
            log(f"  ! nu pot citi starea ({e}) — pornesc curat")
    return {"known_assets": {}, "allocated": None, "pair": None,
            "alerted_pair": False, "alerted_tp": False, "alerted_sl": False}


def _save_state(st: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, indent=2)
    except OSError as e:
        log(f"  ! nu pot salva starea: {e}")


# -- pret subiacent (Yahoo) cat timp perechea nu e pe API ---------------------
def yahoo_last(sym: str) -> float | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (watch)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        res = (data.get("chart", {}).get("result") or [None])[0]
        return (res or {}).get("meta", {}).get("regularMarketPrice")
    except Exception as e:  # noqa: BLE001
        log(f"  ! yahoo {sym}: {e}")
        return None


# -- verificari --------------------------------------------------------------
def check_balance(client: KrakenClient, st: dict, rx: str, desktop: bool) -> None:
    """Activ NOU in cont = posibila alocare. Prima rulare = doar snapshot."""
    try:
        bal = client.balance()
    except KrakenError as e:
        log(f"  ! balanta indisponibila ({e}) — continui doar cu watch-ul public")
        return
    assets = {a: float(q) for a, q in bal.items() if float(q) > 0}
    if not st["known_assets"]:
        st["known_assets"] = assets
        log(f"  snapshot initial balanta: {len(assets)} active ({', '.join(sorted(assets))})")
        return
    for a, q in assets.items():
        if a in st["known_assets"]:
            continue
        if re.search(rx, a, re.I):
            st["allocated"] = {"asset": a, "qty": q, "ts": time.time()}
            log(f"  🎯 ALOCARE DETECTATA: {a} = {q}")
            notify(title=f"🎯 ALOCARE xStock: {a} = {q}",
                   body=f"A aparut {a} in contul Kraken (cantitate {q}). "
                        f"Seteaza XSTOCK_ALLOC_PRICE in config.env pt alerte de nivel "
                        f"si STRAT_ADOPT_COST cand perechea devine tranzactionabila.",
                   source="xstock-watch", desktop=desktop)
        else:
            log(f"  ℹ activ nou in cont: {a} = {q}")
            notify(title=f"ℹ Activ nou in cont Kraken: {a} = {q}",
                   body="Verifica daca e alocarea xStock sub alt cod decat cel asteptat.",
                   source="xstock-watch", desktop=desktop)
    st["known_assets"] = assets


def check_pairs(client: KrakenClient, st: dict, rx: str, desktop: bool) -> None:
    """Perechea devine vizibila pe API-ul public = tranzactionabila programatic."""
    try:
        pairs = client.asset_pairs()
    except KrakenError as e:
        log(f"  ! asset_pairs: {e}")
        return
    for k, v in pairs.items():
        hay = f"{k} {v.get('wsname') or ''} {v.get('base') or ''}"
        if re.search(rx, hay, re.I):
            name = v.get("wsname") or k
            st["pair"] = k
            if not st["alerted_pair"]:
                st["alerted_pair"] = True
                px = None
                try:
                    px = client.last_price(k)
                except KrakenError:
                    pass
                log(f"  🚀 PERECHE LISTATA pe API: {name} (pret {px})")
                notify(title=f"🚀 {name} LISTAT pe Kraken API" + (f" @ {px}" if px else ""),
                       body=f"Poti porni botul cu adoptarea alocarii:\n"
                            f"STRAT_ADOPT_COST=<pret_alocare> python3 kraken_bot.py --pair {k}",
                       source="xstock-watch", price=px, desktop=desktop)
            return


def check_levels(client: KrakenClient, st: dict, alloc_price: float,
                 tp_pct: float, sl_pct: float, yahoo_sym: str, desktop: bool) -> None:
    """Alerta o singura data la +tp% / -sl% fata de pretul alocarii."""
    if not st["allocated"] or alloc_price <= 0:
        return
    price = None
    if st["pair"]:
        try:
            price = client.last_price(st["pair"])
        except KrakenError:
            pass
    if price is None and yahoo_sym:
        price = yahoo_last(yahoo_sym)
    if not price:
        return
    chg = (price - alloc_price) / alloc_price * 100
    log(f"  pret {price} vs alocare {alloc_price} ({chg:+.1f}%)")
    qty = st["allocated"].get("qty", 0)
    if not st["alerted_tp"] and chg >= tp_pct:
        st["alerted_tp"] = True
        notify(title=f"📈 xStock {chg:+.1f}% peste alocare ({price})",
               body=f"Valoare estimata: {qty * price:.0f} (alocat la {alloc_price}). "
                    f"Ia in calcul vanzarea partiala / pornirea botului cu adoptare.",
               source="xstock-watch", price=price, desktop=desktop)
    if not st["alerted_sl"] and chg <= -sl_pct:
        st["alerted_sl"] = True
        notify(title=f"📉 xStock {chg:+.1f}% sub alocare ({price})",
               body=f"Valoare estimata: {qty * price:.0f} (alocat la {alloc_price}). "
                    f"Decide: tii (DCA) sau tai pierderea.",
               source="xstock-watch", price=price, desktop=desktop)


def main() -> int:
    load_dotenv(os.path.join(_HERE, ".env"))
    load_dotenv(os.path.join(_HERE, "config.env"))

    ap = argparse.ArgumentParser(description="Watcher alocare xStocks (Kraken).")
    ap.add_argument("--once", action="store_true", help="o singura verificare si iese")
    ap.add_argument("--status", action="store_true", help="arata starea si iese")
    ap.add_argument("--desktop", action="store_true")
    ap.add_argument("--interval", type=float,
                    default=float_env("XSTOCK_CHECK_MINUTES") or 10.0, help="minute")
    args = ap.parse_args()

    rx = os.environ.get("XSTOCK_REGEX", "SPCX|SPACEX")
    alloc_price = float_env("XSTOCK_ALLOC_PRICE") or 0.0
    tp_pct = float_env("XSTOCK_TP_ALERT_PCT") or 20.0
    sl_pct = float_env("XSTOCK_SL_ALERT_PCT") or 15.0
    yahoo_sym = os.environ.get("XSTOCK_YAHOO", "SPCX")

    client = KrakenClient(os.environ.get("KRAKEN_API_KEY"), os.environ.get("KRAKEN_API_SECRET"))
    st = _load_state()

    if args.status:
        print(f"regex={rx}  alloc_price={alloc_price}  tp={tp_pct}%  sl={sl_pct}%  yahoo={yahoo_sym}")
        print(f"active cunoscute: {len(st['known_assets'])} -> {', '.join(sorted(st['known_assets'])) or '-'}")
        print(f"alocare: {st['allocated'] or 'nedetectata'}")
        print(f"pereche API: {st['pair'] or 'nelistata'}")
        return 0

    log("=== xStock watcher pornit ===")
    log(f"    regex      : {rx}")
    log(f"    alocare    : {alloc_price if alloc_price > 0 else 'pret necunoscut (doar detectie)'}")
    log(f"    alerte     : +{tp_pct}% / -{sl_pct}%  (pret: Kraken sau Yahoo {yahoo_sym})")
    log(f"    interval   : {args.interval} min")
    while True:
        check_balance(client, st, rx, args.desktop)
        check_pairs(client, st, rx, args.desktop)
        check_levels(client, st, alloc_price, tp_pct, sl_pct, yahoo_sym, args.desktop)
        _save_state(st)
        if args.once:
            return 0
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    sys.exit(main())
