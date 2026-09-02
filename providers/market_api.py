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
from .binance_filters import BinanceFilterError, BinanceOrderRules, decimal_places
from .strategy_executor import (
    OrderReconciliationCapabilities,
    OrderStatus,
    PairPrecision,
    ProviderError,
    candle_interval,
    extract_order_id,
    reconciliation_capabilities_of,
)
from market_regime import (
    CompositeMarketRegimeDecision,
    MarketRegimeDecision,
    MarketRegimeService,
)


# Binance imports reach WebSockets and local keys. Kraken and Hyperliquid must
# remain importable in a clean checkout without Binance secrets or side effects.
# Keep these variables patchable in tests.
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
    """Return significant decimals in a Binance stepSize/tickSize."""
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

    def reconciliation_capabilities(self) -> OrderReconciliationCapabilities:
        return OrderReconciliationCapabilities(
            lookup_by_client_order_id=True,
            status_by_order_id=True,
            cancel_by_order_id=True,
            list_open_orders=True,
        )

    def get_current_price(self, symbol: str) -> Optional[float]:
        return _get_bapi().get_current_price(symbol)

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        return None

    def supports_symbol(self, symbol: str) -> bool:
        # The operational Binance account uses USDC pairs exclusively, preserving
        # the facade's Binance default. Hyperliquid owns HYPE spot symbols, so
        # exclude them before Binance's broad USDC claim can capture HYPEUSDC.
        if symbol.upper().startswith("HYPE"):
            return False
        return symbol.endswith("USDC")

    # -- Account data, repackaged through the facade without semantic changes. --
    def free_balance(self, asset: str) -> Optional[float]:
        # Delegate to Binance's direct per-asset balance query. It returns ``0.0``
        # for an absent asset and ``None`` when the API read fails.
        return _get_bapi().get_free_balance(asset)

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        # Preserve get_trade_orders side and age filtering, then normalize shape.
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
            # Binance -2013 means missing order; all other errors remain fail closed.
            if getattr(exc, "code", None) == -2013:
                return None
            raise ProviderError(
                f"order_by_client_id({symbol},{client_order_id}): {exc}") from exc

    def place_order(self, symbol: str, side: str, price: float, qty: float, force: bool = False, **kwargs):
        # Mechanics only: fee/balance, minimum notional, and dispatch. Instrument.place
        # applies daily, profit, quantity, trend, cooldown, and logging policies via
        # provider-neutral hooks. Only force affects market versus limit here.
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
        # Use the Order-cache safeback window as tier-one reference. When the caller
        # omits it, use the rich 14-day default rather than a weaker fill fallback.
        import order_guard
        from binance_api import bapi_placeorder as _po
        sb = safeback_sec if safeback_sec else _po.PLACE_ORDER_SAFEBACK_SEC
        return order_guard.window_reference(self, symbol, side, sb)

    def last_opposite_fill(self, symbol: str, order_type: str, since_s: float = 0) -> Optional[float]:
        # Persistent Binance source without a window: fill cache followed by direct
        # get_my_trades. This matches legacy guard tiers two and three. Lazy import
        # avoids a bapi_placeorder/cacheManager/market_api cycle.
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
        # False because Binance now uses Instrument.place's shared policy pipeline.
        # Provider hooks preserve its richer mechanics, while mechanics-only
        # place_order prevents duplicated guards.
        return False

    # -- StrategyExecutor contract. --------------------------------------------
    # get_current_price and free_balance already satisfy it.
    def pair_precision(self, symbol: str):
        try:
            info = _get_bapi().client.get_symbol_info(symbol)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"pair_precision({symbol}): {e}") from e
        try:
            rules = BinanceOrderRules.from_symbol_info(info)
        except BinanceFilterError as exc:
            raise ProviderError(f"pair_precision({symbol}): {exc}") from exc
        return PairPrecision(
            price_decimals=decimal_places(rules.tick_size),
            volume_decimals=decimal_places(rules.lot_step),
            order_min=float(rules.lot_min), base_asset=rules.base_asset,
        )

    def ohlc_closes(self, symbol: str, interval_min: int) -> list:
        iv = candle_interval(interval_min)
        try:
            kl = _get_bapi().client.get_klines(symbol=symbol, interval=iv, limit=91)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"ohlc_closes({symbol}): {e}") from e
        closes = [float(k[4]) for k in (kl or [])]     # k[4] is the close.
        return closes[:-1] if closes else []           # Exclude the forming bar.

    def submit_order(self, symbol: str, side: str, qty: float,
                     price: Optional[float] = None, *, market: bool = False,
                     kind: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> str:
        order_type = "BUY" if (side or "").lower().startswith("b") else "SELL"
        try:
            client = _get_bapi().client
            rules = BinanceOrderRules.from_symbol_info(client.get_symbol_info(symbol))
            reference_price = _get_bapi().get_current_price(symbol)
            normalized_qty, normalized_price = rules.normalize(
                quantity=qty, price=price, market=bool(market or price is None),
                reference_price=reference_price,
            )
            qty = float(normalized_qty)
            price = None if normalized_price is None else float(normalized_price)
            if market or price is None:
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
        oid = extract_order_id(res)
        if oid is None:
            raise ProviderError(f"submit_order({symbol}): a response without an orderId ({res})")
        return oid

    def _order_fee_quote(self, symbol: str, order_id: int) -> float:
        """Return cumulative commission converted into the pair's quote currency.

        Binance omits fees from get_order. Without this query the generic engine
        would systematically overestimate P&L and diverge from live results.
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
                        f"order_status({order_id}): cannot convert the fee {asset}->{quote}"
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
        venue_status = str(o.get("status") or "").upper()
        return OrderStatus(
            status=st_map.get(o.get("status"), "open"),
            filled_qty=executed_qty,
            cost=float(o.get("cummulativeQuoteQty") or 0.0),
            fee=fee,
            venue_status=venue_status,
        )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        try:
            canceled = _get_bapi().cancel_order(symbol, int(order_id))
            if not canceled:
                raise ProviderError(
                    f"cancel_order({order_id}): the venue did not confirm the cancellation"
                )
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"cancel_order({order_id}): {e}") from e


class MarketApi:
    """Route each symbol to the first provider that claims it.

    Fall back to the first configured provider, Binance, and memoize routes. The
    get_current_price signature matches bapi, making this a drop-in data facade.
    """

    def __init__(self, providers: List[MarketDataProvider]):
        if not providers:
            raise ValueError("MarketApi: the provider list cannot be empty")
        self._providers: List[MarketDataProvider] = list(providers)
        self._route: dict = {}   # Lock-free, idempotent symbol-to-provider memoization.
        # Name registry enables explicit venue routing for Instrument rather than
        # guessing via supports_symbol. It does not alter symbol routing below.
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
        # Behavior-preserving default: first provider (Binance).
        default = self._providers[0]
        self._route[symbol] = default
        return default

    def get_current_price(self, symbol: str) -> Optional[float]:
        return self._provider_for(symbol).get_current_price(symbol)

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        return self._provider_for(symbol).get_price_history(symbol, lookback_h)

    # -- Account routing by symbol/asset, normalized by provider. ---------------
    def free_balance(self, asset: str) -> Optional[float]:
        """Preserve legacy compatibility; new code should use free_balance_for.

        A bare asset is ambiguous across venues. Historical first-provider routing
        remains only to avoid breaking old external integrations.
        """
        return self._provider_for(asset).free_balance(asset)

    def free_balance_for(self, provider_name: str, asset: str) -> Optional[float]:
        """Read free balance explicitly from the requested unambiguous venue."""
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

    @staticmethod
    def _reconciliation_capabilities(provider) -> OrderReconciliationCapabilities:
        return reconciliation_capabilities_of(provider)

    def reconciliation_capabilities(
            self, symbol: str, *, provider_name=None
    ) -> OrderReconciliationCapabilities:
        provider = self._provider_explicit_or_routed(symbol, provider_name)
        return self._reconciliation_capabilities(provider)

    def open_orders(self, symbol: str, *, provider_name=None) -> List[dict]:
        provider = self._provider_explicit_or_routed(symbol, provider_name)
        capabilities = self._reconciliation_capabilities(provider)
        if not capabilities.list_open_orders:
            raise ProviderError(f"{provider.name}: open_orders is unsupported")
        return provider.open_orders(symbol)

    def order_by_client_id(self, symbol: str, client_order_id: str, *,
                           provider_name=None):
        """Return a native order found by deterministic client ID, or ``None``.

        This narrow recovery hook lets persistent callers reconcile an ambiguous
        submit without guessing from balances or submitting a duplicate.
        """
        provider = self._provider_explicit_or_routed(symbol, provider_name)
        capabilities = self._reconciliation_capabilities(provider)
        if not capabilities.lookup_by_client_order_id:
            raise ProviderError(
                f"{provider.name}: order_by_client_id is unsupported")
        method = getattr(provider, "order_by_client_id", None)
        if not callable(method):
            raise ProviderError(
                f"{provider.name}: declared order_by_client_id is missing")
        return method(symbol, str(client_order_id))

    def order_status(self, symbol: str, order_id: str, *,
                     provider_name=None) -> OrderStatus:
        """Return venue-neutral status; lookup failures remain fail-closed."""
        provider = self._provider_explicit_or_routed(symbol, provider_name)
        capabilities = self._reconciliation_capabilities(provider)
        if not capabilities.status_by_order_id:
            raise ProviderError(f"{provider.name}: order_status is unsupported")
        method = getattr(provider, "order_status", None)
        if not callable(method):
            raise ProviderError(f"{provider.name}: declared order_status is missing")
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
        capabilities = self._reconciliation_capabilities(provider)
        if not capabilities.cancel_by_order_id:
            raise ProviderError(f"{provider.name}: cancel_order is unsupported")
        method = getattr(provider, "cancel_order", None)
        if not callable(method):
            raise ProviderError(f"{provider.name}: declared cancel_order is missing")
        method(symbol, str(order_id))

    def tracked_order_lifecycle(self, *, provider_name: str, venue=None,
                                missing_confirmations=2,
                                retry_on_lookup_error=False,
                                max_age_seconds=None, audit=None,
                                clock=time.time):
        """Build the persistent lifecycle companion for synchronous ``place``.

        Persistence remains strategy-owned and is supplied to each lifecycle call;
        this facade supplies provider-neutral lookup/status/cancel routing.
        """
        from order_retry import TrackedOrderLifecycle
        return TrackedOrderLifecycle(
            self,
            provider_name=provider_name,
            venue=venue,
            missing_confirmations=missing_confirmations,
            retry_on_lookup_error=retry_on_lookup_error,
            max_age_seconds=max_age_seconds,
            audit=audit,
            clock=clock,
        )

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
        # Mechanics-only provider dispatch without guards. Real placement must use
        # guarded .place(); this remains for internal and dry-run cases.
        return self._provider_for(symbol).place_order(symbol, side, price, qty, **kwargs)

    def place(self, symbol: str, side: str, price: float, qty: float,
              base: Optional[str] = None, quote: Optional[str] = None, **kwargs):
        """Place through the single guarded proxy using a temporary Instrument.

        Run the complete provider-agnostic policy pipeline with provider hooks.
        This replaces direct legacy smart/safe placement calls. Derive base and
        quote from the symbol when absent. Lazy Instrument import avoids a cycle.
        """
        from instrument import Instrument
        import utils as u
        prov_name = self.provider_name_for(symbol)
        if base is None:
            try:
                base = u.base_asset(symbol)
            except Exception:
                base = None
        if quote is None and base and symbol.startswith(base) and symbol != base:
            quote = symbol[len(base):]   # For example, BTCUSDC gives BTC and USDC.
        inst = Instrument(name=symbol, symbol=symbol, provider=prov_name,
                          base=base, quote=quote, api=self)
        return inst.place(side, price, qty, **kwargs)

    def supports_symbol(self, symbol: str) -> bool:
        return any(p.supports_symbol(symbol) for p in self._providers)

    def provider_name_for(self, symbol: str) -> str:
        """Return the provider that would serve the symbol for logs and debugging."""
        return self._provider_for(symbol).name

    def provider_by_name(self, name: str) -> Optional[MarketDataProvider]:
        """Return the case-insensitive named provider, or None when absent.

        Instrument uses this for explicit venue routing so identical assets on
        multiple venues are not inferred from the symbol string.
        """
        return self._by_name.get((name or "").strip().lower())

    @property
    def providers(self) -> List[MarketDataProvider]:
        return list(self._providers)


# Singleton injected when constructors receive api=None. Binance remains first to
# preserve routing for unclaimed symbols. Hyperliquid claims only HYPE and Binance
# explicitly excludes it. Provider construction is cheap and SDKs load lazily;
# import failure falls back cleanly without affecting the fleet.
_extra_providers = []
# Isolate every provider import so one missing dependency leaves the others intact.
# Kraken and T212 are explicit-only and reachable through Instrument, so their
# ordering does not affect symbol routing.
for _modname, _clsname in (("hyperliquid_provider", "HyperliquidProvider"),
                           ("kraken_provider", "KrakenProvider"),
                           ("t212_provider", "T212Provider")):
    try:
        _mod = __import__("providers." + _modname, fromlist=[_clsname])
        _extra_providers.append(getattr(_mod, _clsname)())
    except Exception as _e:  # noqa: BLE001
        print(f"market_api: {_clsname} indisponibil ({_e})")

api = MarketApi([BinanceProvider()] + _extra_providers)
