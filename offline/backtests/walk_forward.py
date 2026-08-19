"""Splituri temporale walk-forward fără shuffle și fără leakage."""

from __future__ import annotations

from dataclasses import dataclass
import statistics


@dataclass(frozen=True)
class WalkForwardFold:
    train: slice
    validation: slice
    test: slice


def walk_forward_splits(
    sample_count: int,
    *,
    train_size: int,
    validation_size: int,
    test_size: int,
    step_size: int | None = None,
    anchored_train: bool = False,
) -> list[WalkForwardFold]:
    """Returnează fold-uri strict cronologice ``train < validation < test``.

    În modul rolling, train-ul are lungime fixă. Cu ``anchored_train=True``,
    începutul rămâne 0 și fereastra de train crește la fiecare pas.
    """
    sizes = (sample_count, train_size, validation_size, test_size)
    if any(not isinstance(value, int) or value <= 0 for value in sizes):
        raise ValueError("sample_count și dimensiunile ferestrelor trebuie să fie întregi pozitivi")
    if step_size is None:
        step_size = test_size
    if not isinstance(step_size, int) or step_size <= 0:
        raise ValueError("step_size trebuie să fie un întreg pozitiv")

    required = train_size + validation_size + test_size
    if sample_count < required:
        return []

    folds = []
    offset = 0
    while offset + required <= sample_count:
        train_start = 0 if anchored_train else offset
        train_stop = offset + train_size
        validation_stop = train_stop + validation_size
        test_stop = validation_stop + test_size
        folds.append(WalkForwardFold(
            train=slice(train_start, train_stop),
            validation=slice(train_stop, validation_stop),
            test=slice(validation_stop, test_stop),
        ))
        offset += step_size
    return folds


def summarize_test_windows(windows: list[dict]) -> dict:
    """Agregă ferestre TEST comparabile, fără să ascundă cel mai slab regim.

    Fiecare element trebuie să conțină ``return_pct``, ``max_drawdown_pct``,
    ``buy_hold_return_pct``, ``cycles`` și ``fills``. Ferestrele au pondere
    egală; nu amestecăm curbele de equity cu frecvențe diferite.
    """
    if not windows:
        raise ValueError("este necesară cel puțin o fereastră TEST")
    required = {
        "return_pct", "max_drawdown_pct", "buy_hold_return_pct", "cycles", "fills",
    }
    for window in windows:
        missing = required.difference(window)
        if missing:
            raise ValueError(f"câmpuri lipsă în fereastra TEST: {sorted(missing)}")

    returns = [float(window["return_pct"]) for window in windows]
    drawdowns = [float(window["max_drawdown_pct"]) for window in windows]
    up_market = [
        float(window["return_pct"]) for window in windows
        if float(window["buy_hold_return_pct"]) > 0
    ]
    down_market = [
        float(window["return_pct"]) for window in windows
        if float(window["buy_hold_return_pct"]) <= 0
    ]
    return {
        "window_count": len(windows),
        "mean_return_pct": statistics.fmean(returns),
        "median_return_pct": statistics.median(returns),
        "worst_return_pct": min(returns),
        "best_return_pct": max(returns),
        "positive_windows": sum(value > 0 for value in returns),
        "worst_max_drawdown_pct": max(drawdowns),
        "mean_max_drawdown_pct": statistics.fmean(drawdowns),
        "up_market_windows": len(up_market),
        "mean_return_up_market_pct": statistics.fmean(up_market) if up_market else None,
        "down_market_windows": len(down_market),
        "mean_return_down_market_pct": (
            statistics.fmean(down_market) if down_market else None
        ),
        "total_cycles": sum(int(window["cycles"]) for window in windows),
        "total_fills": sum(int(window["fills"]) for window in windows),
    }
