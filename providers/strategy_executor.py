"""Provider-neutral execution contract used by financially tracked strategies.

``strategies.spot_dca`` consumes this interface. Venue adapters normalize native
responses into the explicit types below so the strategy engine can operate on one
order lifecycle model.

``submit_order`` is intentionally distinct from ``MarketDataProvider.place_order``.
The former returns a venue order ID or raises ``ProviderError``; the latter is the
mechanical/legacy facade entry point and may return a native payload or ``None``.
Each adapter still applies its configured live-order gate.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Optional, Protocol, runtime_checkable


class ProviderError(Exception):
    """Venue-neutral error raised by strict strategy-execution adapters."""


@dataclass(frozen=True)
class OrderStatus:
    """Normalized order result: ``open``, ``closed``, ``canceled``, or ``expired``."""
    status: str
    filled_qty: float          # Executed quantity (Kraken ``vol_exec``).
    cost: float                # Executed notional; average price is cost / filled_qty.
    fee: float                 # Actual fee reported by the venue.
    venue_status: str = ""     # Native terminal reason (REJECTED vs CANCELED, etc.).

    def __post_init__(self):
        if self.status not in {"open", "closed", "canceled", "expired"}:
            raise ValueError(f"unnormalized order status: {self.status!r}")
        for name, raw in (("filled_qty", self.filled_qty),
                          ("cost", self.cost), ("fee", self.fee)):
            value = float(raw)
            # A negative fee is valid when the venue pays a maker rebate.
            if not math.isfinite(value) or (name != "fee" and value < 0):
                raise ValueError(f"invalid {name} in OrderStatus: {raw!r}")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "venue_status", str(self.venue_status or "").upper())

    @property
    def terminal(self) -> bool:
        return self.status in {"closed", "canceled", "expired"}

    @property
    def fully_filled(self) -> bool:
        """The venue confirmed a complete fill, not merely order acceptance."""
        return self.status == "closed"

    @property
    def has_fill(self) -> bool:
        return self.filled_qty > 0

    @property
    def partially_filled(self) -> bool:
        return self.has_fill and not self.fully_filled


@dataclass(frozen=True)
class PairPrecision:
    """Normalized price, volume, and minimum-quantity metadata for a pair."""
    price_decimals: int
    volume_decimals: int
    order_min: float           # Minimum quantity (Kraken ``ordermin``).
    base_asset: str = ""       # Pair base asset, used when adopting an existing position.

    def __post_init__(self):
        for name in ("price_decimals", "volume_decimals"):
            raw = getattr(self, name)
            if isinstance(raw, bool) or int(raw) != raw or int(raw) < 0:
                raise ValueError(f"invalid {name} in PairPrecision: {raw!r}")
            object.__setattr__(self, name, int(raw))
        minimum = float(self.order_min)
        if not math.isfinite(minimum) or minimum < 0:
            raise ValueError(f"invalid order_min in PairPrecision: {self.order_min!r}")
        object.__setattr__(self, "order_min", minimum)
        object.__setattr__(self, "base_asset", str(self.base_asset or "").strip())


_CANDLE_INTERVALS = {
    1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d",
}


def candle_interval(interval_min: int) -> str:
    """Return the canonical venue interval or reject an unsupported horizon."""
    try:
        minutes = int(interval_min)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProviderError(f"invalid candle interval: {interval_min!r}") from exc
    if minutes != interval_min or minutes not in _CANDLE_INTERVALS:
        raise ProviderError(f"unsupported candle interval: {interval_min!r} minutes")
    return _CANDLE_INTERVALS[minutes]


@dataclass(frozen=True)
class OrderReconciliationCapabilities:
    """Venue facts needed by the common order-lifecycle machinery.

    A capability means the adapter exposes a strict, normalized operation. It does
    not promise that the venue is currently reachable. Missing declarations fail
    closed through the conservative all-false default on ``MarketDataProvider``.
    """

    lookup_by_client_order_id: bool = False
    status_by_order_id: bool = False
    cancel_by_order_id: bool = False
    list_open_orders: bool = False

    def __post_init__(self):
        for field_name in (
                "lookup_by_client_order_id", "status_by_order_id",
                "cancel_by_order_id", "list_open_orders"):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} capability must be bool")


def reconciliation_capabilities_of(provider) -> OrderReconciliationCapabilities:
    """Read and validate an adapter's explicit reconciliation declaration."""
    name = str(getattr(provider, "name", type(provider).__name__))
    method = getattr(provider, "reconciliation_capabilities", None)
    if not callable(method):
        raise ProviderError(f"{name}: reconciliation capabilities are undeclared")
    capabilities = method()
    if not isinstance(capabilities, OrderReconciliationCapabilities):
        raise ProviderError(f"{name}: invalid reconciliation capabilities")
    return capabilities


@runtime_checkable
class StrategyExecutor(Protocol):
    """Minimum venue-neutral interface required by the spot-DCA engine."""

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Return the current last/mid price, or ``None`` when unavailable."""
        ...

    def submit_order(self, symbol: str, side: str, qty: float,
                     price: Optional[float] = None, *, market: bool = False,
                     kind: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> str:
        """Submit an order and return its venue ID.

        ``price=None`` or ``market=True`` requests a market order.
        ``client_order_id`` carries the persisted intent when the venue supports it.
        Raise ``ProviderError`` when submission cannot be confirmed.
        """
        ...

    def order_status(self, symbol: str, order_id: str) -> OrderStatus:
        """Return normalized status and cumulative fills, or raise ``ProviderError``."""
        ...

    def cancel_order(self, symbol: str, order_id: str) -> None:
        """Request cancellation by venue ID or raise ``ProviderError``.

        Adapters are not uniformly idempotent for already-terminal or unknown orders;
        callers must reconcile status when cancellation is ambiguous.
        """
        ...

    def pair_precision(self, symbol: str) -> Optional[PairPrecision]:
        """Return pair precision and minimum quantity, or ``None`` if unavailable."""
        ...

    def free_balance(self, asset: str) -> Optional[float]:
        """Return free quantity: ``0.0`` is real zero; ``None`` means unavailable."""
        ...

    def ohlc_closes(self, symbol: str, interval_min: int) -> list[float]:
        """Return completed-bar closes for ``interval_min``; empty means unavailable."""
        ...
