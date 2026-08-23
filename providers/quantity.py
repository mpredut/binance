"""Decizia unica provider-neutral pentru sold si cantitatea unui ordin spot."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Optional


QUOTE_SUFFIXES = ("USDC", "EUR", "RON", "BTC", "ETH", "USD")


@dataclass(frozen=True)
class QuantityDecision:
    requested_qty: float
    balance_cap: Optional[float]
    policy_cap: Optional[float]
    fee_cap: Optional[float]
    final_qty: float
    refuse_reason: Optional[str] = None
    balance_asset: Optional[str] = None


def resolve_assets(symbol: str, base: Optional[str] = None,
                   quote: Optional[str] = None) -> tuple[str, Optional[str]]:
    symbol_u = symbol.upper()
    base_u = base.upper() if base else None
    quote_u = quote.upper() if quote else None
    if base_u and quote_u:
        return base_u, quote_u
    if base_u and symbol_u.startswith(base_u) and symbol_u != base_u:
        return base_u, quote_u or symbol_u[len(base_u):]
    for suffix in QUOTE_SUFFIXES:
        if symbol_u.endswith(suffix) and len(symbol_u) > len(suffix):
            return base_u or symbol_u[:-len(suffix)], quote_u or suffix
    return base_u or symbol_u, quote_u


def balance_cap_quantity(free_balance: Callable[[str], Optional[float]],
                         symbol: str, side: str, price: float, *,
                         base: Optional[str] = None,
                         quote: Optional[str] = None
                         ) -> tuple[Optional[float], Optional[str]]:
    base, quote = resolve_assets(symbol, base, quote)
    side = side.upper()
    asset = base if side == "SELL" else quote
    if side not in {"BUY", "SELL"}:
        raise ValueError("side trebuie sa fie BUY sau SELL")
    if not asset or (side == "BUY" and price <= 0):
        return None, asset
    raw = free_balance(asset)
    if raw is None:
        return None, asset
    balance = float(raw)
    if not math.isfinite(balance) or balance < 0:
        return None, asset
    return (balance if side == "SELL" else balance / float(price)), asset


def fee_cap_quantity(available_qty: float, fee_rate: float) -> float:
    """Return the base-quantity cap after reserving for fees."""
    available = max(0.0, float(available_qty))
    fee = max(0.0, float(fee_rate))
    return available / (1.0 + fee)


def decide_quantity(provider, symbol: str, side: str, price: float,
                    requested_qty: Optional[float], *, base: Optional[str] = None,
                    quote: Optional[str] = None, cancelorders: bool = False,
                    hours: float = 5,
                    apply_policy: bool = True) -> QuantityDecision:
    # Historical safe contract: None means "maximum permitted", not missing
    # validation. Balance, policy, and the fee cap determine final quantity.
    requested = float("inf") if requested_qty is None else max(0.0, float(requested_qty))
    balance_cap, asset = balance_cap_quantity(
        provider.free_balance, symbol, side, price, base=base, quote=quote)
    if balance_cap is None:
        return QuantityDecision(requested, None, None, None, 0.0,
                                "balance_unavailable", asset)
    if balance_cap <= 0:
        return QuantityDecision(requested, 0.0, 0.0, 0.0, 0.0,
                                "insufficient_funds", asset)
    policy_cap = requested
    if apply_policy:
        policy_cap = provider.policy_cap_quantity(
            symbol, side, price, requested, balance_cap,
            base=base, quote=quote, cancelorders=cancelorders, hours=hours)
        if policy_cap is None:
            return QuantityDecision(requested, balance_cap, None, None, 0.0,
                                    "policy_unavailable", asset)
        policy_cap = max(0.0, float(policy_cap))
    fee_cap = max(0.0, float(provider.fee_cap_quantity(
        symbol, side, price, balance_cap)))
    final = min(requested, balance_cap, policy_cap, fee_cap)
    reason = None if final > 0 else "qty_zero_after_policy"
    return QuantityDecision(requested, balance_cap, policy_cap, fee_cap,
                            final, reason, asset)
