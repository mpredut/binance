"""Replay care rulează strategia live Kraken peste OHLC istoric.

Strategia decide ordinele. Harness-ul modelează separat fill-urile, costurile și
ambiguitatea intrabar, fără rețea, notificări sau stare persistentă.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategy as _strat
from offline.backtests.execution import (
    ExecutionModel,
    choose_intrabar_scenario,
    split_order_fill,
)
from offline.backtests.metrics import calculate_performance_metrics


def _silent(*_args, **_kwargs):
    return None


def _validate_replay(ohlc, params, bar_minutes: float | None) -> None:
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


def run_replay(
    ohlc,
    params,
    fee_pct: float = 0.26,
    bar_minutes: float | None = None,
    warmup_ohlc=(),
    execution: ExecutionModel | None = None,
) -> dict:
    """Rulează replay-ul; ipotezele implicite păstrează baseline-ul anterior."""
    _validate_replay(ohlc, params, bar_minutes)
    model = execution or ExecutionModel()
    return choose_intrabar_scenario(
        model,
        lambda scenario: _run_once(
            ohlc,
            params,
            fee_pct=fee_pct,
            bar_minutes=bar_minutes,
            warmup_ohlc=warmup_ohlc,
            execution=scenario,
        ),
    )


def _run_once(
    ohlc,
    params,
    *,
    fee_pct: float,
    bar_minutes: float | None,
    warmup_ohlc,
    execution: ExecutionModel,
) -> dict:
    client = MagicMock()
    client.pair_info.return_value = None
    original_notify = _strat.notify
    _strat.notify = _silent
    try:
        strategy = _strat.Strategy(
            client,
            "REPLAY",
            params,
            dry_run=True,
            initial_state=_strat._new_state(),
            replay_mode=True,
        )
        warmup_step = bar_minutes * 60 if bar_minutes else 1.0
        for index, (_open, _high, _low, close) in enumerate(
                warmup_ohlc, start=-len(warmup_ohlc)):
            strategy._shadow_prices.append((index * warmup_step, float(close)))
        strategy._save = _silent
        cycle0 = strategy.s.get("cycle", 1)
        wins = fill_count = ambiguous_bars = 0
        turnover_notional = 0.0
        trade_pnls = []
        cycle_net_start = strategy.s["realized_net"]
        initial_capital = float(params.max_budget)
        equity_curve = [initial_capital]
        exposure = []

        for bar_index, (open_, high, low, close) in enumerate(ohlc):
            def eligible(order: dict) -> bool:
                if order.get("market"):
                    return True
                return execution.limit_touched(
                    order["side"], high=high, low=low, limit=order["price"],
                )

            eligible_sides = {
                order["side"].lower()
                for order in strategy.s["orders"]
                if eligible(order)
            }
            if {"buy", "sell"}.issubset(eligible_sides):
                ambiguous_bars += 1

            for side in execution.side_order():
                for order in list(strategy.s["orders"]):
                    if order not in strategy.s["orders"] or order["side"] != side:
                        continue
                    if not eligible(order):
                        continue
                    market = bool(order.get("market"))
                    fill_order, volume, complete = split_order_fill(
                        order,
                        quantity_key="vol",
                        amount_key="amount" if side == "buy" else None,
                        ratio=execution.partial_fill_ratio,
                        force_full=market,
                    )
                    if complete:
                        strategy._remove(order)
                    price = (
                        execution.market_price(side, open_)
                        if market else float(order["price"])
                    )
                    if side == "sell":
                        volume = min(volume, strategy.s["qty"])
                        if volume <= 1e-12:
                            strategy._remove(order)
                            continue
                        fill_order["vol"] = volume
                        gross_before = strategy.s["realized_gross"]
                    fee = fee_pct / 100.0 * volume * price
                    strategy._apply_fill(fill_order, volume, price, fee=fee)
                    fill_count += 1
                    turnover_notional += volume * price
                    if side == "sell":
                        if strategy.s["realized_gross"] > gross_before:
                            wins += 1
                        if strategy.s.get("cycle", 1) > cycle0 + len(trade_pnls):
                            trade_pnls.append(
                                strategy.s["realized_net"] - cycle_net_start
                            )
                            cycle_net_start = strategy.s["realized_net"]

            replay_time = bar_index * bar_minutes * 60 if bar_minutes else bar_index
            strategy.step(close, timestamp=replay_time)
            qty = strategy.s["qty"]
            unrealized = (
                (close - strategy.s["cost"] / qty) * qty if qty > 1e-12 else 0.0
            )
            equity_curve.append(initial_capital + strategy.s["realized_net"] + unrealized)
            exposure.append(qty > 1e-12)
    finally:
        _strat.notify = original_notify

    qty = strategy.s["qty"]
    final_upnl = (
        (ohlc[-1][3] - strategy.s["cost"] / qty) * qty if qty > 1e-12 else 0.0
    )
    periods_per_year = 365.0 * 24 * 60 / bar_minutes if bar_minutes else None
    performance = calculate_performance_metrics(
        equity_curve,
        initial_capital=initial_capital,
        periods_per_year=periods_per_year,
        exposure=exposure,
        trade_pnls=trade_pnls,
        turnover_notional=turnover_notional,
    )
    rounded = lambda value: round(value, 10)
    result = {
        "realized": rounded(strategy.s["realized_gross"]),
        "net": rounded(strategy.s["realized_net"]),
        "fees": rounded(strategy.s["fees_total"]),
        "total": rounded(strategy.s["realized_net"] + final_upnl),
        "final_upnl": rounded(final_upnl),
        "cycles": strategy.s.get("cycle", 1) - cycle0,
        "wins": wins,
        "maxdd": rounded(performance["max_drawdown_abs"]),
        "open_qty": rounded(qty),
        "fills": fill_count,
        "ambiguous_bars": ambiguous_bars,
    }
    result.update({
        key: (round(value, 10) if isinstance(value, float) else value)
        for key, value in performance.items()
    })
    return result
