"""Model de execuție OHLC comun adaptoarelor de replay.

Strategiile decid ordinele; acest modul descrie numai cum pot fi executate:
spread, slippage pentru market, ordine intrabar și tranșe asincrone. Valorile
implicite reproduc comportamentul istoric al backtesterelor.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Callable


_POLICIES = {"buy_first", "sell_first", "worst_case"}


@dataclass(frozen=True)
class FeeModel:
    """Fee procentual per fill, separat pentru LIMIT și MARKET."""

    limit_fee_pct: float
    market_fee_pct: float

    def __post_init__(self) -> None:
        for name, value in (
            ("limit_fee_pct", self.limit_fee_pct),
            ("market_fee_pct", self.market_fee_pct),
        ):
            if not math.isfinite(value) or value < 0 or value >= 100:
                raise ValueError(f"{name} trebuie să fie în intervalul [0, 100)")

    def rate_pct(self, *, market: bool) -> float:
        return self.market_fee_pct if market else self.limit_fee_pct


@dataclass(frozen=True)
class ExecutionModel:
    """Ipoteze de fill, exprimate explicit și serializabil."""

    spread_bps: float = 0.0
    market_slippage_bps: float = 0.0
    partial_fill_ratio: float = 1.0
    intrabar_policy: str = "buy_first"

    def __post_init__(self) -> None:
        if not math.isfinite(self.spread_bps) or self.spread_bps < 0:
            raise ValueError("spread_bps nu poate fi negativ")
        if not math.isfinite(self.market_slippage_bps) or self.market_slippage_bps < 0:
            raise ValueError("market_slippage_bps nu poate fi negativ")
        if not math.isfinite(self.partial_fill_ratio) or not 0 < self.partial_fill_ratio <= 1:
            raise ValueError("partial_fill_ratio trebuie să fie în intervalul (0, 1]")
        if self.spread_bps >= 20_000:
            raise ValueError("spread_bps trebuie să fie sub 20.000")
        if self.spread_bps / 2.0 + self.market_slippage_bps >= 10_000:
            raise ValueError("costul advers market trebuie să fie sub 100%")
        if self.intrabar_policy not in _POLICIES:
            raise ValueError(
                f"intrabar_policy invalid: {self.intrabar_policy}; "
                f"așteptat {sorted(_POLICIES)}"
            )

    def with_policy(self, policy: str) -> "ExecutionModel":
        return replace(self, intrabar_policy=policy)

    def side_order(self) -> tuple[str, str]:
        if self.intrabar_policy == "sell_first":
            return "sell", "buy"
        return "buy", "sell"

    def limit_touched(
        self,
        side: str,
        *,
        high: float,
        low: float,
        limit: float,
    ) -> bool:
        """Evaluează limita contra bid/ask estimate din OHLC-ul observat."""
        half_spread = self.spread_bps / 20_000.0
        if side.lower() == "buy":
            return float(low) * (1.0 + half_spread) <= float(limit)
        if side.lower() == "sell":
            return float(high) * (1.0 - half_spread) >= float(limit)
        raise ValueError(f"side invalid: {side}")

    def market_price(self, side: str, open_price: float) -> float:
        """Preț market advers: jumătate de spread plus slippage."""
        adverse = (self.spread_bps / 2.0 + self.market_slippage_bps) / 10_000.0
        if side.lower() == "buy":
            return float(open_price) * (1.0 + adverse)
        if side.lower() == "sell":
            return float(open_price) * (1.0 - adverse)
        raise ValueError(f"side invalid: {side}")


def split_order_fill(
    order: dict,
    *,
    quantity_key: str,
    ratio: float,
    amount_key: str | None = None,
    force_full: bool = False,
) -> tuple[dict, float, bool]:
    """Extrage o tranșă și păstrează restul pe ordinul original.

    Raportul este aplicat cantității inițiale, nu repetat cantității rămase;
    astfel ``0.25`` închide ordinul în cel mult patru evenimente eligibile.
    """
    remaining = float(order[quantity_key])
    if remaining <= 0:
        raise ValueError("ordinul nu mai are cantitate de executat")
    original_key = f"_replay_original_{quantity_key}"
    original = float(order.setdefault(original_key, remaining))
    chunk = remaining if force_full else min(remaining, original * ratio)
    if remaining - chunk <= max(1e-12, original * 1e-12):
        chunk = remaining
    fill = dict(order)
    fill[quantity_key] = chunk

    if amount_key is not None:
        remaining_amount = float(order.get(amount_key, 0.0))
        fill_amount = remaining_amount * (chunk / remaining)
        fill[amount_key] = fill_amount
        order[amount_key] = max(0.0, remaining_amount - fill_amount)

    order[quantity_key] = max(0.0, remaining - chunk)
    complete = order[quantity_key] <= max(1e-12, original * 1e-12)
    already_filled = bool(order.get("_replay_had_fill"))
    order["_replay_had_fill"] = True
    # DCA/trend sunt evenimente de ordin, nu câte un eveniment per partial fill.
    if already_filled and fill.get("side", "").lower() == "buy":
        if fill.get("kind") in {"DCA", "TREND_ENTRY"}:
            fill["kind"] = f"{fill['kind']}_PARTIAL"
    return fill, chunk, complete


def choose_intrabar_scenario(
    model: ExecutionModel,
    run_once: Callable[[ExecutionModel], dict[str, Any]],
) -> dict[str, Any]:
    """Rulează extremele deterministe și alege randamentul mai slab."""
    if model.intrabar_policy != "worst_case":
        return run_once(model)

    scenarios = {
        policy: run_once(model.with_policy(policy))
        for policy in ("buy_first", "sell_first")
    }
    selected_policy, selected = min(
        scenarios.items(), key=lambda item: float(item[1]["return_pct"]),
    )
    result = dict(selected)
    result["intrabar_policy_selected"] = selected_policy
    result["intrabar_scenarios"] = {
        policy: {
            "return_pct": metrics["return_pct"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "fills": metrics["fills"],
            "ambiguous_bars": metrics.get("ambiguous_bars", 0),
        }
        for policy, metrics in scenarios.items()
    }
    return result
