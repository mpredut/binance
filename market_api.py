# market_api.py
"""Facada market-data (pret + history) — Faza 2a a decuplarii de Binance.

SCOP: trademonitorul devine GENERIC (sa ruleze in special pe HYPE/Hyperliquid).
Aici e DOAR fundatia, BEHAVIOR-PRESERVING pentru Binance: facada ruteaza pe symbol
catre providerul potrivit, iar default-ul (cand nimeni nu revendica symbolul) e
primul provider = Binance. Astfel symbolurile Binance ajung exact la bapi ca azi.

DELIMITARE: facada acopera market-data (pret curent + istoric granular) si — din
Faza 3 — CITIREA starii de cont (sold liber + istoric ordine/tranzactii), NORMALIZATA
la o forma comuna. Din Faza 2b acopera si PLASAREA de ordine (place_order), rutata pe
symbol: BinanceProvider -> bapi_placeorder.place_order_smart (IDENTIC ca azi), iar
HyperliquidProvider -> spot HL (DRY implicit). RAMANE in afara facadei stream-ul WS de cont.

CAPCANA import circular: `pricefetcher` importa `cacheManager`, iar `cacheManager`
importa `market_api`. Deci market_api NU are voie sa importe pricefetcher/cacheManager
(ar inchide ciclul). BinanceProvider wrapeaza DIRECT `binance_api.bapi` si
`binance_api.bapi_allorders` — ambele importa cacheManager doar LAZY (in functii), nu
la nivel de modul, deci e sigur (nu se inchide ciclul prin market_api).
"""
from abc import ABC, abstractmethod
from typing import List, Optional

from binance_api import bapi as _bapi
from binance_api import bapi_allorders as _allorders


def _normalize_order(o: dict) -> dict:
    """Traduce un ordin/tranzactie din formatul NATIV al providerului in FORMA COMUNA:
    {side, price, qty, timestamp}. `timestamp` ramane in MS (ca nativul Binance), pentru
    ca get_position_stats / get_relevant_trade il consuma asa (sort + /1000). Asa toata
    aritmetica (price*qty, ultimul buy) merge IDENTIC pe orice provider."""
    return {
        "side": (o.get("side") or "").upper(),
        "price": float(o.get("price", 0.0) or 0.0),
        "qty": float(o.get("qty", o.get("quantity", 0.0)) or 0.0),
        "timestamp": o.get("timestamp"),
    }


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

    # ── CONT (Faza 3): citire stare cont, NORMALIZATA la forma comuna. ──────────
    # Default None/[]: un provider pur market-data (fara cont) le poate lasa asa.
    # Forma comuna:
    #   balanta liber = float
    #   order/trade   = {"side","price","qty","timestamp"}  (timestamp in ms)
    def free_balance(self, asset: str) -> Optional[float]:
        """Soldul LIBER (disponibil) al unui asset (ex. 'TAO'). None daca providerul
        n-are notiune de cont. Sursa de adevar pt 'vinde tot ce ai disponibil'."""
        return None

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        """Ordinele pt `symbol` din ultimele `since_s` secunde, optional filtrate pe
        `side` ('BUY'/'SELL'; None = ambele), NORMALIZATE la forma comuna. Default []."""
        return []

    def get_trades(self, symbol: str, since_s: float) -> List[dict]:
        """Toate tranzactiile (orice side) pt `symbol` din ultimele `since_s` secunde,
        normalizate. Default = get_orders fara filtru de side."""
        return self.get_orders(symbol, None, since_s)

    def open_orders(self, symbol: str) -> List[dict]:
        """Ordinele DESCHISE (neexecutate) pt `symbol`. Optional; default []."""
        return []

    # ── PLASARE ordine (Faza 2b): rutata pe symbol. Default no-op (provider pur
    #    market-data). `**kwargs` cara parametrii specifici Binance (safeback_seconds,
    #    force, cancelorders, hours, pair) fara sa-i impuna celorlalti provideri.
    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        """Plaseaza un ordin pt `symbol`. Default None (provider fara plasare)."""
        return None


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
        # EXCEPTIE: HYPE* e servit de HyperliquidProvider (HL spot), nu de Binance —
        # altfel claim-ul lacom pe *USDC ar fura HYPEUSDC inaintea providerului HL.
        if symbol.upper().startswith("HYPE"):
            return False
        return symbol.endswith("USDC") or symbol.endswith("USDT")

    # ── CONT: aceleasi date ca azi, doar reimpachetate prin facada. ─────────────
    def free_balance(self, asset: str) -> Optional[float]:
        # Mirror EXACT al buclei din monitortrades.get_available_qty (si trade_watch):
        # parcurge soldurile, intoarce 'free' pt asset, altfel 0.0. get_account_assets_
        # balances are deja try/except (intoarce [] la eroare) -> nu arunca aici.
        for bal in (_bapi.get_account_assets_balances() or []):
            if bal.get("asset") == asset:
                return float(bal.get("free", 0.0) or 0.0)
        return 0.0

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        # bapi_allorders.get_trade_orders(order_type, symbol, max_age_seconds) — aceeasi
        # filtrare pe side+varsta ca pana acum; doar normalizam la forma comuna.
        raw = _allorders.get_trade_orders(side, symbol, since_s) or []
        return [_normalize_order(o) for o in raw]

    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        # IDENTIC ca azi: deleaga la po.place_order_smart cu ACELEASI kwargs pe care
        # monitortrades le pasa (safeback_seconds, force, cancelorders, hours, pair).
        # Import LAZY: bapi_placeorder trage priceAnalysis->cacheManager->market_api,
        # deci un import la nivel de modul ar inchide ciclul. Aici e sigur (runtime).
        from binance_api import bapi_placeorder as _po
        return _po.place_order_smart(side, symbol, price, qty, **kwargs)


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
        # Registry pe NUME (ex. 'binance', 'hyperliquid'): rutare EXPLICITA pe venue
        # pt descriptorul Instrument, in loc de ghicitul prin supports_symbol. Aditiv —
        # nu schimba rutarea pe symbol de mai jos.
        self._by_name: dict = {p.name.lower(): p for p in self._providers}

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

    # ── CONT (Faza 3): rutare pe symbol/asset, normalizat de provider. ─────────
    def free_balance(self, asset: str) -> Optional[float]:
        # `asset` (ex. 'TAO') nu e un symbol, deci nu va revendica niciun provider via
        # supports_symbol -> cade pe default = Binance. Behavior-preserving azi; cand
        # apare HYPE, providerul lui isi va revendica asset-urile proprii.
        return self._provider_for(asset).free_balance(asset)

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        return self._provider_for(symbol).get_orders(symbol, side, since_s)

    def get_trades(self, symbol: str, since_s: float) -> List[dict]:
        return self._provider_for(symbol).get_trades(symbol, since_s)

    def open_orders(self, symbol: str) -> List[dict]:
        return self._provider_for(symbol).open_orders(symbol)

    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        # Rutare pe symbol: HYPE -> HyperliquidProvider (spot HL, DRY implicit);
        # restul -> BinanceProvider (po.place_order_smart, IDENTIC ca azi).
        return self._provider_for(symbol).place_order(symbol, side, price, qty, **kwargs)

    def supports_symbol(self, symbol: str) -> bool:
        return any(p.supports_symbol(symbol) for p in self._providers)

    def provider_name_for(self, symbol: str) -> str:
        """Numele providerului care ar servi symbolul (util pt debug/loguri)."""
        return self._provider_for(symbol).name

    def provider_by_name(self, name: str) -> Optional[MarketDataProvider]:
        """Providerul inregistrat sub `name` (case-insensitive, ex. 'binance',
        'hyperliquid'); None daca nu exista. Rutare EXPLICITA pe venue, folosita de
        descriptorul Instrument: instrumentul isi declara providerul, nu-l mai ghicim
        din string-ul de symbol (necesar cand acelasi activ e pe mai multe venue-uri)."""
        return self._by_name.get((name or "").strip().lower())

    @property
    def providers(self) -> List[MarketDataProvider]:
        return list(self._providers)


# Singleton injectat in constructori (api=None -> acest singleton).
# ORDINE PROVIDERI: Binance ramane PRIMUL = default behavior-preserving pt symbolurile
# nerevendicate (asset-uri bare BTC/TAO etc.). HyperliquidProvider revendica DOAR HYPE
# (supports_symbol), iar Binance exclude explicit HYPE -> HYPEUSDC ajunge la HL.
# Constructia HyperliquidProvider() e ieftina (NU atinge SDK-ul); SDK-ul se incarca
# lenes la prima folosire. Daca pana si importul modulului ar esua (n-ar trebui),
# cadem curat pe Binance-only, ca flota sa nu fie afectata.
try:
    from hyperliquid_provider import HyperliquidProvider
    _extra_providers = [HyperliquidProvider()]
except Exception as _e:  # noqa: BLE001
    print(f"market_api: HyperliquidProvider indisponibil ({_e}) — doar Binance")
    _extra_providers = []

api = MarketApi([BinanceProvider()] + _extra_providers)
