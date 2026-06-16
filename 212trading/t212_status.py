#!/usr/bin/env python3
"""
t212_status.py — raport cont Trading 212: cash (liber/investit/blocat), pozitii,
ordine PENDING. Raspunde la "cat am investit, cat mai am liber, de ce e blocat".

  python3 t212_status.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ipo_common import http_get, load_dotenv  # noqa: E402
from t212_client import T212Client  # noqa: E402


def _retry(fn, tries=4, pause=2):
    for i in range(tries):
        try:
            r = fn()
        except Exception:  # noqa: BLE001
            r = None
        if r is not None:
            return r
        if i < tries - 1:
            time.sleep(pause)
    return None


def main() -> int:
    load_dotenv(".env")
    load_dotenv("config.env")
    key = os.environ.get("T212_API_KEY")
    if not key:
        print("! lipseste T212_API_KEY (.env)"); return 1
    c = T212Client(key, os.environ.get("T212_API_SECRET"), env="live")

    # --- CASH ---
    def get_cash():
        st, body = http_get(f"{c.base}/equity/account/cash", headers=c._headers())
        if st != 200 or not body:
            return None
        try:
            return json.loads(body)
        except ValueError:
            return None

    cash = _retry(get_cash)
    print("============ CONT TRADING 212 ============")
    if cash:
        free = cash.get("free", 0); inv = cash.get("invested", 0)
        blk = cash.get("blocked") or 0; tot = cash.get("total", 0)
        ppl = cash.get("ppl", 0); pie = cash.get("pieCash", 0)
        print(f"  INVESTIT (cost)   : {inv:>10.2f}")
        print(f"  DISPONIBIL (liber): {free:>10.2f}   <- cu astia poti plasa ordine noi")
        print(f"  BLOCAT            : {blk:>10.2f}   <- prins in ordine pending/in curs")
        print(f"  cash in pie-uri   : {pie:>10.2f}")
        print(f"  P&L nerealizat    : {ppl:>+10.2f}")
        print(f"  TOTAL cont        : {tot:>10.2f}")
    else:
        print("  ! nu pot citi cash-ul (rate-limit/auth?)")

    # --- POZITII ---
    pf = _retry(c.get_portfolio) or []
    print(f"\n  --- POZITII ({len(pf)}) ---")
    inv_sum = 0.0
    for p in pf:
        q = float(p.get("quantity", 0)); avg = float(p.get("averagePrice", 0))
        cur = float(p.get("currentPrice", 0)); ppl = p.get("ppl", 0)
        val = q * cur; inv_sum += q * avg
        print(f"    {p.get('ticker',''):<14} qty {q:<8.3f} avg {avg:<9.2f} pret {cur:<9.2f} "
              f"valoare {val:>9.2f}  P&L {float(ppl):>+8.2f}")
    if pf:
        print(f"    (suma cost pozitii: {inv_sum:.2f})")

    # --- ORDINE PENDING (blocheaza cash) ---
    orders = _retry(c.list_active_orders) or []
    print(f"\n  --- ORDINE PENDING ({len(orders)}) ---  <- ASTEA blocheaza cash liber")
    if not orders:
        print("    (niciunul — cash-ul nu e blocat de ordine)")
    for o in orders:
        q = o.get("quantity", 0); lp = o.get("limitPrice", o.get("price"))
        side = "BUY" if (isinstance(q, (int, float)) and q > 0) else "SELL"
        print(f"    {o.get('ticker',''):<14} {side} qty {q} @ {lp}  ({o.get('type','')}, {o.get('status','')}) id={o.get('id','')}")
    print("==========================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
