#!/usr/bin/env python3
"""verify_hype_facade.py — DRY-RUN sanity check of the HYPE facade (Hyperliquid SPOT).

Places ZERO real orders. Through the market_api facade (the `mkt` singleton) it checks:
  - symbol ROUTING: HYPEUSDC -> Hyperliquid; BTCUSDC/TAOUSDC -> Binance;
    bare assets BTC/TAO -> Binance (behaviour-preserving default); HYPE -> Hyperliquid.
  - get_current_price(HYPEUSDC) -> a sane SPOT price (>0).
  - get_price_history(HYPEUSDC) -> a fine-grained ascending series, prices >0.
  - free_balance(HYPE) -> the free SPOT balance (total minus hold).
  - get_orders(HYPEUSDC, BUY/SELL) -> normalised {side,price,qty,timestamp}; SPOT only.
  - get_position_stats (avg_buy/net) reproduced from the facade's orders.
  - the DECISION monitor_price_and_trade would take for HYPE (without placing it).
  - place_order(HYPEUSDC, SELL) is DRY (HL_LIVE_ORDERS off) -> None, no order sent.

Run it locally with the venv that has the HL SDK:
  ../.venv/bin/python verify_hype_facade.py
Exits non-zero if anything is clearly unhealthy (wrong routing, invalid price, real order).
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from providers.market_api import api as mkt          # noqa: E402  (facada singleton)
import symbols as sym                        # noqa: E402

HYPE = sym.hypesymbol                        # "HYPEUSDC"
MAXAGE = 17 * 24 * 3600
failures = []


def check(name, cond, detail=""):
    print(f"  [{'OK ' if cond else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")
    if not cond:
        failures.append(f"{name}: {detail}")


def main():
    print("==== RUTARE pe symbol (Binance ramane default) ====")
    check("HYPEUSDC -> Hyperliquid", mkt.provider_name_for(HYPE) == "Hyperliquid",
          mkt.provider_name_for(HYPE))
    check("BTCUSDC  -> Binance", mkt.provider_name_for(sym.btcsymbol) == "Binance",
          mkt.provider_name_for(sym.btcsymbol))
    check("TAOUSDC  -> Binance", mkt.provider_name_for(sym.taosymbol) == "Binance",
          mkt.provider_name_for(sym.taosymbol))
    check("asset HYPE -> Hyperliquid", mkt.provider_name_for("HYPE") == "Hyperliquid",
          mkt.provider_name_for("HYPE"))
    check("asset BTC  -> Binance (default)", mkt.provider_name_for("BTC") == "Binance",
          mkt.provider_name_for("BTC"))
    check("asset TAO  -> Binance (default)", mkt.provider_name_for("TAO") == "Binance",
          mkt.provider_name_for("TAO"))

    print("\n==== MARKET-DATA HYPE (public, fara cheie) ====")
    price = mkt.get_current_price(HYPE)
    print(f"  get_current_price(HYPEUSDC) = {price}")
    check("valid SPOT price (>0)", isinstance(price, (int, float)) and price and price > 0,
          repr(price))

    hist = mkt.get_price_history(HYPE, 3)
    n = len(hist) if hist else 0
    print(f"  get_price_history(HYPEUSDC, 3h): {n} puncte")
    if hist:
        print(f"    primul={hist[0]}  ultimul={hist[-1]}")
    asc = bool(hist) and all(hist[i]["timestamp"] <= hist[i + 1]["timestamp"]
                             for i in range(len(hist) - 1))
    pos = bool(hist) and all(p["price"] > 0 for p in hist)
    check("history granulara, ascendenta, preturi>0", bool(hist) and asc and pos,
          f"n={n} asc={asc} pos={pos}")
    if price and hist:
        last = hist[-1]["price"]
        near = abs(last - price) / price < 0.10
        check("ultimul close ~ pretul curent (<10%)", near,
              f"close={last} price={price}")

    print("\n==== CONT SPOT HYPE (citire) ====")
    free_hype = mkt.free_balance_for("hyperliquid", "HYPE")
    free_usdc = mkt.free_balance_for("hyperliquid", "USDC")
    print(f"  free_balance(HYPE) = {free_hype}")
    print("  free_balance(USDC) = "
          f"{free_usdc}  (provider explicit: hyperliquid)")
    check("free_balance(HYPE) numeric >=0", isinstance(free_hype, (int, float)) and free_hype >= 0,
          repr(free_hype))

    buys = mkt.get_orders(HYPE, "BUY", MAXAGE)
    sells = mkt.get_orders(HYPE, "SELL", MAXAGE)
    print(f"  get_orders BUY={len(buys)} SELL={len(sells)} (ultimele {MAXAGE//86400} zile, SPOT)")
    shape_ok = all(set(("side", "price", "qty", "timestamp")) <= set(o) for o in (buys + sells))
    sides_ok = all(o["side"] == "BUY" for o in buys) and all(o["side"] == "SELL" for o in sells)
    check("ordine normalizate {side,price,qty,timestamp}", shape_ok)
    check("side filtrat corect (BUY/SELL)", sides_ok)
    for o in (buys[:2] + sells[:2]):
        print(f"    {o['side']} px={o['price']} qty={o['qty']} ts={o['timestamp']}")

    print("\n==== POZITIE (logica get_position_stats) ====")
    tbq = sum(float(o["qty"]) for o in buys)
    tbv = sum(float(o["price"]) * float(o["qty"]) for o in buys)
    tsq = sum(float(o["qty"]) for o in sells)
    avg_buy = tbv / tbq if tbq else 0.0
    net = tbq - tsq
    print(f"  buy_qty={tbq:.6f} sell_qty={tsq:.6f} net_qty={net:.6f} avg_buy={avg_buy:.4f}")
    check("avg_buy plauzibil (0 sau pozitiv si in raza pretului)",
          avg_buy == 0.0 or (price and 0.2 * price <= avg_buy <= 5 * price),
          f"avg_buy={avg_buy} price={price}")

    print("\n==== DECIZIE simulata (ca monitor_price_and_trade, FARA plasare) ====")
    if buys and price:
        last_buy = sorted(buys, key=lambda x: x["timestamp"], reverse=True)[0]["price"]
        inc = (price - last_buy) / last_buy
        print(f"  ultimul buy={last_buy}  pret={price}  variatie={inc*100:+.2f}%")
        gain, lost = 0.092, 0.049
        if inc >= 0.17:
            decizie = "HARD-TP (vinde proportie, indiferent de trend)"
        elif inc > gain:
            decizie = "gain above threshold -> sell ONLY if the trend is NOT up"
        elif (last_buy - price) / last_buy > lost:
            decizie = "loss above threshold -> sell ONLY if the trend is NOT up"
        else:
            decizie = "nimic interesant"
        print(f"  -> decizie: {decizie}")
    else:
        print("  (no recent SPOT buys or no price — no sell decision)")

    print("\n==== POARTA ORDINE: place_order trebuie sa fie DRY ====")
    os.environ.pop("HL_LIVE_ORDERS", None)   # asiguram DRY pt test
    res = mkt.place_order(HYPE, "SELL", price or 1.0, 0.0001)
    check("place_order(HYPEUSDC) DRY -> None (niciun ordin real)", res is None, repr(res))

    print("\n" + "=" * 60)
    if failures:
        print(f"FAIL — {len(failures)} probleme:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS — facada HYPE (Hyperliquid spot) e sanatoasa in DRY-RUN.")
    sys.exit(0)


if __name__ == "__main__":
    main()
