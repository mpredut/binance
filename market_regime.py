"""Clasificare provider-neutral a regimului de piata.

Providerul/sursa de date produce un snapshot normalizat. Strategiile consulta
aceeasi decizie, dar isi pastreaza separat politica financiara (entry, exit,
market/limit). Modulul nu importa Binance, Kraken, Hyperliquid sau cacheManager.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
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
