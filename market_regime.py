"""Clasificare provider-neutral a regimului de piata.

Providerul/sursa de date produce un snapshot normalizat. Strategiile consulta
aceeasi decizie, dar isi pastreaza separat politica financiara (entry, exit,
market/limit). Modulul nu importa Binance, Kraken, Hyperliquid sau cacheManager.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import threading
import time
from collections import OrderedDict
from typing import Mapping, Optional


@dataclass(frozen=True)
class MarketRegimeDecision:
    regime: str                 # bull|bear|sideways|unknown
    gradient: Optional[float]
    epsilon: Optional[float]
    strength: Optional[float]   # abs(gradient)/epsilon
    fresh: bool
    reason: str
    n_samples: Optional[int] = None
    window_seconds: Optional[float] = None

    @property
    def directional(self) -> bool:
        return self.regime in {"bull", "bear"}

    def adverse_to(self, exposure_side: str) -> bool:
        side = str(exposure_side or "").upper()
        return ((side == "LONG" and self.regime == "bear") or
                (side == "SOLD" and self.regime == "bull"))


class MarketRegimeEvaluator:
    """Transforma un snapshot comun intr-o decizie determinista."""

    def __init__(self, strength_threshold: float = 2.0):
        threshold = float(strength_threshold)
        if not math.isfinite(threshold) or threshold <= 0:
            raise ValueError("strength_threshold trebuie sa fie pozitiv")
        self.strength_threshold = threshold

    def unknown(self, reason: str = "signal_unavailable") -> MarketRegimeDecision:
        return MarketRegimeDecision(
            regime="unknown", gradient=None, epsilon=None, strength=None,
            fresh=False, reason=reason)

    def evaluate(self, snapshot: Optional[Mapping]) -> MarketRegimeDecision:
        if not snapshot:
            return self.unknown()
        try:
            gradient = float(snapshot.get("gradient_recent", 0.0) or 0.0)
            epsilon = abs(float(snapshot.get("epsilon", 0.0) or 0.0))
        except (TypeError, ValueError):
            return self.unknown("invalid_signal")
        if not math.isfinite(gradient) or not math.isfinite(epsilon):
            return self.unknown("non_finite_signal")
        samples = snapshot.get("n_samples")
        window = snapshot.get("window_seconds")
        if epsilon <= 0:
            regime, strength, reason = "sideways", None, "noise_floor_zero"
        else:
            strength = abs(gradient) / epsilon
            if strength <= self.strength_threshold:
                regime, reason = "sideways", "below_strength_threshold"
            else:
                regime = "bull" if gradient > 0 else "bear"
                reason = "directional_signal"
        return MarketRegimeDecision(
            regime=regime, gradient=gradient, epsilon=epsilon,
            strength=strength, fresh=True, reason=reason,
            n_samples=(int(samples) if samples is not None else None),
            window_seconds=(float(window) if window is not None else None),
        )


class MarketRegimeService:
    """Construiește aceeași decizie din snapshot sau OHLC-ul oricărui provider.

    Cache-ul mic evită ca mai mulți boți să lovească același endpoint OHLC în
    aceeași secundă. Providerul rămâne sursa datelor; strategia rămâne proprietara
    deciziei financiare.
    """

    def __init__(self, strength_threshold: float = 2.0, *, cache_ttl_sec=30.0,
                 cache_max=256, clock=time.monotonic):
        self.evaluator = MarketRegimeEvaluator(strength_threshold)
        self.cache_ttl_sec = float(cache_ttl_sec)
        self.cache_max = max(1, int(cache_max))
        self.clock = clock
        if not math.isfinite(self.cache_ttl_sec) or self.cache_ttl_sec < 0:
            raise ValueError("cache_ttl_sec trebuie sa fie finit si >= 0")
        self._cache = OrderedDict()
        self._lock = threading.Lock()

    def evaluate_snapshot(self, snapshot: Optional[Mapping]) -> MarketRegimeDecision:
        return self.evaluator.evaluate(snapshot)

    def evaluate_provider(self, provider, symbol: str, *, interval_min=1,
                          window_seconds=900.0) -> MarketRegimeDecision:
        interval_min = int(interval_min)
        window_seconds = float(window_seconds)
        if interval_min <= 0 or not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError("intervalul si fereastra regimului trebuie sa fie pozitive")
        key = (str(getattr(provider, "name", "unknown")).lower(),
               str(symbol), interval_min, window_seconds)
        now = self.clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached and now - cached[0] <= self.cache_ttl_sec:
                self._cache.move_to_end(key)
                return cached[1]
        try:
            closes = provider.ohlc_closes(symbol, interval_min) or []
            decision = self.evaluate_closes(
                closes, interval_min=interval_min, window_seconds=window_seconds)
        except Exception as exc:  # date indisponibile => unknown explicit, nu trade orb
            decision = self.evaluator.unknown(
                f"source_error:{exc.__class__.__name__}")
        with self._lock:
            self._cache[key] = (now, decision)
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_max:
                self._cache.popitem(last=False)
        return decision

    def evaluate_closes(self, closes, *, interval_min=1,
                        window_seconds=900.0) -> MarketRegimeDecision:
        needed = max(3, int(math.ceil(float(window_seconds) / (int(interval_min) * 60))))
        try:
            values = [float(value) for value in list(closes)[-needed:]]
        except (TypeError, ValueError, OverflowError):
            return self.evaluator.unknown("invalid_ohlc")
        if len(values) < 3:
            return self.evaluator.unknown("insufficient_samples")
        if any(not math.isfinite(value) or value <= 0 for value in values):
            return self.evaluator.unknown("non_finite_ohlc")

        base = values[0]
        y = [value / base - 1.0 for value in values]
        n = len(y)
        x_mean = (n - 1) / 2.0
        y_mean = sum(y) / n
        sxx = sum((i - x_mean) ** 2 for i in range(n))
        slope = sum((i - x_mean) * (value - y_mean)
                    for i, value in enumerate(y)) / sxx
        intercept = y_mean - slope * x_mean
        residual_sq = sum(
            (value - (intercept + slope * i)) ** 2
            for i, value in enumerate(y))
        slope_error = math.sqrt(max(0.0, residual_sq) / max(1, n - 2) / sxx)
        epsilon = max(slope_error, 1e-12)
        return self.evaluator.evaluate({
            "gradient_recent": slope,
            "epsilon": epsilon,
            "n_samples": n,
            "window_seconds": float(window_seconds),
        })
