"""Replay OHLC fidel pentru motorul live Trading212.

Deciziile sunt produse de ``strategy.Strategy.step``. Acest fișier modelează
doar mediul: fill-uri pentru ordinele deja existente, ceas, OHLC și metrici.
Ordinele decise la close pot fi executate cel mai devreme în bara următoare.
"""

from __future__ import annotations

from collections import deque
import math
import os
import sys
from unittest.mock import MagicMock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import strategy as _strat
from offline.backtests.metrics import calculate_performance_metrics


class _SimClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _ols_slope_pct(closes: list[float]) -> float | None:
    values = closes[-12:]
    if len(values) < 6:
        return None
    n = len(values)
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    denominator = sum((index - mean_x) ** 2 for index in range(n))
    if denominator == 0 or mean_y == 0:
        return None
    slope = sum(
        (index - mean_x) * (value - mean_y)
        for index, value in enumerate(values)
    ) / denominator
    return slope / mean_y * 100.0


def run_replay(
    ohlc,
    params: _strat.StratParams,
    *,
    bar_minutes: float | None = None,
    fx_to_usd: float = 1.0,
    periods_per_year: float | None = None,
    warmup_ohlc=(),
) -> dict:
    """Rulează strategia live T212 pe ``(open, high, low, close)``.

    Gate-ul DCA live citește bare Yahoo de 5 minute. Când este activ, replay-ul
    refuză alte cadențe pentru a nu pretinde paritate pe un semnal diferit.
    """
    if not ohlc:
        raise ValueError("ohlc nu poate fi gol")
    if fx_to_usd <= 0:
        raise ValueError("fx_to_usd trebuie să fie pozitiv")
    if params.dca_trend_gate_pct > 0 and bar_minutes != 5:
        raise ValueError(
            "dca_trend_gate_pct cere bare de 5 minute, aceeași cadență ca semnalul live Yahoo"
        )

    client = MagicMock()
    clock = _SimClock()
    trend_closes: deque[float] = deque(maxlen=12)
    trend_closes.extend(float(row[3]) for row in warmup_ohlc)
    original_log = _strat.log
    original_notify = _strat.notify
    _strat.log = lambda *_args, **_kwargs: None
    _strat.notify = lambda *_args, **_kwargs: None
    try:
        engine = _strat.Strategy(
            client,
            "REPLAY_US_EQ",
            params,
            dry_run=True,
            initial_state=_strat._new_state(),
            fx_to_usd=fx_to_usd,
            clock=clock,
            trend_slope_provider=lambda _symbol: _ols_slope_pct(list(trend_closes)),
        )
        engine._save = lambda: None
        initial_capital = float(params.max_budget) * fx_to_usd
        equity_curve = [initial_capital]
        exposure = []
        trade_pnls = []
        turnover_notional = 0.0
        fills = wins = 0
        cycle0 = engine.s.get("cycle", 1)

        for bar_index, (open_, high, low, close) in enumerate(ohlc):
            clock.value = bar_index * bar_minutes * 60 if bar_minutes else float(bar_index)

            # Fill numai pentru ordine plasate în bare anterioare.
            for order in list(engine.s["orders"]):
                if order not in engine.s["orders"] or order["side"] != "BUY":
                    continue
                if low <= order["limit"]:
                    engine._remove_order(order)
                    engine._apply_fill(order, order["qty"], order["limit"])
                    fills += 1
                    turnover_notional += order["qty"] * order["limit"]

            for order in list(engine.s["orders"]):
                if order not in engine.s["orders"] or order["side"] != "SELL":
                    continue
                if high >= order["limit"]:
                    avg = engine._avg_cost() or order["limit"]
                    qty = min(order["qty"], engine.s["qty"])
                    _gross, _fee, net = _strat._sell_pnl(
                        avg, order["limit"], qty, params.fx_fee_pct,
                    )
                    engine._remove_order(order)
                    if order.get("level") is not None:
                        engine.s.setdefault("tp_sold_levels", []).append(order["level"])
                    engine._apply_fill(order, qty, order["limit"])
                    fills += 1
                    wins += int(net > 0)
                    trade_pnls.append(net)
                    turnover_notional += qty * order["limit"]

            trend_closes.append(float(close))
            engine.step(float(close))

            qty = engine.s["qty"]
            avg = engine._avg_cost()
            unrealized = (float(close) - avg) * qty if avg and qty > 1e-9 else 0.0
            # Taxa de BUY este deja plătită chiar dacă poziția nu s-a închis.
            open_buy_fee = params.fx_fee_pct / 100.0 * engine.s["cost_usd"]
            equity_curve.append(
                initial_capital + engine.s["realized_net_usd"] + unrealized - open_buy_fee
            )
            exposure.append(qty > 1e-9)
    finally:
        _strat.log = original_log
        _strat.notify = original_notify

    if periods_per_year is None and bar_minutes:
        # Fallback tehnic; runnerul de acțiuni injectează calendarul 252 zile.
        periods_per_year = 365.0 * 24 * 60 / bar_minutes
    performance = calculate_performance_metrics(
        equity_curve,
        initial_capital=initial_capital,
        periods_per_year=periods_per_year,
        exposure=exposure,
        trade_pnls=trade_pnls,
        turnover_notional=turnover_notional,
    )
    qty = engine.s["qty"]
    avg = engine._avg_cost()
    final_upnl = (float(ohlc[-1][3]) - avg) * qty if avg and qty > 1e-9 else 0.0
    open_buy_fee = params.fx_fee_pct / 100.0 * engine.s["cost_usd"]
    rounded = lambda value: round(float(value), 10)
    result = {
        "realized": rounded(engine.s["realized_pnl_usd"]),
        "net": rounded(engine.s["realized_net_usd"]),
        "fees": rounded(engine.s["fees_usd"] + open_buy_fee),
        "total": rounded(engine.s["realized_net_usd"] + final_upnl - open_buy_fee),
        "final_upnl": rounded(final_upnl),
        "open_buy_fee": rounded(open_buy_fee),
        "cycles": engine.s.get("cycle", 1) - cycle0,
        "wins": wins,
        "maxdd": rounded(performance["max_drawdown_abs"]),
        "open_qty": rounded(qty),
        "fills": fills,
    }
    result.update({
        key: (rounded(value) if isinstance(value, float) and math.isfinite(value) else value)
        for key, value in performance.items()
    })
    return result
