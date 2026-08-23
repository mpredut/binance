"""Contractul provider-agnostic al motoarelor de strategie urmarite financiar.

Interfata minima este consumata de ``strategies.spot_dca`` si implementata de
providerii Kraken, Hyperliquid, Binance si Trading212. Tipurile explicite tin
normalizarea venue-ului in adaptor si lasa motorul sa decida numai financiar.

NB: metoda de plasare se numeste `submit_order`, NU `place_order` — providerii au deja un
`place_order(symbol, side, price, qty)` pt MarketApi/tradeall (guarded, intoarce dict/None,
gate KRAKEN_LIVE_ORDERS). Motorul de strategie cere alta semantica (raw, order_id, ridica
ProviderError, guvernat de dry_run-ul propriu al strategiei) -> nume distinct, coexista.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


class ProviderError(Exception):
    """Eroare agnostica de venue. Fiecare provider mapeaza eroarea lui nativa
    (KrakenError, erori HL/Binance) in asta, ca strategia sa prinda UN singur tip
    (inlocuieste cele 6 `except KrakenError` din strategy.py)."""


@dataclass(frozen=True)
class OrderStatus:
    """Rezultatul interogarii unui ordin dupa id (inlocuieste dict-ul de la
    Kraken query_orders). `status` normalizat: 'open'|'closed'|'canceled'|'expired'."""
    status: str
    filled_qty: float          # cantitatea executata (vol_exec la Kraken)
    cost: float                # notional executat (pt pretul mediu: cost/filled_qty)
    fee: float                 # comisionul real raportat de venue


@dataclass(frozen=True)
class PairPrecision:
    """Metadatele de precizie/limita ale perechii (inlocuieste pair_info)."""
    price_decimals: int
    volume_decimals: int
    order_min: float           # cantitatea minima (ordermin la Kraken)
    base_asset: str = ""       # activul de baza al perechii (pt adoptia pozitiei existente)


@runtime_checkable
class StrategyExecutor(Protocol):
    """Interfata minima ceruta de motorul spot DCA, agnostica de venue."""

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Pretul curent (last/mid) pt bucla de decizie. None daca indisponibil."""
        ...

    def submit_order(self, symbol: str, side: str, qty: float,
                     price: Optional[float] = None, *, market: bool = False,
                     kind: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> str:
        """Plaseaza un ordin. `price=None` sau `market=True` => ordin de piata.
        Intoarce order_id-ul de venue (folosit apoi la order_status/cancel_order).
        `client_order_id` coreleaza intentia persistata cu ordinul de venue, acolo
        unde API-ul il suporta; providerul il adapteaza formatului specific bursei.
        Ridica ProviderError la esec."""
        ...

    def order_status(self, symbol: str, order_id: str) -> OrderStatus:
        """Starea unui ordin dupa id — pt detectia fill-ului in reconcile().
        Ridica ProviderError daca interogarea esueaza."""
        ...

    def cancel_order(self, symbol: str, order_id: str) -> None:
        """Anuleaza un ordin dupa id. Ridica ProviderError la esec (dar NU daca
        ordinul e deja inchis/inexistent — acela e succes idempotent)."""
        ...

    def pair_precision(self, symbol: str) -> Optional[PairPrecision]:
        """Precizia pret/volum + cantitatea minima. None daca perechea nu e
        (inca) listata — strategia cade pe precizie implicita, ca azi."""
        ...

    def free_balance(self, asset: str) -> Optional[float]:
        """Cantitatea LIBERA: 0.0 = zero real; None = citire indisponibila."""
        ...

    def ohlc_closes(self, symbol: str, interval_min: int) -> list[float]:
        """Inchiderile barelor pe `interval_min` (semnal trend/vol). Exclude bara
        in formare. Lista goala daca datele nu-s disponibile (fara semnal ->
        strategia pur si simplu nu intra in regimul de trend)."""
        ...
