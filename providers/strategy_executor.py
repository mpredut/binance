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
