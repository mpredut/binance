#!/usr/bin/env python3
"""Raport P&L LIVE pe toata flota — realized (cost mediu din fill-uri reale) + unrealized
(pozitie curenta x pret curent). Surse: Binance get_filled_orders (API direct, nu cache-ul
incomplet), stari Kraken/HL. Fereastra Binance 120z (cost-basis mai vechi -> aproximat).
Rulare: ./myenv/bin/python verify_tools/pnl_report.py"""
import sys, os, time, json
ROOT = "/home/predut/binance"
sys.path.insert(0, ROOT); os.chdir(ROOT)
from binance_api import bapi_allorders as ao
from binance_api import bapi

WINDOW_D = 120


def binance_symbol(symbol):
    st = int((time.time() - WINDOW_D * 24 * 3600) * 1000)
    fills = ao.get_filled_orders(None, symbol, st) or []
    fills.sort(key=lambda t: t.get("timestamp", 0))
    qty = cost = realized = 0.0
    buys = sells = 0
    for t in fills:
        p = float(t["price"]); q = float(t.get("quantity", 0)); side = (t.get("side") or "").upper()
        if side == "BUY":
            qty += q; cost += q * p; buys += 1
        elif side == "SELL":
            sells += 1
            if qty > 1e-12:
                m = min(q, qty); avg = cost / qty
                realized += (p - avg) * m; cost -= avg * m; qty -= m
    try:
        cur = float(bapi.get_current_price(symbol))
    except Exception:
        cur = 0.0
    unreal = qty * cur - cost if qty > 1e-9 else 0.0
    return dict(realized=realized, unreal=unreal, pos=qty, cur=cur, n=len(fills), buys=buys, sells=sells)


print(f"\n=== RAPORT P&L FLOTA ({time.strftime('%Y-%m-%d %H:%M')}) ===")
print(f"{'sursa':<22}{'realized':>11}{'unrealized':>12}{'pozitie':>12}")
tot_r = tot_u = 0.0

for sym in ("BTCUSDC", "TAOUSDC"):
    try:
        d = binance_symbol(sym)
        tot_r += d["realized"]; tot_u += d["unreal"]
        print(f"{'Binance '+sym:<22}{d['realized']:>+11.2f}{d['unreal']:>+12.2f}"
              f"{d['pos']:>10.4f}  ({d['n']} fills 120z, {d['buys']}b/{d['sells']}s @ {d['cur']})")
    except Exception as e:
        print(f"{'Binance '+sym:<22} EROARE: {e}")

# Kraken HYPE (kraken_bot)
try:
    k = json.load(open("kraken/.state_HYPEUSD.json"))
    hy = 54.77  # ultim pret din trail_k
    kr, ku = k["realized_net"], (k["qty"] * hy - k["cost"] if k["qty"] > 1e-9 else 0.0)
    tot_r += kr; tot_u += ku
    print(f"{'Kraken HYPE (bot)':<22}{kr:>+11.2f}{ku:>+12.2f}{k['qty']:>10.4f}  (avg cost {k['cost']/k['qty'] if k['qty'] else 0:.2f}, cur ~{hy})")
except Exception as e:
    print(f"{'Kraken HYPE':<22} EROARE: {e}")

# Hyperliquid dn_bot (delta-neutral: profit = funding - fees, price-independent)
try:
    h = json.load(open("hyperliquid/.state_dn_HYPE.json"))
    hl = h.get("funding_accrued", 0) - h.get("fees_paid", 0)
    tot_r += hl
    print(f"{'HL dn_bot (funding)':<22}{hl:>+11.2f}{0.0:>+12.2f}{h.get('spot_qty',0):>10.4f}  (delta-neutral: funding {h.get('funding_accrued',0):.2f} - fee {h.get('fees_paid',0):.2f})")
except Exception as e:
    print(f"{'HL dn_bot':<22} EROARE: {e}")

print("-" * 57)
print(f"{'TOTAL':<22}{tot_r:>+11.2f}{tot_u:>+12.2f}   -> NET {tot_r+tot_u:+.2f} USD")
print("\nNota: Binance realized pe fereastra de 120z (cost-basis mai vechi de-atat = aproximat).")
print("Fara comisioane Binance in realized (fill-urile API nu le expun aici); ~0.075%/leg cu BNB.")
