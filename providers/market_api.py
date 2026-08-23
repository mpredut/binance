# market_api.py
"""Multi-venue facade for market data, account reads, and order mechanics.

Symbol routing selects the first provider that claims a symbol and falls back to
the first configured provider, Binance. ``Instrument`` uses the name registry for
explicit venue routing when a symbol alone is ambiguous.

``place_order`` dispatches directly to adapter mechanics and live-order gates.
``place`` constructs an ``Instrument`` and runs the shared policy pipeline before
those mechanics. Account WebSocket streams remain outside this facade.

Imports of Binance modules are lazy because ``cacheManager`` imports this module;
eagerly importing modules that lead back to ``cacheManager`` would create a cycle.
"""
import math
import time
from typing import List, Optional

from .base import MarketDataProvider, _normalize_order, env_value
from .strategy_executor import OrderStatus, PairPrecision, ProviderError
from market_regime import (
    CompositeMarketRegimeDecision,
    MarketRegimeDecision,
    MarketRegimeService,
)


# Importurile Binance ajung pana la websocket-uri si chei locale. Providerii
# Kraken/HL trebuie sa poata fi importati intr-un checkout curat, fara secrete
# Binance si fara efecte secundare. Variabilele raman patch-uibile in teste.
_bapi = None
_allorders = None


def _get_bapi():
    global _bapi
    if _bapi is None:
        from binance_api import bapi
        _bapi = bapi
    return _bapi


def _get_allorders():
    global _allorders
    if _allorders is None:
        from binance_api import bapi_allorders
        _allorders = bapi_allorders
    return _allorders


def _step_decimals(step: str) -> int:
    """Nr de zecimale semnificative dintr-un stepSize/tickSize Binance (ex '0.00100000'->3)."""
    s = str(step).rstrip("0")
    return len(s.split(".")[1]) if "." in s and s.split(".", 1)[1] else 0


class BinanceProvider(MarketDataProvider):
    """Adapt Binance market data, account reads, and execution mechanics.

    ``get_price_history`` is intentionally unavailable through this adapter; strict
    strategy execution obtains completed OHLC closes through ``ohlc_closes``.
    """

    @property
    def name(self) -> str:
        return "Binance"

    def get_current_price(self, symbol: str) -> Optional[float]:
        return _get_bapi().get_current_price(symbol)

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        return None

    def supports_symbol(self, symbol: str) -> bool:
        # Contul Binance operational foloseste exclusiv perechi USDC.
        # pe default = Binance in facada, deci ramane behavior-preserving.
        # EXCEPTIE: HYPE* e servit de HyperliquidProvider (HL spot), nu de Binance —
        # altfel claim-ul lacom pe *USDC ar fura HYPEUSDC inaintea providerului HL.
        if symbol.upper().startswith("HYPE"):
            return False
        return symbol.endswith("USDC")

    # ── CONT: aceleasi date ca azi, doar reimpachetate prin facada. ─────────────
    def free_balance(self, asset: str) -> Optional[float]:
        # Delegate to Binance's direct per-asset balance query. It returns ``0.0``
        # for an absent asset and ``None`` when the API read fails.
        return _get_bapi().get_free_balance(asset)

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        # bapi_allorders.get_trade_orders(order_type, symbol, max_age_seconds) — aceeasi
        # filtrare pe side+varsta ca pana acum; doar normalizam la forma comuna.
        raw = _get_allorders().get_trade_orders(side, symbol, since_s) or []
        return [_normalize_order(o) for o in raw]

    def open_orders(self, symbol: str) -> List[dict]:
        try:
            raw = _get_bapi().client.get_open_orders(symbol=symbol) or []
        except Exception as exc:
            raise ProviderError(f"open_orders({symbol}): {exc}") from exc
        return [{
            "orderId": str(order.get("orderId")),
            "clientOrderId": order.get("clientOrderId"),
            "side": str(order.get("side") or "").upper(),
            "price": float(order.get("price") or 0.0),
            "origQty": float(order.get("origQty") or 0.0),
            "executedQty": float(order.get("executedQty") or 0.0),
            "status": str(order.get("status") or "NEW"),
        } for order in raw]

    def order_by_client_id(self, symbol: str, client_order_id: str):
        try:
            return _get_bapi().client.get_order(
                symbol=symbol, origClientOrderId=client_order_id)
        except Exception as exc:
            # Binance -2013 = ordin inexistent; celelalte erori raman fail-closed.
            if getattr(exc, "code", None) == -2013:
                return None
            raise ProviderError(
                f"order_by_client_id({symbol},{client_order_id}): {exc}") from exc

    def place_order(self, symbol: str, side: str, price: float, qty: float, force: bool = False, **kwargs):
        # 30 iul: MECANICA-ONLY (fee/balanta + min-notional + dispatch). Protectia
        # (plafon zilnic, gard profit, cantitate, trend-wait, cooldown, jurnal) e rulata
        # de Instrument.place() ca strat AGNOSTIC, prin hook-uri provider-neutral.
        # guards_internally()=False, deci Binance trece prin acelasi pipeline.
        # kwargs (safeback_seconds/cancelorders/hours/pair/motivation) sunt consumati
        # de stratul agnostic; aici conteaza doar force (market vs limit).
        from binance_api import bapi_placeorder as _po
        mechanics_kwargs = {"force": force}
        if kwargs.get("client_order_id") is not None:
            mechanics_kwargs["client_order_id"] = kwargs["client_order_id"]
        return _po.place_order_mechanics(
            side, symbol, price, qty, **mechanics_kwargs)

    def adjust_order_price(self, symbol: str, side: str, price: float, cancel_opposite: bool = True) -> float:
        from binance_api import bapi_placeorder as _po
        return _po.adjust_price_and_cancel_opposite(side, symbol, price, cancel_opposite=cancel_opposite)

    def profit_guard_window_ref(self, symbol: str, side: str, safeback_sec):
        # Referinta tier-1 din fereastra Order-cache pe safeback (12-14 zile), ca in
        # vechiul if_place_safe_order. Daca apelantul n-a dat safeback -> defaultul
        # bogat (14 zile), nu None (ca sa nu cada pe last_opposite_fill mai slab).
        import order_guard
        from binance_api import bapi_placeorder as _po
        sb = safeback_sec if safeback_sec else _po.PLACE_ORDER_SAFEBACK_SEC
        return order_guard.window_reference(self, symbol, side, sb)

    def last_opposite_fill(self, symbol: str, order_type: str, since_s: float = 0) -> Optional[float]:
        # Sursa dedicata Binance (PERSISTENT, fara fereastra): cache de fills (CacheTradeManager)
        # apoi API direct (get_my_trades). IDENTIC cu tier 2+3 din gardul vechi. Import LAZY
        # (bapi_placeorder trage cacheManager->market_api -> ciclu daca ar fi la nivel de modul).
        from binance_api import bapi_placeorder as _po
        ref = _po._last_opposite_fill_price(symbol, order_type)        # cache fills (via WS)
        if ref is None:
            ref = _po._last_opposite_fill_price_api(symbol, order_type)  # fallback API direct
        return ref

    def policy_cap_quantity(self, symbol: str, side: str, price: float,
                            qty: float, available_qty: float, **kwargs) -> float:
        from binance_api import bapi_placeorder as _po
        return _po.apply_weight_limit(
            symbol, side, price, qty, available_qty)

    def fee_cap_quantity(self, symbol: str, side: str, price: float,
                         available_qty: float) -> float:
        from binance_api import bapi_placeorder as _po
        from providers.quantity import fee_cap_quantity
        return fee_cap_quantity(available_qty, _po.PLACE_ORDER_FEE_PCT)

    def guards_internally(self) -> bool:
        # 30 iul: FALSE — Binance trece acum prin pipeline-ul AGNOSTIC din
        # Instrument.place() (plafon zilnic, gard profit, QuantityDecision,
        # trend-wait, cooldown, jurnal), cu hook-urile care-i pastreaza mecanica
        # bogata (adjust_order_price, profit_guard_window_ref, policy cap). place_order
        # e acum mecanica-only, deci NU se dubleaza gardul. (Era True cat timp Binance
        # rula lantul propriu place_order_smart -> if_place_safe_order.)
        return False

    # ── CONTRACT StrategyExecutor (Faza 4) ─────────────────────────────────────
    # get_current_price / free_balance de mai sus satisfac deja contractul.
    def pair_precision(self, symbol: str):
        try:
            info = _get_bapi().client.get_symbol_info(symbol)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"pair_precision({symbol}): {e}") from e
        if not info:
            return None
        price_dec = vol_dec = 0
        omin = 0.0
        for f in info.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                price_dec = _step_decimals(f.get("tickSize", "0"))
            elif f.get("filterType") == "LOT_SIZE":
                vol_dec = _step_decimals(f.get("stepSize", "0"))
                omin = float(f.get("minQty", 0) or 0.0)
        return PairPrecision(price_decimals=price_dec, volume_decimals=vol_dec,
                             order_min=omin, base_asset=str(info.get("baseAsset", "")))

    def ohlc_closes(self, symbol: str, interval_min: int) -> list:
        iv = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}.get(int(interval_min), "1h")
        try:
            kl = _get_bapi().client.get_klines(symbol=symbol, interval=iv, limit=91)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"ohlc_closes({symbol}): {e}") from e
        closes = [float(k[4]) for k in (kl or [])]     # k[4] = close
        return closes[:-1] if closes else []           # exclude bara in formare

    def submit_order(self, symbol: str, side: str, qty: float,
                     price: Optional[float] = None, *, market: bool = False,
                     kind: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> str:
        order_type = "BUY" if (side or "").lower().startswith("b") else "SELL"
        try:
            if market or price is None:
                client = _get_bapi().client
                fn = (client.order_market_buy if order_type == "BUY"
                      else client.order_market_sell)
                kwargs = {"symbol": symbol, "quantity": qty}
                if client_order_id is not None:
                    kwargs["newClientOrderId"] = client_order_id
                res = fn(**kwargs)
            else:
                from binance_api import bapi_placeorder as _po
                kwargs = {"force": False}
                if client_order_id is not None:
                    kwargs["client_order_id"] = client_order_id
                res = _po.place_order_mechanics(order_type, symbol, price, qty, **kwargs)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"submit_order({symbol}): {e}") from e
        oid = (res or {}).get("orderId")
        if oid is None:
            raise ProviderError(f"submit_order({symbol}): raspuns fara orderId ({res})")
        return str(oid)

    def _order_fee_quote(self, symbol: str, order_id: int) -> float:
        """Comision cumulativ convertit in valuta de cotare a perechii.

        Binance nu include fee-ul in get_order. Fara aceasta interogare motorul
        generic ar supraestima sistematic P&L-ul si ar diferi de live.
        """
        client = _get_bapi().client
        info = client.get_symbol_info(symbol) or {}
        base = str(info.get("baseAsset") or "")
        quote = str(info.get("quoteAsset") or "")
        trades = client.get_my_trades(symbol=symbol, orderId=order_id) or []
        total = 0.0
        for trade in trades:
            if int(trade.get("orderId", -1)) != order_id:
                continue
            commission = float(trade.get("commission") or 0.0)
            asset = str(trade.get("commissionAsset") or quote)
            price = float(trade.get("price") or 0.0)
            if asset == quote:
                total += commission
            elif asset == base:
                total += commission * price
            else:
                conversion = _get_bapi().get_current_price(f"{asset}{quote}")
                if not conversion:
                    raise ProviderError(
                        f"order_status({order_id}): nu pot converti fee {asset}->{quote}"
                    )
                total += commission * float(conversion)
        return total

    def order_status(self, symbol: str, order_id: str):
        try:
            oid = int(order_id)
            o = _get_bapi().client.get_order(symbol=symbol, orderId=oid)
            executed_qty = float(o.get("executedQty") or 0.0)
            fee = self._order_fee_quote(symbol, oid) if executed_qty > 0 else 0.0
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"order_status({order_id}): {e}") from e
        st_map = {"FILLED": "closed", "CANCELED": "canceled", "EXPIRED": "expired",
                  "REJECTED": "canceled", "NEW": "open", "PARTIALLY_FILLED": "open"}
        return OrderStatus(
            status=st_map.get(o.get("status"), "open"),
            filled_qty=executed_qty,
            cost=float(o.get("cummulativeQuoteQty") or 0.0),
            fee=fee,
        )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        try:
            canceled = _get_bapi().cancel_order(symbol, int(order_id))
            if not canceled:
                raise ProviderError(
                    f"cancel_order({order_id}): venue-ul nu a confirmat anularea"
                )
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"cancel_order({order_id}): {e}") from e


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
        self._regime_service = MarketRegimeService()

    def _provider_explicit_or_routed(self, symbol: str, provider_name=None):
        if provider_name is None:
            return self._provider_for(symbol)
        provider = self.provider_by_name(provider_name)
        if provider is None:
            raise ValueError(f"Provider necunoscut: {provider_name!r}")
        return provider

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
        """Compatibilitate legacy; pentru cod nou folosește free_balance_for().

        Un asset simplu nu identifică venue-ul când USDC/HYPE există pe mai multe
        platforme. Rutarea istorică pe primul provider rămâne doar ca să nu rupă
        integrări externe vechi.
        """
        return self._provider_for(asset).free_balance(asset)

    def free_balance_for(self, provider_name: str, asset: str) -> Optional[float]:
        """Sold liber citit explicit de la venue-ul cerut, fără rutare ambiguă."""
        provider = self.provider_by_name(provider_name)
        if provider is None:
            raise ValueError(f"Provider necunoscut: {provider_name!r}")
        return provider.free_balance(asset)

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        return self._provider_for(symbol).get_orders(symbol, side, since_s)

    def get_trades(self, symbol: str, since_s: float, *,
                   provider_name=None) -> List[dict]:
        provider = self._provider_explicit_or_routed(symbol, provider_name)
        return provider.get_trades(symbol, since_s)

    def latest_fill_price(self, symbol: str, side: str, since_s: float, *,
                          provider_name=None, min_notional=None,
                          max_notional=None) -> Optional[float]:
        """Return the newest normalized fill without a Binance-specific API."""
        side = str(side).upper()
        if side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        since_s = float(since_s)
        if not math.isfinite(since_s) or since_s <= 0:
            raise ValueError("since_s must be finite and positive")
        min_value = None if min_notional is None else float(min_notional)
        max_value = None if max_notional is None else float(max_notional)
        if min_value is not None and (not math.isfinite(min_value) or min_value < 0):
            raise ValueError("min_notional must be finite and non-negative")
        if max_value is not None and (not math.isfinite(max_value) or max_value < 0):
            raise ValueError("max_notional must be finite and non-negative")
        if min_value is not None and max_value is not None and min_value > max_value:
            raise ValueError("min_notional cannot exceed max_notional")
        now_ms = time.time() * 1000.0
        cutoff_ms = now_ms - since_s * 1000.0
        candidates = []
        for trade in self.get_trades(
                symbol, since_s, provider_name=provider_name) or []:
            if str(trade.get("side") or "").upper() != side:
                continue
            try:
                timestamp = float(trade.get("timestamp") or 0.0)
                if 0 < timestamp < 10_000_000_000:  # secunde -> milisecunde
                    timestamp *= 1000.0
                price = float(trade.get("price") or 0.0)
                qty = float(trade.get("qty", trade.get("quantity", 0.0)) or 0.0)
            except (TypeError, ValueError, OverflowError):
                continue
            if (not all(math.isfinite(value) for value in (timestamp, price, qty)) or
                    timestamp < cutoff_ms or timestamp > now_ms + 60_000 or
                    price <= 0 or qty <= 0):
                continue
            notional = price * qty
            if not math.isfinite(notional):
                continue
            if min_value is not None and notional < min_value:
                continue
            if max_value is not None and notional > max_value:
                continue
            candidates.append((timestamp, price))
        return max(candidates)[1] if candidates else None

    def open_orders(self, symbol: str) -> List[dict]:
        return self._provider_for(symbol).open_orders(symbol)

    def order_status(self, symbol: str, order_id: str, *,
                     provider_name=None) -> OrderStatus:
        """Return venue-neutral status; lookup failures remain fail-closed."""
        provider = self._provider_explicit_or_routed(symbol, provider_name)
        method = getattr(provider, "order_status", None)
        if not callable(method):
            raise ProviderError(f"{provider.name}: order_status is unsupported")
        try:
            status = method(symbol, str(order_id))
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(
                f"{provider.name}: invalid order status for {order_id}: {exc}"
            ) from exc
        if not isinstance(status, OrderStatus):
            raise ProviderError(
                f"{provider.name}: order_status returned {type(status).__name__}")
        return status

    def cancel_order(self, symbol: str, order_id: str, *, provider_name=None) -> None:
        """Cancel through the provider-neutral adapter contract."""
        provider = self._provider_explicit_or_routed(symbol, provider_name)
        method = getattr(provider, "cancel_order", None)
        if not callable(method):
            raise ProviderError(f"{provider.name}: cancel_order is unsupported")
        method(symbol, str(order_id))

    def market_regime(self, symbol: str, *, provider_name=None, horizon="short",
                      interval_min=None, window_seconds=None, snapshot=None,
                      strength_threshold=None,
                      allow_fallback=True) -> MarketRegimeDecision:
        """Return a common regime with explicit horizon and source fallback."""
        service = self._regime_service
        if strength_threshold is not None:
            service = MarketRegimeService(
                strength_threshold, cache_ttl_sec=service.cache_ttl_sec,
                cache_max=service.cache_max)
        provider = self._provider_explicit_or_routed(symbol, provider_name)
        return service.resolve(
            provider, symbol, horizon=horizon, snapshot=snapshot,
            interval_min=interval_min, window_seconds=window_seconds,
            allow_fallback=allow_fallback)

    def composite_market_regime(self, symbol: str, *, benchmarks=(),
                                provider_name=None, use_case="balanced", weights=None,
                                strength_threshold=None,
                                allow_fallback=True) -> CompositeMarketRegimeDecision:
        """Blend asset horizons with explicitly configured crypto benchmarks."""
        service = self._regime_service
        if strength_threshold is not None:
            service = MarketRegimeService(
                strength_threshold, cache_ttl_sec=service.cache_ttl_sec,
                cache_max=service.cache_max)
        asset_short = self.market_regime(
            symbol, provider_name=provider_name, horizon="short",
            strength_threshold=strength_threshold, allow_fallback=allow_fallback)
        asset_long = self.market_regime(
            symbol, provider_name=provider_name, horizon="long",
            strength_threshold=strength_threshold, allow_fallback=allow_fallback)
        context = []
        for benchmark in tuple(benchmarks or ()):
            benchmark = str(benchmark)
            context.append((
                benchmark,
                self.market_regime(
                    benchmark, provider_name=provider_name, horizon="short",
                    strength_threshold=strength_threshold,
                    allow_fallback=allow_fallback),
                self.market_regime(
                    benchmark, provider_name=provider_name, horizon="long",
                    strength_threshold=strength_threshold,
                    allow_fallback=allow_fallback),
            ))
        return service.compose(
            asset_short, asset_long, context, use_case=use_case, weights=weights)

    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        # MECANICA-ONLY (dispatch la provider, FARA garduri) — NU folosi direct pt
        # plasare reala; foloseste .place() (guardat). Ramas pt cazuri interne/DRY.
        return self._provider_for(symbol).place_order(symbol, side, price, qty, **kwargs)

    def place(self, symbol: str, side: str, price: float, qty: float,
              base: Optional[str] = None, quote: Optional[str] = None, **kwargs):
        """Plasare GUARDATA prin proxy-ul unic (30 iul): construieste un Instrument
        efemer rutat pe symbol si ruleaza pipeline-ul agnostic complet (plafon zilnic,
        gard profit, weight, trend-wait, cooldown, jurnal), cu hook-urile provider-ului
        (Binance isi pastreaza mecanica bogata). Inlocuitorul unic pt vechile apeluri
        directe po.place_order_smart/place_safe_order. `base`/`quote` derivate din symbol
        daca nu-s date (side-aware pt weight/balanta). Import LAZY al Instrument (evita
        ciclul market_api<->instrument la nivel de modul)."""
        from instrument import Instrument
        import utils as u
        prov_name = self.provider_name_for(symbol)
        if base is None:
            try:
                base = u.base_asset(symbol)
            except Exception:
                base = None
        if quote is None and base and symbol.startswith(base) and symbol != base:
            quote = symbol[len(base):]   # ex. BTCUSDC -> base=BTC -> quote=USDC
        inst = Instrument(name=symbol, symbol=symbol, provider=prov_name,
                          base=base, quote=quote, api=self)
        return inst.place(side, price, qty, **kwargs)

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
_extra_providers = []
# Fiecare provider in propriul try/except: import LAZY al SDK-urilor/clientilor, deci
# constructia e ieftina; daca unul lipseste, ceilalti raman. Kraken/T212 sunt
# EXPLICIT-ONLY (supports_symbol=False) -> NU schimba rutarea pe symbol; reachable doar
# prin Instrument (provider_by_name). Deci ordinea lor aici nu afecteaza behavior-ul.
for _modname, _clsname in (("hyperliquid_provider", "HyperliquidProvider"),
                           ("kraken_provider", "KrakenProvider"),
                           ("t212_provider", "T212Provider")):
    try:
        _mod = __import__("providers." + _modname, fromlist=[_clsname])
        _extra_providers.append(getattr(_mod, _clsname)())
    except Exception as _e:  # noqa: BLE001
        print(f"market_api: {_clsname} indisponibil ({_e})")

api = MarketApi([BinanceProvider()] + _extra_providers)
