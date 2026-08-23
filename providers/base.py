"""Primitive comune providerilor, fara registry sau importuri de venue.

Separarea acestui modul tine adaptoarele importabile independent si elimina ciclul
provider -> market_api -> registry -> provider.
"""

import os
from abc import ABC, abstractmethod
from typing import List, Optional


def env_value(folder: str, key: str) -> Optional[str]:
    """Citeste o singura cheie din .env/config.env fara sa modifice os.environ."""
    for fname in (".env", "config.env"):
        path = os.path.join(folder, fname)
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as file:
                for line in file:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    name, _, value = line.partition("=")
                    if name.strip() == key:
                        return value.strip().strip('"').strip("'") or None
        except OSError:
            continue
    return None


def normalize_order(order: dict) -> dict:
    """Normalizeaza un ordin nativ la {side, price, qty, timestamp}."""
    return {
        "side": (order.get("side") or "").upper(),
        "price": float(order.get("price", 0.0) or 0.0),
        "qty": float(order.get("qty", order.get("quantity", 0.0)) or 0.0),
        "timestamp": order.get("timestamp"),
    }


# Alias temporar pentru importurile interne/externe existente.
_normalize_order = normalize_order


class MarketDataProvider(ABC):
    """Interfata comuna de market-data, cont, ordine si guard hooks."""

    @abstractmethod
    def get_current_price(self, symbol: str) -> Optional[float]:
        ...

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        return None

    @abstractmethod
    def supports_symbol(self, symbol: str) -> bool:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    def free_balance(self, asset: str) -> Optional[float]:
        """Sold liber: 0.0 = sold real zero; None = citire indisponibila/eronata."""
        return None

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        return []

    def get_trades(self, symbol: str, since_s: float) -> List[dict]:
        return self.get_orders(symbol, None, since_s)

    def open_orders(self, symbol: str) -> List[dict]:
        return []

    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        return None

    def guards_internally(self) -> bool:
        return False

    def min_order_qty(self, symbol: str) -> float:
        return 0.0

    def policy_cap_quantity(self, symbol: str, side: str, price: float,
                            qty: float, available_qty: float, **kwargs) -> float:
        import order_guard
        return order_guard.weight_limit(
            self, symbol, side, price, qty,
            available_qty=available_qty)

    def fee_cap_quantity(self, symbol: str, side: str, price: float,
                         available_qty: float) -> float:
        return available_qty

    def quantity_decision(self, symbol: str, side: str, price: float,
                          qty: float, **kwargs):
        from providers.quantity import decide_quantity
        return decide_quantity(self, symbol, side, price, qty, **kwargs)

    def adjust_order_price(self, symbol: str, side: str, price: float,
                           cancel_opposite: bool = True) -> float:
        return price

    def profit_guard_window_ref(self, symbol: str, side: str, safeback_sec):
        import order_guard
        return order_guard.window_reference(self, symbol, side,
                                            order_guard.window_for(self.name))

    def last_opposite_fill(self, symbol: str, order_type: str,
                           since_s: float = 90 * 24 * 3600) -> Optional[float]:
        opposite = "SELL" if order_type.upper() == "BUY" else "BUY"
        fills = self.get_orders(symbol, opposite, since_s) or []
        if not fills:
            return None
        latest = max(fills, key=lambda order: order.get("timestamp") or 0)
        price = float(latest.get("price") or 0)
        return price or None
