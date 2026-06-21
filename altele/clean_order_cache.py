#!/usr/bin/env python3
"""
Curata cache_order.json de ordinele care NU sunt tranzactii realizate.

De ce: calea WS (_upsert_order_from_execution_report) stoca ORICE executionReport,
inclusiv NEW/CANCELED/EXPIRED/REJECTED, iar anulatele veneau cu price=0 (bug "L or p":
L = "0.00000000" e string truthy). Astea poluau gardul de profit (min(pret) -> 0).
Codul a fost reparat (Order cache = doar FILLED/PARTIALLY_FILLED), dar fisierul vechi
mai contine gunoiul. Scriptul il scoate.

Criteriu de SCOATERE:  status in {CANCELED, EXPIRED, REJECTED, NEW}  SAU  price <= 0.
Pastreaza: FILLED/PARTIALLY_FILLED si intrarile din REST (status None) cu price > 0.

RULEAZA DOAR cu cacheManager OPRIT (altfel rescrie fisierul din memoria veche).
  python3 altele/clean_order_cache.py --dry     # doar raporteaza
  python3 altele/clean_order_cache.py           # curata (face .bak intai)
Idempotent.
"""
import os
import sys
import json
import time

# altele/ e la un nivel sub radacina repo -> dirname x2 pt radacina (NU altele/cachedb).
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.environ.get("BINANCE_CACHE_DIR", os.path.join(REPO_ROOT, "cachedb"))
ORDER_FILE = os.path.join(CACHE_DIR, "cache_order.json")

BAD_STATUS = {"CANCELED", "EXPIRED", "REJECTED", "NEW"}


def is_garbage(order):
    status = str(order.get("status") or "").upper()
    try:
        price = float(order.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    return status in BAD_STATUS or price <= 0


def main():
    dry = "--dry" in sys.argv
    if not os.path.exists(ORDER_FILE):
        print(f"Nu exista {ORDER_FILE} — nimic de facut.")
        return

    with open(ORDER_FILE) as f:
        data = json.load(f)
    items = data.get("items", {})

    total = removed = 0
    cleaned = {}
    for sym, lst in items.items():
        keep = [o for o in lst if not is_garbage(o)]
        total += len(lst)
        removed += len(lst) - len(keep)
        cleaned[sym] = keep
        print(f"  {sym}: total={len(lst)} | scoase={len(lst) - len(keep)} | raman={len(keep)}")
    print(f"TOTAL: {total} | de scos={removed} | raman={total - removed}")

    if dry:
        print("[DRY] nu am scris nimic.")
        return
    if removed == 0:
        print("Nimic de curatat.")
        return

    bak = ORDER_FILE + f".bak.{int(time.time())}"
    os.rename(ORDER_FILE, bak)
    print(f"Backup: {bak}")
    data["items"] = cleaned
    tmp = ORDER_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, ORDER_FILE)
    print(f"Scris {ORDER_FILE} curatat ({removed} scoase).")


if __name__ == "__main__":
    main()
