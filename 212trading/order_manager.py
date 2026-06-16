#!/usr/bin/env python3
"""
order_manager.py — logica de plasare a ordinului SPCX:
  * calcul cantitate din buget RON,
  * plasare cu retry (instrumentul poate fi blocat pana se deschide piata),
  * marker pe disc anti-dublura la restart,
  * polling status pana la stare terminala (FILLED/CANCELLED/REJECTED).
"""

from __future__ import annotations

import json
import os
import time

from ipo_common import log, now_str
from ipo_notify import notify
from market_data import get_usd_ron, get_price_usd, t212_to_yahoo
from t212_client import T212Client

# marker pe disc, langa script, ca sa nu plasam ordin de doua ori
ORDER_MARKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".spcx_order_placed")

ORDER_STATUS_POLL_SECONDS = 30
ORDER_STATUS_MAX_WAIT = 900           # 15 min
T212_ORDER_TERMINAL = {"FILLED", "CANCELLED", "REJECTED"}


# ---------------------------------------------------------------------------
# Cantitate
# ---------------------------------------------------------------------------
def resolve_quantity(order_price: float,
                     order_qty: float | None,
                     order_budget_ron: float | None) -> float | None:
    """Cantitatea de actiuni: fie fixa (ORDER_QTY), fie din buget RON la cursul curent."""
    if order_qty:
        return order_qty
    if order_budget_ron:
        rate = get_usd_ron()
        qty = order_budget_ron / (order_price * rate)
        log(f"  [ORDER] {order_budget_ron} RON / ({order_price} USD × {rate:.2f}) = {qty:.4f} actiuni")
        if qty < 1:
            log(f"  ! [ORDER] buget < pretul unei actiuni (~{order_price*rate:.0f} RON) "
                f"-> ordin FRACTIONAR ({qty:.4f}). T212 poate refuza fractional pe instrument nou.")
        return qty
    return None


# ---------------------------------------------------------------------------
# Marker anti-dublura
# ---------------------------------------------------------------------------
def order_already_placed() -> bool:
    return os.path.exists(ORDER_MARKER)


def _write_marker(ticker: str, result: dict) -> None:
    try:
        with open(ORDER_MARKER, "w", encoding="utf-8") as f:
            json.dump({"at": now_str(), "ticker": ticker, "order": result}, f)
        log(f"  [ORDER] marker scris: {ORDER_MARKER}")
    except OSError as e:
        log(f"  ! nu pot scrie markerul: {e}")


# ---------------------------------------------------------------------------
# Polling status ordin
# ---------------------------------------------------------------------------
def poll_order_until_terminal(client: T212Client, order_id, ticker: str,
                              desktop: bool = False) -> None:
    log(f"  [ORDER] Polling status ordin {order_id} (max {ORDER_STATUS_MAX_WAIT}s)...")
    deadline = time.time() + ORDER_STATUS_MAX_WAIT
    last_status = None
    while time.time() < deadline:
        time.sleep(ORDER_STATUS_POLL_SECONDS)
        info = client.get_order_status(order_id)
        if not info:
            log(f"  [ORDER] nu pot citi statusul {order_id}")
            continue
        st = (info.get("status") or "").upper()
        if st == last_status:
            continue
        last_status = st
        log(f"  [ORDER] status {order_id}: {st}")
        if st in T212_ORDER_TERMINAL:
            fq = info.get("filledQuantity") or info.get("quantity", 0)
            fp = info.get("fillPrice") or info.get("limitPrice", 0)
            if st == "FILLED":
                log(f"  [ORDER] ✓ FILLED: {fq} @ {fp} USD")
                notify(title=f"✓ Ordin executat: {ticker}",
                       body=f"Qty {fq} @ {fp} USD\nMoment: {now_str()}",
                       source="T212 order", price=float(fp or 0), desktop=desktop)
            else:
                log(f"  [ORDER] ✗ {st}")
                notify(title=f"✗ Ordin {st}: {ticker}",
                       body=f"id={order_id}\nMoment: {now_str()}",
                       source="T212 order", desktop=desktop)
            return
    log(f"  [ORDER] timeout polling — {order_id} inca neterminat dupa {ORDER_STATUS_MAX_WAIT}s")


# ---------------------------------------------------------------------------
# Plasare ordin (cu retry + validare pret + marker)
# ---------------------------------------------------------------------------
def place_order_with_retry(
    client: T212Client,
    ticker: str,
    quantity: float,
    limit_price: float,
    validity: str,
    dry_run: bool,
    max_limit: float | None = None,
    desktop: bool = False,
    max_retries: int = 10,
    retry_delay: int = 60,
    write_marker: bool = True,
) -> bool:
    """Plaseaza ordinul LIMIT. Returneaza True daca a fost acceptat (sau dry-run)."""

    qty_r   = round(quantity, 2)
    price_r = round(limit_price, 2)

    # info pret curent (doar log; nu modificam limita aleasa de user)
    current = get_price_usd(t212_to_yahoo(ticker))
    if current:
        log(f"  [ORDER] pret curent {t212_to_yahoo(ticker)}: {current:.2f} USD  |  limita: {price_r:.2f} USD")
        if price_r < current:
            log(f"  ! [ORDER] limita {price_r} < pret {current:.2f} -> ordinul va sta in asteptare "
                f"(se executa doar daca pretul scade la {price_r}).")

    if dry_run:
        log(f"  [ORDER] [DRY-RUN] LIMIT BUY {ticker}  qty={qty_r}  @ {price_r} USD  validity={validity}")
        log("  [ORDER] Dry-run — ordin NESENT. Seteaza ORDER_EXECUTE=true in .env.")
        return True

    for attempt in range(max_retries):
        if attempt > 0:
            log(f"  [ORDER] Retry {attempt}/{max_retries} in {retry_delay}s "
                f"(instrumentul poate fi inca netranzactionabil)...")
            time.sleep(retry_delay)

        log(f"  [ORDER] LIMIT BUY {ticker}  qty={qty_r}  @ {price_r} USD  validity={validity}")
        status, data = client.place_limit_order(ticker, qty_r, price_r, validity)

        if status in (200, 201):
            oid = data.get("id")
            log(f"  [ORDER] ✓ plasat: id={oid}  status={data.get('status')}")
            if write_marker:
                _write_marker(ticker, data)
            notify(title="Ordin SPCX plasat pe T212!",
                   body=(f"LIMIT {ticker}  qty={qty_r} @ {price_r} USD\n"
                         f"id={oid}  status={data.get('status')}"),
                   source="T212 order", price=price_r, desktop=desktop)
            if oid:
                poll_order_until_terminal(client, oid, ticker, desktop=desktop)
            return True
        else:
            log(f"  ! [ORDER] T212 HTTP {status}: {json.dumps(data)[:400]}")

    notify(title="✗ Ordin SPCX ESUAT pe T212!",
           body=f"{max_retries} incercari esuate pentru {ticker}. Plaseaza manual.",
           source="T212 order", desktop=desktop)
    return False
