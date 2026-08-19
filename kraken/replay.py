"""kraken/replay.py — motor de BACKTEST care ruleaza STRATEGIA LIVE (kraken/strategy.py:
Strategy.step + _apply_fill) peste OHLC istoric. Faza 2 a unificarii: fidelitate 100%
fata de productie — exact aceleasi decizii (tp_tranches, reintrare adaptiva/STOP-aware,
DCA cu toleranta) pe care vechiul simulate() NU le modela.

Design izolat: fill-ul OHLC (buy@low, sell@high) traieste AICI, in harness — NU atinge
calea reconcile() a botului live. Reutilizam _apply_fill (contabilitate reala:
qty/cost/realized/fee/inchidere ciclu) si step() (deciziile reale)."""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategy as _strat


def _silent(*_a, **_k):
    return None


def run_replay(ohlc, params, fee_pct: float = 0.26) -> dict:
    """ohlc: lista de (open, high, low, close). params: StratParams. fee_pct: comision
    per leg (%). Intoarce metrici compatibile cu simulate(): realized/net/fees/total/
    final_upnl/cycles/wins/maxdd/open_qty."""
    client = MagicMock()
    client.pair_info.return_value = None      # precizie implicita (fara retea)
    orig_notify = _strat.notify
    _strat.notify = _silent                   # fara push/desktop in replay
    try:
        s = _strat.Strategy(client, "REPLAY", params, dry_run=True)
        s._save = _silent                     # fara fisier de stare
        cycle0 = s.s.get("cycle", 1)
        wins = 0
        peak = 0.0
        maxdd = 0.0
        for (_o, h, l, c) in ohlc:
            # --- FILL OHLC-aware: buy se umple daca low<=limita, sell daca high>=limita.
            #     Ordinea buy-apoi-sell (ca simulate). _apply_fill = contabilitatea LIVE.
            for order in list(s.s["orders"]):
                if order not in s.s["orders"]:
                    continue
                if order["side"] != "buy":
                    continue
                if l <= order["price"]:
                    vol, px = order["vol"], order["price"]
                    s._remove(order)
                    s._apply_fill(order, vol, px, fee=fee_pct / 100 * vol * px)
            for order in list(s.s["orders"]):
                if order not in s.s["orders"]:
                    continue
                if order["side"] != "sell":
                    continue
                if h >= order["price"]:
                    vol, px = order["vol"], order["price"]
                    g0 = s.s["realized_gross"]
                    s._remove(order)
                    s._apply_fill(order, vol, px, fee=fee_pct / 100 * vol * px)
                    if s.s["realized_gross"] > g0:      # castig brut (px>avg), ca simulate
                        wins += 1
            # --- DECIZIA = step-ul LIVE (entry/DCA/TP/stop/reintrare) pe close ---
            s.step(c)
            # --- equity mark-to-market pe close, pt drawdown ---
            qty = s.s["qty"]
            upnl = (c - s.s["cost"] / qty) * qty if qty > 1e-12 else 0.0
            eq = s.s["realized_net"] + upnl
            peak = max(peak, eq)
            maxdd = max(maxdd, peak - eq)
    finally:
        _strat.notify = orig_notify

    qty = s.s["qty"]
    final_upnl = (ohlc[-1][3] - s.s["cost"] / qty) * qty if qty > 1e-12 else 0.0
    r = lambda x: round(x, 10)
    return {
        "realized": r(s.s["realized_gross"]),
        "net": r(s.s["realized_net"]),
        "fees": r(s.s["fees_total"]),
        "total": r(s.s["realized_net"] + final_upnl),
        "final_upnl": r(final_upnl),
        "cycles": s.s.get("cycle", 1) - cycle0,
        "wins": wins,
        "maxdd": r(maxdd),
        "open_qty": r(qty),
    }
