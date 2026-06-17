#!/usr/bin/env python3
"""
trend_stats.py — statistici de trend folosite ca FILTRE peste detectorul existent:

  * mann_kendall(y)  -> (S, Z, p): test neparametric de semnificatie a trendului.
                        p mic = trend real; p mare = panta e probabil zgomot.
  * hurst_rs(y)      -> H: exponentul Hurst (R/S). H>0.5 = serie persistenta
                        (trend-following functioneaza); H<0.5 = mean-reverting
                        (trendurile se inverseaza repede); ~0.5 = random walk.
"""

from __future__ import annotations

from math import erfc, sqrt

import numpy as np


def mann_kendall(y) -> tuple[int, float, float]:
    """Testul Mann-Kendall. Întoarce (S, Z, p_bilateral). n<8 -> nesemnificativ."""
    y = np.asarray(y, dtype=float)
    n = len(y)
    if n < 8:
        return 0, 0.0, 1.0
    s = 0.0
    for k in range(n - 1):
        s += np.sign(y[k + 1:] - y[k]).sum()
    _, counts = np.unique(y, return_counts=True)          # corectie pt valori egale
    var = (n * (n - 1) * (2 * n + 5) - (counts * (counts - 1) * (2 * counts + 5)).sum()) / 18.0
    if var <= 0:
        return int(s), 0.0, 1.0
    z = (s - np.sign(s)) / sqrt(var)
    p = erfc(abs(z) / sqrt(2))                            # bilateral, normala
    return int(s), float(z), float(p)


def hurst_rs(y) -> float | None:
    """Exponent Hurst prin VARIANTA AGREGATA pe log-randamente:
    Var(suma a k randamente) ~ k^(2H), H = panta/2 in log-log.
    Aleasa in locul R/S clasic, care e biasat spre 0.5 pe serii anti-persistente
    (un mean-reverter clar citea ~0.49 si parea random-walk). None = serie scurta."""
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
        return "persistent"        # trend-following favorizat
    if h < lo:
        return "mean-reverting"    # trendurile mor repede
    return "random-walk"
