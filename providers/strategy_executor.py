"""CONTRACT provider-agnostic pt motorul de strategie (Calea B, Faza 0).

Defineste interfata MINIMA de care are nevoie `kraken/strategy.py` (base v2:
DCA+TP+trailing) ca sa ruleze pe ORICE venue, nu doar Kraken. Azi strategia e
cuplata de `KrakenClient` prin 6 metode; aici le abstractizam intr-un `Protocol`
+ tipuri de retur explicite. `MarketDataProvider` (providers/market_api.py) va fi
extins in Faza 1 ca sa satisfaca acest contract, iar `strategy.py` va fi rewire-uit
in Faza 2 sa-l ceara (in loc de KrakenClient).

Contractul e verificabil static (typing.Protocol) fara sa forteze deocamdata vreun
provider existent sa se schimbe — Faza 0 nu cabla nimic, doar fixeaza tinta.

Harta fata de KrakenClient (metoda veche -> metoda din contract):
  add_order    -> place_order        (intoarce order_id; market cand price=None)
  query_orders -> order_status       (LIPSA azi in providers/: gol de umplut per venue)
  cancel_order -> cancel_order       (LIPSA azi in providers/: gol de umplut per venue)
  pair_info    -> pair_precision     (partial: min_order_qty exista; precizia lipseste)
  balance      -> free_balance       (exista la toti providerii)
  ohlc_closes  -> ohlc_closes        (mapabil pe get_price_history, atentie la cadenta)
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


@runtime_checkable
class StrategyExecutor(Protocol):
    """Interfata MINIMA ceruta de kraken/strategy.py. Orice obiect care o
    implementeaza (kraken_provider, hyperliquid_provider, binance, replay_provider)
    poate rula base v2. Semnaturile sunt agnostice de venue."""

    def place_order(self, symbol: str, side: str, qty: float,
                    price: Optional[float] = None, *, market: bool = False,
                    kind: Optional[str] = None) -> str:
        """Plaseaza un ordin. `price=None` sau `market=True` => ordin de piata.
        Intoarce order_id-ul de venue (folosit apoi la order_status/cancel_order).
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

    def free_balance(self, asset: str) -> float:
        """Cantitatea LIBERA din activ (pt adoptia pozitiei existente)."""
        ...

    def ohlc_closes(self, symbol: str, interval_min: int) -> list[float]:
        """Inchiderile barelor pe `interval_min` (semnal trend/vol). Exclude bara
        in formare. Lista goala daca datele nu-s disponibile (fara semnal ->
        strategia pur si simplu nu intra in regimul de trend)."""
        ...
