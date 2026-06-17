#!/usr/bin/env python3
"""
trade_watch.py — stare rapida a deciziei de trade (TAO/BTC) pt MONITORIZARE LIVE.

Citeste semnale NE-bufferate (sursa de adevar, nu logul bufferat):
  - trend instant din managerul de cache (gradient_recent, up-to-date cu cache_instant_trend.json)
  - sold liber real (api.get_account_assets_balances)
  - avg buy + pozitie neta din trade-urile Binance
Arata si pragurile: HARD-TP (+18%) si vanzarea normala (gain>9.2% SI trend DOWN).

  ~/binance/myenv/bin/python trade_watch.py
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from binance_api import bapi as api  # noqa: E402
from binance_api import bapi_allorders as apiorders  # noqa: E402
import cacheManager as cm  # noqa: E402
import symbols as sym  # noqa: E402

SYMBOLS = [sym.taosymbol, sym.btcsymbol]
HARD_TP_PCT = 17.0   # tine sincron cu monitortrades.conf (hard_tp_pct)
GAIN_PCT = 9.2


def _base(symbol):
    for q in ("USDC", "USDT", "BUSD", "FDUSD", "USD"):
        if symbol.endswith(q):
            return symbol[:-len(q)]
    return symbol


def free_qty(symbol):
    base = _base(symbol)
    for b in (api.get_account_assets_balances() or []):
        if b.get("asset") == base:
            return float(b.get("free", 0) or 0)
    return 0.0


def position(symbol, maxage=17 * 24 * 3600):
    buys = apiorders.get_trade_orders("BUY", symbol, maxage) or []
    sells = apiorders.get_trade_orders("SELL", symbol, maxage) or []
    tq = sum(float(o["qty"]) for o in buys)
    tv = sum(float(o["price"]) * float(o["qty"]) for o in buys)
    sq = sum(float(o["qty"]) for o in sells)
    last_buy = float(buys[0]["price"]) if buys else 0.0   # ca get_relevant_trade (referinta botului)
    return (tv / tq if tq else 0.0), tq - sq, len(sells), last_buy


def main():
    mgr = cm.get_short_trend_manager()
    print(f"==== TRADE WATCH {time.strftime('%Y-%m-%d %H:%M:%S')} ====")
    for s in SYMBOLS:
        snap = mgr.get_snapshot(s) or {}
        px = snap.get("current_price") or api.get_current_price(s) or 0.0
        gr = float(snap.get("gradient_recent", 0.0) or 0.0)
        avg, net, n_sells, last_buy = position(s)
        free = free_qty(s)
        ref = last_buy or avg   # botul foloseste ULTIMUL buy ca referinta (TP_REFERENCE="last")
        gain = (px - ref) / ref * 100 if ref else 0.0
        trend = "UP  " if gr > 0 else ("DOWN" if gr < 0 else "flat")
        hard_px = ref * (1 + HARD_TP_PCT / 100) if ref else 0.0
        print(f"  {s}: px={px:.2f}  ref=ULTIMUL_buy {ref:.2f} (avg {avg:.2f})  gain={gain:+.1f}%")
        print(f"      trend_instant={trend} ({gr:+.4f})  |  free={free:.4f}  net={net:.4f}  sells={n_sells}")
        flags = []
        if gain >= HARD_TP_PCT:
            flags.append(f"HARD-TP ARMAT (vinde 50%={free*0.5:.3f})")
        elif gain >= GAIN_PCT and gr < 0:
            flags.append("VANZARE NORMALA ARMATA (gain>9.2% + trend DOWN -> vinde TOT)")
        elif gain >= GAIN_PCT and gr >= 0:
            flags.append(f"in profit dar trend UP -> tine (HARD-TP la px {hard_px:.2f})")
        else:
            flags.append(f"sub praguri (HARD-TP la px {hard_px:.2f})")
        print(f"      => {'; '.join(flags)}")
    print("=" * 50)


if __name__ == "__main__":
    main()
