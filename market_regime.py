"""Clasificare provider-neutral a regimului de piata.

Providerul/sursa de date produce un snapshot normalizat. Strategiile consulta
aceeasi decizie, dar isi pastreaza separat politica financiara (entry, exit,
market/limit). Modulul nu importa Binance, Kraken, Hyperliquid sau cacheManager.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
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
    horizon: str = "unspecified"
    source: str = "unknown"
    fallback_used: bool = False

    @property
    def directional(self) -> bool:
        return self.regime in {"bull", "bear"}

    def adverse_to(self, exposure_side: str) -> bool:
        side = str(exposure_side or "").upper()
        return ((side == "LONG" and self.regime == "bear") or
                (side == "SOLD" and self.regime == "bull"))


@dataclass(frozen=True)
class CompositeMarketRegimeDecision:
    """Asset regime enriched by broader crypto context."""

    regime: str
    score: float
    confidence: float
    conviction: float
    actionable: bool
    conflict: bool
    pattern: str
    use_case: str
    reason: str
    components: tuple


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


class MarketRegimeHorizon(str, Enum):
    """Decision horizon; execution policy remains owned by each strategy."""

    SHORT = "short"
    LONG = "long"

    @classmethod
    def parse(cls, value) -> "MarketRegimeHorizon":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value or cls.SHORT.value).lower())
        except ValueError as exc:
            raise ValueError(f"unsupported market-regime horizon: {value!r}") from exc


_HORIZON_SOURCES = {
    MarketRegimeHorizon.SHORT: ((1, 900.0), (5, 1800.0)),
    MarketRegimeHorizon.LONG: ((240, 7 * 86400.0), (1440, 30 * 86400.0)),
}

_COMPOSITE_PROFILES = {
    "execution": {
        "asset_short": 0.65, "asset_long": 0.25,
        "benchmark_short": 0.05, "benchmark_long": 0.05,
    },
    "balanced": {
        "asset_short": 0.45, "asset_long": 0.35,
        "benchmark_short": 0.10, "benchmark_long": 0.10,
    },
    "risk": {
        "asset_short": 0.25, "asset_long": 0.55,
        "benchmark_short": 0.05, "benchmark_long": 0.15,
    },
}


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

    @staticmethod
    def _annotate(decision, *, horizon, source, fallback_used=False):
        return replace(decision, horizon=horizon.value, source=str(source),
                       fallback_used=bool(fallback_used))

    def resolve(self, provider, symbol: str, *, horizon="short", snapshot=None,
                interval_min=None, window_seconds=None, allow_fallback=True):
        """Resolve a regime from ordered, same-venue sources.

        A valid snapshot is preferred. Unknown/invalid primary data falls back to
        completed OHLC candles for the requested horizon. Sources are never mixed
        across venues implicitly.
        """
        parsed = MarketRegimeHorizon.parse(horizon)
        attempts = []
        if snapshot is not None:
            decision = self._annotate(
                self.evaluate_snapshot(snapshot), horizon=parsed, source="snapshot")
            attempts.append(decision)
            if decision.fresh or not allow_fallback:
                return decision

        configured = _HORIZON_SOURCES[parsed]
        if interval_min is not None or window_seconds is not None:
            primary = (int(interval_min or configured[0][0]),
                       float(window_seconds or configured[0][1]))
            configured = (primary,) + tuple(item for item in configured if item != primary)
        if not allow_fallback:
            configured = configured[:1]

        for index, (interval, window) in enumerate(configured):
            decision = self._annotate(
                self.evaluate_provider(provider, symbol, interval_min=interval,
                                       window_seconds=window),
                horizon=parsed, source=f"ohlc:{interval}m",
                fallback_used=bool(attempts or index))
            attempts.append(decision)
            if decision.fresh:
                return decision
        reason = ",".join(f"{item.source}={item.reason}" for item in attempts)
        return self._annotate(
            self.evaluator.unknown(f"all_sources_failed:{reason}"),
            horizon=parsed, source="none", fallback_used=len(attempts) > 1)

    @staticmethod
    def _signed_score(decision, strength_cap):
        if not decision.fresh or decision.regime == "unknown":
            return None
        if decision.regime == "sideways":
            return 0.0
        strength = float(decision.strength or 0.0)
        magnitude = min(strength, strength_cap) / strength_cap
        return magnitude if decision.regime == "bull" else -magnitude

    def compose(self, asset_short, asset_long, benchmarks=(), *,
                use_case="balanced", weights=None, strength_cap=6.0,
                directional_threshold=0.15):
        """Combine asset horizons with optional benchmark context.

        Benchmark-only data is never actionable. Missing components retain their
        nominal weight as lost confidence instead of amplifying the remaining data.
        """
        use_case = str(use_case or "balanced").lower()
        if use_case not in _COMPOSITE_PROFILES:
            raise ValueError(f"unsupported composite use case: {use_case!r}")
        raw_weights = weights or _COMPOSITE_PROFILES[use_case]
        expected = {"asset_short", "asset_long", "benchmark_short", "benchmark_long"}
        if set(raw_weights) != expected:
            raise ValueError(f"composite weights must contain exactly {sorted(expected)}")
        normalized = {name: float(value) for name, value in raw_weights.items()}
        if (any(not math.isfinite(value) or value < 0 for value in normalized.values()) or
                not math.isclose(sum(normalized.values()), 1.0, abs_tol=1e-9)):
            raise ValueError("composite weights must be finite, non-negative, and sum to 1")
        strength_cap = float(strength_cap)
        directional_threshold = float(directional_threshold)
        if (not math.isfinite(strength_cap) or strength_cap <= 0 or
                not math.isfinite(directional_threshold) or
                not 0 <= directional_threshold <= 1):
            raise ValueError("invalid composite strength cap or threshold")

        benchmark_pairs = tuple(benchmarks or ())
        components = []
        weighted_score = confidence = 0.0
        asset_scores = []
        benchmark_scores = []

        def add(label, decision, weight, bucket):
            nonlocal weighted_score, confidence
            score = self._signed_score(decision, strength_cap)
            components.append((label, decision.regime, decision.source, score))
            if score is not None:
                weighted_score += weight * score
                confidence += weight
                bucket.append(score)

        add("asset_short", asset_short, normalized["asset_short"], asset_scores)
        add("asset_long", asset_long, normalized["asset_long"], asset_scores)
        count = len(benchmark_pairs)
        for name, short, long in benchmark_pairs:
            add(f"{name}_short", short,
                normalized["benchmark_short"] / max(1, count), benchmark_scores)
            add(f"{name}_long", long,
                normalized["benchmark_long"] / max(1, count), benchmark_scores)

        actionable = bool(asset_scores)
        asset_conflict = len(asset_scores) > 1 and min(asset_scores) < 0 < max(asset_scores)
        asset_mean = sum(asset_scores) / len(asset_scores) if asset_scores else 0.0
        benchmark_mean = (sum(benchmark_scores) / len(benchmark_scores)
                          if benchmark_scores else 0.0)
        market_conflict = bool(asset_scores and benchmark_scores and
                               asset_mean * benchmark_mean < 0)
        conflict = asset_conflict or market_conflict
        if not actionable:
            regime, pattern, reason = (
                "unknown", "insufficient_asset_data", "asset_signal_unavailable")
        elif weighted_score > directional_threshold:
            regime, reason = "bull", "positive_composite"
            if len(asset_scores) > 1 and asset_scores[0] < 0 < asset_scores[1]:
                pattern = "bullish_pullback"
            elif market_conflict:
                pattern = "asset_bull_market_headwind"
            else:
                pattern = "aligned_bull"
        elif weighted_score < -directional_threshold:
            regime, reason = "bear", "negative_composite"
            if len(asset_scores) > 1 and asset_scores[1] < 0 < asset_scores[0]:
                pattern = "bearish_rebound"
            elif market_conflict:
                pattern = "asset_bear_market_tailwind"
            else:
                pattern = "aligned_bear"
        else:
            regime, reason = "sideways", ("conflicting_components" if conflict
                                           else "below_composite_threshold")
            pattern = "mixed" if conflict else "neutral"
        conviction = min(1.0, abs(weighted_score) * confidence)
        if conflict:
            conviction *= 0.75
        return CompositeMarketRegimeDecision(
            regime=regime, score=weighted_score, confidence=min(1.0, confidence),
            conviction=conviction, actionable=actionable, conflict=conflict,
            pattern=pattern, use_case=use_case, reason=reason,
            components=tuple(components))

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
