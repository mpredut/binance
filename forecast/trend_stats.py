#!/usr/bin/env python3
"""
trend_stats.py — trend statistics used as FILTERS over the existing detector:

  * mann_kendall(y) -> (S, Z, p): nonparametric trend-significance test.
                       Small p suggests a real trend; large p suggests slope noise.
  * hurst_rs(y) -> H: Hurst exponent. H>0.5 means persistence and favors
                     trend-following; H<0.5 means fast mean reversion; ~0.5 is random walk.
"""

from __future__ import annotations

from math import erfc, sqrt

import numpy as np


def mann_kendall(y) -> tuple[int, float, float]:
    """Return Mann-Kendall (S, Z, two-sided p); n<8 is treated as insignificant."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < 8:
        return 0, 0.0, 1.0
    s = 0.0
    for k in range(n - 1):
        s += np.sign(y[k + 1:] - y[k]).sum()
    _, counts = np.unique(y, return_counts=True)          # correction for tied values
    var = (n * (n - 1) * (2 * n + 5) - (counts * (counts - 1) * (2 * counts + 5)).sum()) / 18.0
    if var <= 0:
        return int(s), 0.0, 1.0
    z = (s - np.sign(s)) / sqrt(var)
    p = erfc(abs(z) / sqrt(2))                            # two-sided normal approximation
    return int(s), float(z), float(p)


def hurst_rs(y) -> float | None:
    """Estimate Hurst using aggregated log returns.
    Var(sum of k returns) ~ k^(2H), so H is half the log-log slope. This replaces
    classic R/S, which is biased toward 0.5 for anti-persistent series and could make
    clear mean reversion appear random. Return None for a short series."""
    y = np.asarray(y, dtype=float)
    if len(y) < 65 or np.any(y <= 0):
        return None
    r = np.diff(np.log(y))
    n = len(r)
    ks, vs = [], []
    k = 1
    while k <= n // 8:
        m = (n // k) * k
        agg = r[:m].reshape(-1, k).sum(axis=1)
        if len(agg) >= 8:
            v = float(np.var(agg))
            if v > 0:
                ks.append(k)
                vs.append(v)
        k *= 2
    if len(ks) < 3:
        return None
    slope, _ = np.polyfit(np.log(ks), np.log(vs), 1)
    return float(slope / 2.0)


def hurst_regime(h: float | None, lo: float = 0.45, hi: float = 0.55) -> str:
    if h is None:
        return "necunoscut"
    if h > hi:
        return "persistent"        # trend-following favored
    if h < lo:
        return "mean-reverting"    # trends end quickly
    return "random-walk"
