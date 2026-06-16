# instrument.py
"""Descriptor de INSTRUMENT urmarit: encapsuleaza (provider + symbol + params).

SCOP: un singur lucru tranzactionat (BTC pe Binance, HYPE pe HL, TSLAx pe Kraken,
TSLA pe T212...) devine UN obiect care ASCUNDE providerul. Consumatorii (monitortrades,
tradeall, rtrade) itereaza o lista de Instrument si apeleaza operatii GENERICE —
`price()`, `position`/`orders()`, `free()`, `place()` — fara sa stie/sa-i pese ce
platforma e dedesubt. Asa acelasi activ poate trai pe mai multe venue-uri (doua
instrumente, doua providere) si algoritmul ramane unul singur, generic.

Rutare EXPLICITA pe venue: instrumentul isi declara providerul dupa NUME
(`provider="hyperliquid"`), rezolvat din registry-ul facadei (market_api.provider_by_name),
in loc de ghicitul prin supports_symbol pe string-ul de symbol.

NB: `free()` interogheaza soldul pe ASSET (base, ex. 'HYPE'), nu pe symbol, si merge
DIRECT la providerul instrumentului (nu prin rutarea pe symbol a facadei) — deci e
neambiguu chiar daca acelasi asset apare pe mai multe venue-uri.
"""
from typing import Optional, List, Callable, Any

from market_api import api as _default_api


class Instrument:
    """Provider + symbol + params, cu operatii generice care delegheaza la provider.

    params: dict plat cu chei pe NAMESPACE de consumator, ex. {'mt.gain': '9.2',
    'tradeall.budget': '...'}. Citeste-le tipat cu `param(consumer, key, default, cast)`.
    """

    def __init__(self, name: str, symbol: str, provider: str,
                 base: Optional[str] = None, quote: Optional[str] = None,
                 enabled: bool = True, isolation: str = "own_ledger",
                 market_hours: str = "24x7",
                 params: Optional[dict] = None, api=None):
        self.name = name
        self.symbol = symbol
        self.provider_name = provider
        self.base = base
        self.quote = quote
        self.enabled = enabled
        self.isolation = isolation          # 'dedicated' | 'own_ledger' (vezi designul)
        self.market_hours = market_hours    # '24x7' | 'rth' | ...
        self.params = dict(params or {})
        self._api = api or _default_api
        self._provider = self._api.provider_by_name(provider)
        if self._provider is None:
            raise ValueError(
                f"Instrument {name!r} ({symbol}): provider necunoscut {provider!r}. "
                f"Inregistrat in market_api?")

    # ── identitate / acces provider ────────────────────────────────────────────
    @property
    def provider(self):
        return self._provider

    @property
    def provider_label(self) -> str:
        return self._provider.name

    # ── market-data (delegat la provider, pe symbolul instrumentului) ──────────
    def price(self) -> Optional[float]:
        return self._provider.get_current_price(self.symbol)

    def history(self, lookback_h: float) -> Optional[List]:
        return self._provider.get_price_history(self.symbol, lookback_h)

    # ── cont (sold liber pe ASSET; ordine/tranzactii pe symbol) ────────────────
    def free(self) -> Optional[float]:
        return self._provider.free_balance(self.base or self.symbol)

    def orders(self, side: Optional[str], since_s: float) -> List[dict]:
        return self._provider.get_orders(self.symbol, side, since_s)

    def trades(self, since_s: float) -> List[dict]:
        return self._provider.get_trades(self.symbol, since_s)

    def open_orders(self) -> List[dict]:
        return self._provider.open_orders(self.symbol)

    # ── plasare ordin (DRY/real dupa portile providerului) ─────────────────────
    def place(self, side: str, price: float, qty: float, **kwargs):
        return self._provider.place_order(self.symbol, side, price, qty, **kwargs)

    # ── params namespaced (mt.* / tradeall.* / rtrade.*) ───────────────────────
    def param(self, consumer: str, key: str, default: Any = None,
              cast: Optional[Callable] = None) -> Any:
        """Valoarea `consumer.key` (ex. param('mt','gain', cast=float)). default daca
        lipseste sau cast esueaza."""
        v = self.params.get(f"{consumer}.{key}")
        if v is None:
            return default
        if cast is None:
            return v
        try:
            return cast(v)
        except (ValueError, TypeError):
            return default

    def __repr__(self) -> str:
        st = "on" if self.enabled else "off"
        return f"<Instrument {self.name} {self.symbol}@{self.provider_name} {st}>"
