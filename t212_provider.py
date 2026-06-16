# t212_provider.py
"""T212Provider — stocks REALE pe Trading 212, peste 212trading/t212_client.py.

T212 are model de PORTOFOLIU (pozitie cu averagePrice/quantity/currentPrice), nu istoric
de ordine ca Binance/Kraken. Adaptam: pozitia detinuta = UN buy sintetic la averagePrice
-> monitortrades calculeaza la fel avg_buy + ultimul-buy si vinde pe castig.

EXPLICIT-ONLY: supports_symbol -> False (reachable doar prin Instrument provider="t212").
`symbol` = tickerul T212 (ex 'TSLA_US_EQ' sau cum apare in portofoliu). free_balance pe
acelasi ticker. Ore: actiuni reale = doar RTH (instrumentul are market_hours=rth; bucla
poate sari cand piata e inchisa — currentPrice oricum lipseste atunci).

Cheie: T212_API_KEY (+ optional T212_API_SECRET, T212_ENV=live|demo). Plasare: DRY pana
la T212_LIVE_ORDERS=true. Import LAZY (sys.path pe 212trading/).
"""
import os
import sys
import time
from typing import Optional, List

from market_api import MarketDataProvider, _normalize_order

_T212_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "212trading")


def _live() -> bool:
    return os.environ.get("T212_LIVE_ORDERS", "false").strip().lower() == "true"


class T212Provider(MarketDataProvider):
    def __init__(self):
        self._cli = None

    @property
    def name(self) -> str:
        return "T212"

    def supports_symbol(self, symbol: str) -> bool:
        return False  # explicit-only

    def _client(self):
        if self._cli is not None:
            return self._cli
        if _T212_DIR not in sys.path:
            sys.path.insert(0, _T212_DIR)
        from t212_client import T212Client  # noqa: import lazy
        key = os.environ.get("T212_API_KEY")
        if not key:
            raise RuntimeError("Lipseste T212_API_KEY (env)")
        self._cli = T212Client(key, os.environ.get("T212_API_SECRET"),
                               os.environ.get("T212_ENV", "live"))
        return self._cli

    def _position(self, symbol: str) -> Optional[dict]:
        """Pozitia din portofoliu pt ticker (sau None)."""
        try:
            port = self._client().get_portfolio() or []
            for p in port:
                if str(p.get("ticker", "")) == symbol:
                    return p
            return None
        except Exception as e:  # noqa: BLE001
            print(f"[T212] portfolio {symbol}: {e}")
            return None

    # ── market-data ────────────────────────────────────────────────────────────
    def get_current_price(self, symbol: str) -> Optional[float]:
        p = self._position(symbol)
        if not p:
            return None
        try:
            return float(p.get("currentPrice"))
        except (TypeError, ValueError):
            return None

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        return None  # T212: fara istoric granular prin acest client

    # ── cont ───────────────────────────────────────────────────────────────────
    def free_balance(self, asset: str) -> Optional[float]:
        p = self._position(asset)
        if not p:
            return 0.0
        try:
            return float(p.get("quantity") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        """Sintetizeaza pozitia ca UN buy la averagePrice (model portofoliu)."""
        want = (side or "").upper()
        if want == "SELL":
            return []                         # nu modelam vanzari istorice
        p = self._position(symbol)
        if not p:
            return []
        try:
            avg = float(p.get("averagePrice"))
            qty = float(p.get("quantity") or 0.0)
        except (TypeError, ValueError):
            return []
        if qty <= 0:
            return []
        return [_normalize_order({
            "side": "BUY", "price": avg, "qty": qty,
            "timestamp": int((time.time() - 2 * 3600) * 1000),  # in fereastra, nu "prea recent"
        })]

    # ── plasare (DRY pana la T212_LIVE_ORDERS=true) ────────────────────────────
    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        signed = qty if (side or "").upper().startswith("B") else -qty
        if not _live():
            print(f"[T212][DRY] as plasa {side} {symbol} qty={qty} @ {price} "
                  f"(real off; seteaza T212_LIVE_ORDERS=true)")
            return None
        try:
            print(f"[T212][LIVE] {side} {symbol} qty={qty} @ {price}")
            status, data = self._client().place_limit_order(symbol, signed, price, validity="DAY")
            return data if status in (200, 201) else None
        except Exception as e:  # noqa: BLE001
            print(f"[T212] place_order {symbol}: {e}")
            return None
