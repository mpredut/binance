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
from offline.backtests.metrics import calculate_performance_metrics


def _silent(*_a, **_k):
    return None


def run_replay(ohlc, params, fee_pct: float = 0.26,
               bar_minutes: float | None = None) -> dict:
    """ohlc: lista de (open, high, low, close). params: StratParams. fee_pct: comision
    per leg (%). Intoarce metrici compatibile cu simulate(): realized/net/fees/total/
    final_upnl/cycles/wins/maxdd/open_qty."""
    if not ohlc:
        raise ValueError("ohlc nu poate fi gol")
    if (params.trend_overlay or params.dca_trend_brake) and (
            bar_minutes is None or float(bar_minutes) != float(params.trend_interval)):
        raise ValueError(
            "trend_overlay/dca_trend_brake cere ca bar_minutes să fie egal cu trend_interval "
            f"({params.trend_interval} minute); resampling-ul nu este implementat"
        )
    if params.tp_trail_adaptive and (
            bar_minutes is None
            or float(bar_minutes) != float(params.tp_trail_vol_interval)):
        raise ValueError(
            "tp_trail_adaptive cere ca bar_minutes să fie egal cu "
            f"tp_trail_vol_interval ({params.tp_trail_vol_interval} minute)"
        )
    if params.reentry_adaptive and bar_minutes is None:
        raise ValueError("reentry_adaptive cere bar_minutes pentru volatilitatea temporală")
    client = MagicMock()
    client.pair_info.return_value = None      # precizie implicita (fara retea)
    orig_notify = _strat.notify
    _strat.notify = _silent                   # fara push/desktop in replay
    try:
        s = _strat.Strategy(
            client, "REPLAY", params, dry_run=True,
            # Nu citi deloc un eventual .state_REPLAY rămas pe disc. Constructorul
            # live își păstrează comportamentul când initial_state nu este furnizat.
            initial_state=_strat._new_state(),
            replay_mode=True,
        )
        s._save = _silent                     # fara fisier de stare
        cycle0 = s.s.get("cycle", 1)
        wins = 0
        fill_count = 0
        turnover_notional = 0.0
        trade_pnls = []
        cycle_net_start = s.s["realized_net"]
        initial_capital = float(params.max_budget)
        equity_curve = [initial_capital]
        exposure = []
        for bar_index, (_o, h, l, c) in enumerate(ohlc):
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
                    fill_count += 1
                    turnover_notional += vol * px
            for order in list(s.s["orders"]):
                if order not in s.s["orders"]:
                    continue
                if order["side"] != "sell":
                    continue
                # SELL market (iesire trailing/stop) = se executa imediat, la open-ul barei
                # (nu asteapta high>=limita — altfel nu iese intr-o cadere brusca). SELL limita
                # (TP asezat deasupra) = fill doar daca high atinge limita.
                if order.get("market"):
                    fill_ok, px = True, _o
                elif h >= order["price"]:
                    fill_ok, px = True, order["price"]
                else:
                    fill_ok, px = False, None
                if fill_ok:
                    vol = order["vol"]
                    g0 = s.s["realized_gross"]
                    s._remove(order)
                    s._apply_fill(order, vol, px, fee=fee_pct / 100 * vol * px)
                    fill_count += 1
                    turnover_notional += vol * px
                    if s.s["realized_gross"] > g0:      # castig brut (px>avg), ca simulate
                        wins += 1
                    if s.s.get("cycle", 1) > cycle0 + len(trade_pnls):
                        trade_pnls.append(s.s["realized_net"] - cycle_net_start)
                        cycle_net_start = s.s["realized_net"]
            # --- DECIZIA = step-ul LIVE (entry/DCA/TP/stop/reintrare) pe close ---
            replay_time = bar_index * bar_minutes * 60 if bar_minutes else bar_index
            s.step(c, timestamp=replay_time)
            # --- equity mark-to-market pe close, pt drawdown ---
            qty = s.s["qty"]
            upnl = (c - s.s["cost"] / qty) * qty if qty > 1e-12 else 0.0
            equity_curve.append(initial_capital + s.s["realized_net"] + upnl)
            exposure.append(qty > 1e-12)
    finally:
        _strat.notify = orig_notify

    qty = s.s["qty"]
    final_upnl = (ohlc[-1][3] - s.s["cost"] / qty) * qty if qty > 1e-12 else 0.0
    periods_per_year = 365.0 * 24 * 60 / bar_minutes if bar_minutes else None
    performance = calculate_performance_metrics(
        equity_curve,
        initial_capital=initial_capital,
        periods_per_year=periods_per_year,
        exposure=exposure,
        trade_pnls=trade_pnls,
        turnover_notional=turnover_notional,
    )
    r = lambda x: round(x, 10)
    result = {
        "realized": r(s.s["realized_gross"]),
        "net": r(s.s["realized_net"]),
        "fees": r(s.s["fees_total"]),
        "total": r(s.s["realized_net"] + final_upnl),
        "final_upnl": r(final_upnl),
        "cycles": s.s.get("cycle", 1) - cycle0,
        "wins": wins,
        "maxdd": r(performance["max_drawdown_abs"]),
        "open_qty": r(qty),
        "fills": fill_count,
    }
    result.update({key: (round(value, 10) if isinstance(value, float) else value)
                   for key, value in performance.items()})
    return result
