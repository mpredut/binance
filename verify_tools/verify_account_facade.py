#!/usr/bin/env python3
"""verify_account_facade.py — dovada ca facada de CONT (Faza 3) intoarce EXACT aceleasi
date ca accesul direct bapi/bapi_allorders, pt simbolurile live (TAO/BTC).

Compara, in ACELASI proces (deci pe acelasi cache de ordine), pentru fiecare symbol:
  - free_balance(base): facada (mkt.free_balance) vs bucla directa peste
    get_account_assets_balances (exact ca monitortrades.get_available_qty / trade_watch)
  - get_orders(symbol, side): facada NORMALIZATA vs apiorders.get_trade_orders direct
    (numar de ordine + price/qty/timestamp/side pe fiecare ordin)
  - avg_buy / net_qty (logica get_position_stats) calculate din AMBELE surse

Behavior-preserving inseamna: TOATE perechile trebuie sa fie identice. Iese cu cod !=0
daca ceva difera. A se rula INAINTE de orice restart de flota:
  ~/binance/myenv/bin/python verify_account_facade.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from binance_api import bapi as api                  # noqa: E402
from binance_api import bapi_allorders as apiorders  # noqa: E402
from providers.market_api import api as mkt                     # noqa: E402  (facada singleton)
import symbols as sym                                 # noqa: E402

MAXAGE = 17 * 24 * 3600   # ca trade_watch.position (referinta botului)
SYMBOLS = [sym.taosymbol, sym.btcsymbol]
QUOTES = ("USDC", "USDT", "BUSD", "FDUSD", "USD")


def base_of(symbol):
    for q in QUOTES:
        if symbol.endswith(q):
            return symbol[:-len(q)]
    return symbol


def free_direct(base):
    """Identic cu bucla din monitortrades.get_available_qty (inainte de Faza 3)."""
    for b in (api.get_account_assets_balances() or []):
        if b.get("asset") == base:
            return float(b.get("free", 0.0) or 0.0)
    return 0.0


def position_from(buy_orders, sell_orders):
    """Logica get_position_stats: avg_buy + net_qty din liste de ordine."""
    tq = sum(float(o["qty"]) for o in buy_orders)
    tv = sum(float(o["price"]) * float(o["qty"]) for o in buy_orders)
    sq = sum(float(o["qty"]) for o in sell_orders)
    avg = tv / tq if tq else 0.0
    return avg, tq - sq


def order_key(o):
    return (o.get("timestamp"), float(o.get("price", 0) or 0),
            float(o.get("qty", 0) or 0), (o.get("side") or "").upper())


def main():
    failures = []
    for s in SYMBOLS:
        base = base_of(s)
        print(f"\n==== {s} (base={base}) ====")

        # 1) free_balance ------------------------------------------------------
        fd = free_direct(base)
        ff = mkt.free_balance(base)
        same_free = (fd == ff)
        print(f"  free: direct={fd!r}  facada={ff!r}  -> {'OK' if same_free else 'DIFERA'}")
        if not same_free:
            failures.append(f"{s}: free_balance direct={fd!r} != facada={ff!r}")

        # 2) ordine BUY/SELL: numar + camp-cu-camp -----------------------------
        direct = {}
        facade = {}
        for side in ("BUY", "SELL"):
            raw = apiorders.get_trade_orders(side, s, MAXAGE) or []
            nrm = mkt.get_orders(s, side, MAXAGE) or []
            direct[side], facade[side] = raw, nrm
            if len(raw) != len(nrm):
                failures.append(f"{s} {side}: numar ordine direct={len(raw)} != facada={len(nrm)}")
                print(f"  {side}: COUNT DIFERA direct={len(raw)} facada={len(nrm)}")
                continue
            mism = 0
            for r, n in zip(sorted(raw, key=order_key), sorted(nrm, key=order_key)):
                if (float(r.get("price", 0) or 0) != float(n.get("price", 0) or 0)
                        or float(r.get("qty", 0) or 0) != float(n.get("qty", 0) or 0)
                        or r.get("timestamp") != n.get("timestamp")
                        or (r.get("side") or "").upper() != (n.get("side") or "").upper()):
                    mism += 1
            if mism:
                failures.append(f"{s} {side}: {mism} ordine cu campuri diferite")
            print(f"  {side}: n={len(raw)}  camp-cu-camp -> {'OK' if mism == 0 else f'{mism} DIFERA'}")

        # 3) avg_buy / net_qty din ambele surse --------------------------------
        avg_d, net_d = position_from(direct["BUY"], direct["SELL"])
        avg_f, net_f = position_from(facade["BUY"], facade["SELL"])
        same_pos = (avg_d == avg_f and net_d == net_f)
        print(f"  avg_buy: direct={avg_d:.8f} facada={avg_f:.8f} | net: direct={net_d:.8f} "
              f"facada={net_f:.8f} -> {'OK' if same_pos else 'DIFERA'}")
        if not same_pos:
            failures.append(f"{s}: pozitie direct(avg={avg_d},net={net_d}) != facada(avg={avg_f},net={net_f})")

    print("\n" + "=" * 56)
    if failures:
        print(f"FAIL — {len(failures)} diferente:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS — facada de cont == acces direct bapi/apiorders (behavior-preserving).")
    sys.exit(0)


if __name__ == "__main__":
    main()
