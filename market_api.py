# market_api.py
"""Facada market-data (pret + history) — Faza 2a a decuplarii de Binance.

SCOP: trademonitorul devine GENERIC (sa ruleze in special pe HYPE/Hyperliquid).
Aici e DOAR fundatia, BEHAVIOR-PRESERVING pentru Binance: facada ruteaza pe symbol
catre providerul potrivit, iar default-ul (cand nimeni nu revendica symbolul) e
primul provider = Binance. Astfel symbolurile Binance ajung exact la bapi ca azi.

DELIMITARE: facada acopera DOAR market-data (pret curent + istoric granular).
Tranzactiile/ordinele si stream-ul WS de cont RAMAN Binance-specifice, in AFARA
facadei (raman pe binance_api.bapi direct).

CAPCANA import circular: `pricefetcher` importa `cacheManager`, iar `cacheManager`
importa `market_api`. Deci market_api NU are voie sa importe pricefetcher/cacheManager
(ar inchide ciclul). BinanceProvider wrapeaza DIRECT `binance_api.bapi` — bapi nu
importa cacheManager la nivel de modul (doar lazy, in functii), deci e sigur.
"""
from abc import ABC, abstractmethod
from typing import List, Optional

from binance_api import bapi as _bapi


class MarketDataProvider(ABC):
    """Interfata unui provider de market-data (o singura platforma: Binance, HYPE...)."""

    @abstractmethod
    def get_current_price(self, symbol: str) -> Optional[float]:
        """Pretul curent al symbolului, sau None daca nu e disponibil."""
        ...

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        """Istoric granular de preturi pe ultimele `lookback_h` ore.
        Default None: nu toate platformele au history granular (Binance nu, prin
        acest API). Hyperliquid il va implementa in Faza 2b (backfill ferestre trend)."""
        return None

    @abstractmethod
    def supports_symbol(self, symbol: str) -> bool:
        """True daca providerul poate servi symbolul (pentru rutare)."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


class BinanceProvider(MarketDataProvider):
    """Wrapeaza binance_api.bapi pentru market-data. Default-ul flotei azi.

    get_current_price -> bapi.get_current_price (acelasi comportament ca pana acum).
    get_price_history  -> None (Binance n-are history granular prin acest API).
    """

    @property
    def name(self) -> str:
        return "Binance"

    def get_current_price(self, symbol: str) -> Optional[float]:
        return _bapi.get_current_price(symbol)

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        return None

    def supports_symbol(self, symbol: str) -> bool:
        # Perechile spot pe care le foloseste flota (USDC/USDT). Restul cad oricum
        # pe default = Binance in facada, deci ramane behavior-preserving.
        return symbol.endswith("USDC") or symbol.endswith("USDT")


class MarketApi:
    """Facada cu rutare pe symbol. Pentru un symbol, alege PRIMUL provider cu
    supports_symbol(symbol)==True; daca niciunul nu-l revendica, foloseste default-ul
    (primul provider din lista = Binance). Memoizeaza ruta symbol->provider.

    Semnatura get_current_price(symbol) e identica cu cea a bapi pentru market-data,
    deci e drop-in pentru codul existent (doar sursa lui `api` se schimba)."""

    def __init__(self, providers: List[MarketDataProvider]):
        if not providers:
            raise ValueError("MarketApi: lista de provideri nu poate fi goala")
        self._providers: List[MarketDataProvider] = list(providers)
        self._route: dict = {}   # symbol -> provider (memoizare lock-free, idempotenta)

    def _provider_for(self, symbol: str) -> MarketDataProvider:
        provider = self._route.get(symbol)
        if provider is not None:
            return provider
        for candidate in self._providers:
            try:
                if candidate.supports_symbol(symbol):
                    self._route[symbol] = candidate
                    return candidate
            except Exception:
                continue
        # Default behavior-preserving: primul provider (Binance).
        default = self._providers[0]
        self._route[symbol] = default
        return default

    def get_current_price(self, symbol: str) -> Optional[float]:
        return self._provider_for(symbol).get_current_price(symbol)

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        return self._provider_for(symbol).get_price_history(symbol, lookback_h)

    def supports_symbol(self, symbol: str) -> bool:
        return any(p.supports_symbol(symbol) for p in self._providers)

    def provider_name_for(self, symbol: str) -> str:
        """Numele providerului care ar servi symbolul (util pt debug/loguri)."""
        return self._provider_for(symbol).name

    @property
    def providers(self) -> List[MarketDataProvider]:
        return list(self._providers)


# Singleton injectat in constructori (api=None -> acest singleton).
# Faza 2a: doar Binance. Faza 2b adauga HyperliquidProvider() in lista.
api = MarketApi([BinanceProvider()])
