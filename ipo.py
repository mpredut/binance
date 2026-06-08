#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
spacex_ipo_watch.py  —  monitorizeaza SPCX si plaseaza ordin automat pe T212.

Config complet in .env:
    T212_API_KEY / T212_API_SECRET
    NTFY_TOPIC / ALERT_TO_EMAIL / SMTP_*
    ORDER_PRICE=150        pret LIMIT maxim per actiune (USD)
    ORDER_BUDGET_RON=500   buget RON; cantitatea se calculeaza automat
    ORDER_QTY=2            alternativa: numar fix de actiuni
    ORDER_VALIDITY=DAY     DAY sau GTC
    ORDER_EXECUTE=true     false = dry-run

Comenzi utile:
    python3 ipo.py                          # porneste watcherul
    python3 ipo.py --test-notify all        # testeaza notificarile
    python3 ipo.py --test-order NVDA_US_EQ  # testeaza un ordin
    python3 ipo.py --find-ticker nvidia     # gaseste ticker-ul exact in T212
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

from alertnotifiers import AlertNotifier

# ---------------------------------------------------------------------------
# Config implicit
# ---------------------------------------------------------------------------
TICKER = "SPCX"
NAME_PATTERNS = ("spacex", "space exploration")
POLL_SECONDS = 90
HTTP_TIMEOUT = 25
ORDER_STATUS_POLL_SECONDS = 30   # cat de des verificam statusul ordinului dupa plasare
ORDER_STATUS_MAX_WAIT = 900      # max 15 min de polling pt status ordin

T212_BASE = "https://live.trading212.com/api/v0"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
T212_ORDER_TERMINAL = {"FILLED", "CANCELLED", "REJECTED"}

# marker pe disc ca sa nu plasam ordin de doua ori daca scriptul e repornit
ORDER_MARKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".spcx_order_placed")

ET = timezone(timedelta(hours=-4))
BUCHAREST = timezone(timedelta(hours=3))


# ---------------------------------------------------------------------------
# Utilitare
# ---------------------------------------------------------------------------
def now_str() -> str:
    n = datetime.now(timezone.utc)
    return (
        f"{n.astimezone(ET):%Y-%m-%d %H:%M:%S} ET  |  "
        f"{n.astimezone(BUCHAREST):%H:%M:%S} Bucuresti"
    )


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).astimezone(BUCHAREST):%H:%M:%S}] {msg}", flush=True)


def load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if not (val.startswith('"') or val.startswith("'")):
                    val = val.split("#")[0].strip()
                val = val.strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        log(f"  .env incarcat din {path}")
    except OSError as e:
        log(f"  ! nu pot citi {path}: {e}")


def _float_env(key: str) -> float | None:
    raw = os.environ.get(key, "").split("#")[0].strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def http_get(url: str, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        log(f"  ! eroare retea: {e}")
        return 0, b""


def http_post_json(url: str, payload: dict, headers: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        log(f"  ! eroare retea POST: {e}")
        return 0, b""


# ---------------------------------------------------------------------------
# Notificari (via AlertNotifier)
# ---------------------------------------------------------------------------
def notify(title: str, body: str, source: str,
           price: float | None = None, desktop: bool = False) -> None:
    for _ in range(5):
        sys.stdout.write("\a")
        sys.stdout.flush()
        time.sleep(0.2)

    alert = {
        "type": "new_coin_discovered",
        "symbol": "SPCX",
        "name": title,
        "source": source,
        "price": price,
        "added_at": datetime.now(),
        "url": None,
    }

    ntfy_topic = os.environ.get("NTFY_TOPIC")
    ntfy_url = f"https://ntfy.sh/{ntfy_topic}" if ntfy_topic else None
    AlertNotifier.send_phone_webhook_batch([alert], webhook_url=ntfy_url)

    if os.environ.get("ALERT_TO_EMAIL"):
        AlertNotifier.send_email_batch([alert])

    if desktop:
        try:
            subprocess.run(["notify-send", "-u", "critical", title, body], check=False)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------------------
# T212 helpers
# ---------------------------------------------------------------------------
def t212_auth(api_key: str, api_secret: str | None) -> str:
    if api_secret:
        token = base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        return f"Basic {token}"
    return api_key


def t212_headers(api_key: str, api_secret: str | None) -> dict:
    return {
        "Authorization": t212_auth(api_key, api_secret),
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
        "Accept": "application/json",
    }


def t212_to_yahoo(t212_ticker: str) -> str:
    """NVDA_US_EQ -> NVDA  (pentru price check pe Yahoo)."""
    return t212_ticker.split("_")[0]


# ---------------------------------------------------------------------------
# Pret curent si curs valutar (Yahoo Finance)
# ---------------------------------------------------------------------------
def get_price_usd(sym: str) -> float | None:
    headers = {"User-Agent": "Mozilla/5.0 (ipo-watch)"}
    status, body = http_get(YAHOO_CHART.format(sym=sym), headers=headers)
    if status != 200 or not body:
        return None
    try:
        data = json.loads(body)
        meta = ((data.get("chart", {}).get("result") or [{}])[0]).get("meta", {})
        return meta.get("regularMarketPrice") or None
    except (ValueError, KeyError, TypeError):
        return None


def get_usd_ron() -> float:
    rate = get_price_usd("USDRON=X")
    if rate and rate > 1:
        return rate
    log("  ! curs USD/RON indisponibil, folosesc fallback 4.65")
    return 4.65


# ---------------------------------------------------------------------------
# Plasare ordin LIMIT pe T212
# ---------------------------------------------------------------------------
def place_t212_limit_order(
    api_key: str,
    api_secret: str | None,
    ticker: str,
    quantity: float,
    limit_price: float,
    validity: str = "DAY",
    dry_run: bool = True,
) -> dict | None:
    """Plaseaza ordin LIMIT. Returneaza dict cu raspunsul T212 sau None la eroare."""

    qty_r   = round(quantity, 2)
    price_r = round(limit_price, 2)

    # info pret curent (doar pentru log, nu modificam limita utilizatorului)
    yahoo_sym = t212_to_yahoo(ticker)
    current = get_price_usd(yahoo_sym)
    if current:
        log(f"  [ORDER] pret curent {yahoo_sym}: {current:.2f} USD  |  "
            f"limita setata: {price_r:.2f} USD")

    tag = "[DRY-RUN] " if dry_run else ""
    log(f"  [ORDER] {tag}LIMIT BUY {ticker}  qty={qty_r}  @ {price_r} USD  validity={validity}")

    if dry_run:
        log("  [ORDER] Dry-run — ordin NESENT. Seteaza ORDER_EXECUTE=true in .env.")
        return {"dry_run": True, "ticker": ticker, "quantity": qty_r, "limitPrice": price_r}

    # T212 limit-order schema: ticker / quantity (+ = BUY, - = SELL) / limitPrice / timeValidity.
    # NU exista camp "side" si NU e "instrumentTicker" — astea dadeau 400 Invalid payload.
    payload = {
        "ticker":       ticker,
        "quantity":     qty_r,          # pozitiv = cumparare
        "limitPrice":   price_r,
        "timeValidity": validity,       # "DAY" | "GOOD_TILL_CANCEL"
    }
    log(f"  [ORDER] payload: {json.dumps(payload)}")

    status, body = http_post_json(
        f"{T212_BASE}/equity/orders/limit",
        payload=payload,
        headers=t212_headers(api_key, api_secret),
    )

    if status in (200, 201):
        try:
            result = json.loads(body)
        except ValueError:
            result = {}
        log(f"  [ORDER] ✓ Ordin plasat: id={result.get('id')}  status={result.get('status')}")
        return result
    else:
        err = body.decode(errors="replace")[:500]
        log(f"  ! [ORDER] T212 HTTP {status}: {err}")
        return None


# ---------------------------------------------------------------------------
# Verificare status ordin T212
# ---------------------------------------------------------------------------
def check_t212_order_status(api_key: str, api_secret: str | None, order_id) -> dict | None:
    status, body = http_get(
        f"{T212_BASE}/equity/orders/{order_id}",
        headers=t212_headers(api_key, api_secret),
    )
    if status != 200:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def poll_order_until_terminal(
    api_key: str,
    api_secret: str | None,
    order_id,
    ticker: str,
    desktop: bool = False,
) -> None:
    """Verifica periodic statusul ordinului si alerteaza la terminal (FILLED/CANCELLED/REJECTED)."""
    log(f"  [ORDER] Incep polling status pentru ordinul {order_id} (max {ORDER_STATUS_MAX_WAIT}s)...")
    deadline = time.time() + ORDER_STATUS_MAX_WAIT
    last_status = None

    while time.time() < deadline:
        time.sleep(ORDER_STATUS_POLL_SECONDS)
        info = check_t212_order_status(api_key, api_secret, order_id)
        if not info:
            log(f"  [ORDER] Nu pot citi statusul ordinului {order_id}")
            continue

        st = (info.get("status") or "").upper()
        if st == last_status:
            continue
        last_status = st
        log(f"  [ORDER] Status ordin {order_id}: {st}")

        if st in T212_ORDER_TERMINAL:
            filled_qty   = info.get("filledQuantity") or info.get("quantity", 0)
            filled_price = info.get("fillPrice") or info.get("limitPrice", 0)
            if st == "FILLED":
                msg = (f"Ordin EXECUTAT: {ticker}\n"
                       f"Qty: {filled_qty}  Pret: {filled_price} USD\n"
                       f"Moment: {now_str()}")
                log(f"  [ORDER] ✓ FILLED: {filled_qty} actiuni @ {filled_price} USD")
                notify(title=f"✓ Ordin executat: {ticker}", body=msg,
                       source="T212 order", price=float(filled_price or 0), desktop=desktop)
            else:
                msg = (f"Ordin {st}: {ticker}\n"
                       f"id={order_id}\nMoment: {now_str()}")
                log(f"  [ORDER] ✗ {st}: ordinul NU a fost executat")
                notify(title=f"✗ Ordin {st}: {ticker}", body=msg,
                       source="T212 order", desktop=desktop)
            return

    log(f"  [ORDER] Timeout polling — ordinul {order_id} nu a ajuns la stare terminala in {ORDER_STATUS_MAX_WAIT}s")


def place_order_with_retry(
    api_key: str,
    api_secret: str | None,
    ticker: str,
    qty: float,
    limit_price: float,
    validity: str,
    dry_run: bool,
    desktop: bool = False,
    notify_kw: dict | None = None,
    max_retries: int = 10,
    write_marker: bool = True,
) -> bool:
    """Plaseaza ordinul cu retry pe esec, scrie markerul anti-dublura si polleaza statusul.

    Returneaza True daca ordinul a fost plasat (sau dry-run), False daca toate retry-urile au esuat.
    """
    notify_kw = notify_kw or {}
    for attempt in range(max_retries):
        if attempt > 0:
            log(f"  [ORDER] Retry {attempt}/{max_retries} in 60s "
                f"(instrumentul poate fi blocat temporar)...")
            time.sleep(60)
        result = place_t212_limit_order(
            api_key, api_secret, ticker=ticker, quantity=qty,
            limit_price=limit_price, validity=validity, dry_run=dry_run,
        )
        if result is None:
            continue  # esec -> retry
        if result.get("dry_run"):
            return True
        # ordin real plasat cu succes
        if write_marker:
            try:
                with open(ORDER_MARKER, "w", encoding="utf-8") as f:
                    json.dump({"at": now_str(), "ticker": ticker, "order": result}, f)
                log(f"  [ORDER] marker scris: {ORDER_MARKER}")
            except OSError as e:
                log(f"  ! nu pot scrie markerul: {e}")
        notify(title="Ordin SPCX plasat pe T212!",
               body=(f"LIMIT {ticker}  qty={result.get('quantity')} "
                     f"@ {result.get('limitPrice')} USD\n"
                     f"id={result.get('id')}  status={result.get('status')}"),
               source="T212 order", price=limit_price, **notify_kw)
        order_id = result.get("id")
        if order_id:
            poll_order_until_terminal(api_key, api_secret, order_id, ticker, desktop=desktop)
        return True

    # toate retry-urile au esuat
    notify(title="✗ Ordin SPCX ESUAT pe T212!",
           body=f"{max_retries} incercari, toate esuate. Plaseaza manual {ticker}.",
           source="T212 order", **notify_kw)
    return False


# ---------------------------------------------------------------------------
# Calcul cantitate din buget RON
# ---------------------------------------------------------------------------
def resolve_quantity(
    order_price: float,
    order_qty: float | None,
    order_budget_ron: float | None,
) -> float | None:
    if order_qty:
        return order_qty
    if order_budget_ron:
        rate = get_usd_ron()
        qty = order_budget_ron / (order_price * rate)
        ron_per_share = order_price * rate
        log(f"  [ORDER] {order_budget_ron} RON / ({order_price} USD × {rate:.2f}) = {qty:.4f} actiuni")
        if qty < 1:
            log(f"  ! [ORDER] buget {order_budget_ron} RON < pretul unei actiuni "
                f"(~{ron_per_share:.0f} RON) -> ordin FRACTIONAR ({qty:.4f}). "
                f"T212 poate refuza fractional pe un instrument proaspat listat.")
        return qty
    return None


# ---------------------------------------------------------------------------
# Detector 1: piata reala pe NASDAQ (evita false pozitive cu volum 0)
# ---------------------------------------------------------------------------
def check_market(sym: str) -> dict | None:
    headers = {"User-Agent": "Mozilla/5.0 (ipo-watch)"}
    status, body = http_get(YAHOO_CHART.format(sym=sym), headers=headers)
    if status != 200 or not body:
        return None
    try:
        data = json.loads(body)
        result = (data.get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        meta = result.get("meta", {})
    except (ValueError, KeyError, TypeError):
        return None

    price   = meta.get("regularMarketPrice")
    volume  = meta.get("regularMarketVolume") or 0
    state   = (meta.get("marketState") or "").upper()
    last_ts = meta.get("regularMarketTime")

    age_sec = None
    if last_ts:
        try:
            age_sec = time.time() - float(last_ts)
        except (TypeError, ValueError):
            pass

    fresh = age_sec is not None and age_sec < 15 * 60
    live_state = state in ("REGULAR", "PRE", "PREPRE", "POST", "POSTPOST")
    really_trading = bool(price) and volume > 0 and fresh and live_state

    return {
        "price":    price,
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName") or meta.get("fullExchangeName"),
        "volume":   volume,
        "state":    state or "?",
        "age_min":  round(age_sec / 60, 1) if age_sec is not None else None,
        "name":     meta.get("longName") or meta.get("shortName") or "",
        "trading":  really_trading,
    }


# ---------------------------------------------------------------------------
# Detector 2: Trading 212 (instrumentul a aparut)
# ---------------------------------------------------------------------------
def check_t212(api_key: str, api_secret: str | None) -> list[dict] | None:
    status, body = http_get(
        f"{T212_BASE}/equity/metadata/instruments",
        headers=t212_headers(api_key, api_secret),
    )
    if status == 429:
        log("  ! T212 rate limit (429)")
        return None
    if status in (401, 403):
        log(f"  ! T212 auth esuat ({status})")
        return None
    if status != 200 or not body:
        return None
    try:
        instruments = json.loads(body)
    except ValueError:
        return None

    hits = []
    for ins in instruments:
        ticker = str(ins.get("ticker", ""))
        name   = str(ins.get("name", "")).lower()
        short  = str(ins.get("shortName", "")).lower()
        if (
            TICKER.upper() in ticker.upper()
            or any(p in name  for p in NAME_PATTERNS)
            or any(p in short for p in NAME_PATTERNS)
        ):
            hits.append(ins)
    return hits or None


def pick_spcx(hits: list[dict]) -> dict | None:
    """Alege determinist instrumentul SPCX corect din lista de match-uri.

    Scor: ticker incepe cu SPCX (+3), e in USD (+1), nume contine SpaceX (+2).
    Returneaza None daca cel mai bun scor e prea slab SAU daca primele doua
    sunt la egalitate (ambiguu) — preferam sa NU plasam ordin pe ghicite.
    """
    def score(h: dict) -> int:
        t  = str(h.get("ticker", "")).upper()
        nm = (str(h.get("name", "")) + str(h.get("shortName", ""))).lower()
        return ((3 if t.startswith("SPCX") else 0)
                + (1 if h.get("currencyCode") == "USD" else 0)
                + (2 if any(p in nm for p in NAME_PATTERNS) else 0))

    ranked = sorted(hits, key=score, reverse=True)
    if not ranked or score(ranked[0]) < 3:
        return None
    if len(ranked) > 1 and score(ranked[0]) == score(ranked[1]):
        return None  # ambiguu -> doar alerta, fara ordin automat
    return ranked[0]


# ---------------------------------------------------------------------------
# Fereastra orelor de piata US
# ---------------------------------------------------------------------------
def in_market_window() -> bool:
    n = datetime.now(ET)
    if n.weekday() >= 5:
        return False
    minutes = n.hour * 60 + n.minute
    return 9 * 60 <= minutes <= 16 * 60 + 30


# ---------------------------------------------------------------------------
# Bucla principala
# ---------------------------------------------------------------------------
def main() -> int:
    env_file = os.environ.get("ENV_FILE", ".env")
    for i, a in enumerate(sys.argv):
        if a == "--env-file" and i + 1 < len(sys.argv):
            env_file = sys.argv[i + 1]
    load_dotenv(env_file)

    # mediu T212: demo (testare sigura) sau live (bani reali). Default live.
    global T212_BASE
    t212_env = os.environ.get("T212_ENV", "live").strip().lower()
    T212_BASE = ("https://demo.trading212.com/api/v0" if t212_env == "demo"
                 else "https://live.trading212.com/api/v0")

    ap = argparse.ArgumentParser(description="Monitorizeaza listarea SpaceX (SPCX).")
    ap.add_argument("--env-file",          default=env_file)
    ap.add_argument("--ticker",            default=TICKER)
    ap.add_argument("--interval",          type=int, default=POLL_SECONDS)
    ap.add_argument("--desktop",           action="store_true")
    ap.add_argument("--market-hours-only", action="store_true")
    ap.add_argument("--execute",           action="store_true",
                    help="Override: plaseaza ordin real (suprascrie ORDER_EXECUTE din .env)")
    ap.add_argument("--test-notify",       choices=["market", "t212", "all"], metavar="WHAT")
    ap.add_argument("--test-order",        metavar="T212_TICKER",
                    help="Testeaza ordin pe ticker dat si iese (ex: NVDA_US_EQ)")
    ap.add_argument("--find-ticker",       metavar="NUME",
                    help="Cauta instrument in T212 dupa nume/simbol")
    args = ap.parse_args()

    t212_key         = os.environ.get("T212_API_KEY")
    t212_secret      = os.environ.get("T212_API_SECRET")
    order_price      = _float_env("ORDER_PRICE")
    order_qty        = _float_env("ORDER_QTY")
    order_budget_ron = _float_env("ORDER_BUDGET_RON")
    # normalizeaza validity la enum-ul acceptat de T212
    _val_raw = os.environ.get("ORDER_VALIDITY", "DAY").strip().upper()
    order_validity   = ("GOOD_TILL_CANCEL" if _val_raw in ("GTC", "GOOD_TILL_CANCEL")
                        else "DAY")
    dry_run          = not (args.execute or
                            os.environ.get("ORDER_EXECUTE", "false").lower() == "true")
    interval         = max(args.interval, 60)
    notify_kw        = dict(desktop=args.desktop)

    log("=== SpaceX IPO watcher pornit ===")
    log(f"    ticker       : {args.ticker}")
    log(f"    interval     : {interval}s")
    log(f"    mediu T212   : {t212_env.upper()}{'  ⚠ BANI REALI' if t212_env != 'demo' and not dry_run else ''}")
    log(f"    ntfy         : {os.environ.get('NTFY_TOPIC') or '(dezactivat)'}")
    log(f"    email        : {os.environ.get('ALERT_TO_EMAIL') or '(dezactivat)'}")
    log(f"    T212 check   : {'da' if t212_key else 'nu (T212_API_KEY lipsa)'}")
    if order_price:
        log(f"    order-price  : {order_price} USD  "
            f"({'REAL' if not dry_run else 'DRY-RUN'})  validity={order_validity}")

    # -------------------------------------------------------------------------
    # --find-ticker
    # -------------------------------------------------------------------------
    if args.find_ticker:
        if not t212_key:
            log("! T212_API_KEY lipsa in .env")
            return 1
        log(f"[FIND] Caut '{args.find_ticker}'...")
        sc, body = http_get(f"{T212_BASE}/equity/metadata/instruments",
                            headers=t212_headers(t212_key, t212_secret))
        if sc != 200:
            log(f"! T212 HTTP {sc}")
            return 1
        try:
            instruments = json.loads(body)
        except ValueError:
            log("! raspuns invalid")
            return 1
        q = args.find_ticker.lower()
        hits = [i for i in instruments
                if q in str(i.get("ticker","")).lower()
                or q in str(i.get("name","")).lower()
                or q in str(i.get("shortName","")).lower()]
        for h in hits:
            log(f"  ticker={h.get('ticker'):<20} name={h.get('name')}  "
                f"currency={h.get('currencyCode')}  isin={h.get('isin')}")
        if not hits:
            log(f"  Niciun rezultat pentru '{args.find_ticker}'")
        return 0

    # -------------------------------------------------------------------------
    # --test-notify
    # -------------------------------------------------------------------------
    if args.test_notify:
        ts = now_str()
        if args.test_notify in ("market", "all"):
            log("[TEST] notificare market...")
            notify(title="[TEST] SpaceX (SPCX) a inceput tranzactionarea!",
                   body=f"SPCX SE TRANZACTIONEAZA pe NASDAQ.\nLast price: 99.99 USD\n{ts}",
                   source="NASDAQ", price=99.99, **notify_kw)
        if args.test_notify in ("t212", "all"):
            log("[TEST] notificare T212...")
            notify(title="[TEST] SpaceX e cumparabil pe Trading 212!",
                   body=f"SPCX DISPONIBIL pe T212.\n{ts}",
                   source="Trading 212", **notify_kw)
        log("[TEST] Gata.")
        return 0

    # -------------------------------------------------------------------------
    # --test-order
    # -------------------------------------------------------------------------
    if args.test_order:
        if not t212_key:
            log("! T212_API_KEY lipsa in .env"); return 1
        if not order_price:
            log("! ORDER_PRICE lipsa in .env"); return 1
        if not order_qty and not order_budget_ron:
            log("! ORDER_QTY sau ORDER_BUDGET_RON lipsa in .env"); return 1

        qty = resolve_quantity(order_price, order_qty, order_budget_ron)
        if not qty or qty <= 0:
            log("! cantitate invalida"); return 1

        # test: NU scriem markerul (e doar o proba, nu ordinul real de SPCX)
        ok = place_order_with_retry(
            t212_key, t212_secret, args.test_order, qty,
            order_price, order_validity, dry_run,
            desktop=args.desktop, notify_kw=notify_kw,
            max_retries=1, write_marker=False,
        )
        return 0 if ok else 1

    # -------------------------------------------------------------------------
    # Bucla principala
    # -------------------------------------------------------------------------
    log("    Astept... (Ctrl+C ca sa opresc)")
    market_fired = False
    t212_fired   = False

    try:
        while not (market_fired and (t212_fired or not t212_key)):
            if args.market_hours_only and not in_market_window():
                time.sleep(min(interval * 5, 600))
                continue

            # --- Detector piata ---
            last_diag = ""
            if not market_fired:
                m = check_market(args.ticker)
                if m and m.get("trading"):
                    market_fired = True
                    ts = now_str()
                    body = (f"SPCX SE TRANZACTIONEAZA pe {m.get('exchange')}.\n"
                            f"Last price: {m['price']} {m.get('currency') or ''}  "
                            f"(vol {m.get('volume')}, state {m.get('state')})\n"
                            f"Moment: {ts}")
                    log("############################################")
                    log(">>> [MARKET] TRANZACTIONARE REALA DETECTATA <<<")
                    log(body.replace("\n", " | "))
                    log("############################################")
                    notify(title="SpaceX (SPCX) a inceput tranzactionarea!",
                           body=body, source=m.get("exchange") or "NASDAQ",
                           price=m.get("price"), **notify_kw)
                elif m:
                    last_diag = (f"market: inca nu (pret={m.get('price')} "
                                 f"vol={m.get('volume')} state={m.get('state')} "
                                 f"age={m.get('age_min')}min)")
                else:
                    last_diag = "market: simbol indisponibil pe feed"

            # --- Detector T212 + ordin automat ---
            if t212_key and not t212_fired:
                hits = check_t212(t212_key, t212_secret)
                if hits:
                    t212_fired = True
                    ts         = now_str()
                    lines = [f"- {h.get('ticker')} ({h.get('currencyCode')}) "
                             f"{h.get('name')} isin={h.get('isin')}"
                             for h in hits]
                    # log campuri complete pt debug (tradable, type, etc.)
                    for h in hits:
                        extras = {k: v for k, v in h.items()
                                  if k not in ("ticker", "name", "shortName", "isin", "currencyCode")}
                        if extras:
                            log(f"  [T212 meta] {h.get('ticker')}: {extras}")
                    body = ("SPCX e DISPONIBIL pe Trading 212:\n"
                            + "\n".join(lines) + f"\nMoment: {ts}")
                    log("############################################")
                    log(">>> [T212] SPACEX A APARUT IN TRADING 212 <<<")
                    log(body.replace("\n", " | "))
                    log("############################################")
                    notify(title="SpaceX e cumparabil pe Trading 212!",
                           body=body, source="Trading 212", **notify_kw)

                    # alegere determinista a instrumentului (refuza daca e ambiguu)
                    chosen = pick_spcx(hits)
                    if order_price and not chosen:
                        log("  ! [ORDER] instrument SPCX ambiguu/neclar — NU plasez ordin automat. "
                            "Verifica manual din lista de mai sus.")
                        notify(title="⚠ SPCX pe T212 — verifica manual!",
                               body="Match ambiguu, ordinul automat a fost sarit din siguranta.\n" + body,
                               source="T212 order", **notify_kw)
                    elif order_price and chosen:
                        t212_ticker = chosen.get("ticker", "SPCX")
                        if os.path.exists(ORDER_MARKER):
                            log(f"  [ORDER] marker {ORDER_MARKER} existent — ordin deja plasat, nu repet.")
                        else:
                            qty = resolve_quantity(order_price, order_qty, order_budget_ron)
                            if qty and qty > 0:
                                place_order_with_retry(
                                    t212_key, t212_secret, t212_ticker, qty,
                                    order_price, order_validity, dry_run,
                                    desktop=args.desktop, notify_kw=notify_kw,
                                )
                            else:
                                log("  ! ORDER_PRICE setat dar qty/budget lipsesc — ordin NESENT")

            if not (market_fired and (t212_fired or not t212_key)):
                state_parts = ["market:" + ("OK" if market_fired else "wait")]
                if t212_key:
                    state_parts.append("t212:" + ("OK" if t212_fired else "wait"))
                line = "ping - " + ", ".join(state_parts)
                if last_diag:
                    line += "  |  " + last_diag
                log(line)
                time.sleep(interval)

    except KeyboardInterrupt:
        log("Oprit manual.")
        return 130

    log("=== Gata — ambele praguri atinse. ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
