# kraken_provider.py
"""Kraken spot adapter backed by ``kraken/kraken_client.py``.

Symbols are native Kraken pairs such as ``HYPEUSD``. ``supports_symbol`` deliberately
returns false, so instruments must select this adapter explicitly and cannot collide
with Hyperliquid's HYPE routing. Public prices work without credentials; account and
execution methods require Kraken keys loaded lazily from the environment or Kraken
configuration.

The legacy ``place_order`` path submits validation-only requests unless
``KRAKEN_LIVE_ORDERS=true``. The StrategyExecutor ``submit_order`` path is intentionally
strict and always sends a real request; its caller's dry-run policy is the controlling
gate. Importing this module does not eagerly initialize the Kraken client.
"""
import json
import os
import sys
import math
import time
from typing import Optional, List

from .base import MarketDataProvider, _normalize_order, env_value
from .strategy_executor import (
    OrderReconciliationCapabilities,
    OrderStatus,
    PairPrecision,
    ProviderError,
)

_KRAKEN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kraken")
# Shared cross-process fill cache produced by kraken_cachemanager.py.
_KRAKEN_CACHE_FILE = os.path.join(os.path.dirname(_KRAKEN_DIR), "cachedb", "cache_trade_kraken.json")
_CACHE_MAX_STALE_S = 30.0   # Beyond this, assume cachemanager stopped and use TradesHistory.


def _kraken_pair_key(value: str) -> str:
    """Canonicalize common Kraken legacy pair wrappers for strict comparison."""
    key = "".join(ch for ch in str(value or "").upper() if ch.isalnum())
    quote = next((candidate for candidate in (
        "USDC", "USDT", "USD", "EUR", "GBP", "CAD", "JPY", "AUD",
        "BTC", "XBT", "ETH",
    ) if key.endswith(candidate) or key.endswith("Z" + candidate)), None)
    if quote is None:
        return key.replace("XBT", "BTC")
    suffix = "Z" + quote if key.endswith("Z" + quote) else quote
    base = key[:-len(suffix)]
    if base.startswith("X") and len(base) > 3:
        base = base[1:]
    return (base + quote).replace("XBT", "BTC")


def _kraken_pair_matches(expected: str, actual: str) -> bool:
    return bool(actual) and _kraken_pair_key(expected) == _kraken_pair_key(actual)


def _live() -> bool:
    # os.environ takes precedence, with kraken/.env as the API-key-style fallback.
    v = os.environ.get("KRAKEN_LIVE_ORDERS")
    if v is None:
        v = env_value(_KRAKEN_DIR, "KRAKEN_LIVE_ORDERS")
    return (v or "false").strip().lower() == "true"


class KrakenProvider(MarketDataProvider):
    def __init__(self, client=None):
        # kraken_bot can inject its _BOT client to share connection and nonce.
        # None lazily builds a fleet client using the _SPARE credentials.
        self._cli = client
        self._minqty = {}  # Cache symbol-to-ordermin from pair_info.

    @property
    def name(self) -> str:
        return "Kraken"

    def reconciliation_capabilities(self) -> OrderReconciliationCapabilities:
        return OrderReconciliationCapabilities(
            lookup_by_client_order_id=True,
            status_by_order_id=True,
            cancel_by_order_id=True,
            list_open_orders=True,
        )

    def supports_symbol(self, symbol: str) -> bool:
        # Explicit-only: claim no pattern and remain reachable only through Instrument.
        return False

    # -- Lazy client. ----------------------------------------------------------
    def _client(self):
        if self._cli is not None:
            return self._cli
        # Put kraken first on sys.path for lazy client imports, then restore the
        # path so other providers are unaffected. The unique kraken_common name
        # avoids collision with hyperliquid/common.py.
        saved_path = list(sys.path)
        try:
            sys.path.insert(0, _KRAKEN_DIR)
            from kraken_client import KrakenClient  # noqa: import lazy
        finally:
            sys.path[:] = saved_path                  # Restore order for other providers.
        # Read Kraken-specific credentials, with environment variables taking
        # precedence. The fleet is a distinct concurrent consumer and prefers
        # _SPARE for its own nonce sequence, then falls back to plain and _BOT keys.
        api_key = (os.environ.get("KRAKEN_API_KEY")
                   or env_value(_KRAKEN_DIR, "KRAKEN_API_KEY")
                   or env_value(_KRAKEN_DIR, "KRAKEN_API_KEY_SPARE")
                   or env_value(_KRAKEN_DIR, "KRAKEN_API_KEY_BOT"))
        api_secret = (os.environ.get("KRAKEN_API_SECRET")
                      or env_value(_KRAKEN_DIR, "KRAKEN_API_SECRET")
                      or env_value(_KRAKEN_DIR, "KRAKEN_API_SECRET_SPARE")
                      or env_value(_KRAKEN_DIR, "KRAKEN_API_SECRET_BOT"))
        cli = KrakenClient(api_key, api_secret)
        # Cache only a credentialed client. When keys are initially absent, retry
        # loading them on the next tick so the process can recover without restart.
        if api_key and api_secret:
            self._cli = cli
        else:
            print("[Kraken] ⚠ PORNIT FARA chei (kraken/.env lipsa/incomplet la primul apel de cont)"
                  " -> citirile de cont (balance/orders) vor esua; NU cachez, reincerc la urmatorul tick.")
        return cli

    # -- Public market data without keys. --------------------------------------
    def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            return self._client().last_price(symbol)
        except Exception as e:  # noqa: BLE001
            print(f"[Kraken] pret {symbol}: {e}")
            return None

    def min_order_qty(self, symbol: str) -> float:
        """Return public pair minimum volume, caching only a positive result.

        Do not cache zero from a failed lookup, because that would permanently
        disable minimum-volume protection and cause repeated dust rejections.
        """
        cached = self._minqty.get(symbol)
        if cached:  # Only positive values are cached; zero or absent retries.
            return cached
        mn = 0.0
        try:
            info = self._client().pair_info(symbol) or {}
            mn = float(info.get("ordermin", 0) or 0.0)
        except Exception as e:  # noqa: BLE001
            print(f"[Kraken] ordermin {symbol}: {e}")
        if mn > 0:
            self._minqty[symbol] = mn   # Cache only a successful lookup.
        return mn

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        """Return ascending public OHLC closes as timestamp/price mappings."""
        try:
            cli = self._client()
            # Choose a minute interval that fits roughly 720 points or fewer.
            interval = max(1, int(math.ceil((lookback_h * 60.0) / 720.0)))
            res = cli._public("OHLC", {"pair": symbol, "interval": interval})
            rows = next((v for k, v in res.items() if k != "last"), None)
            if not rows:
                return None
            cutoff = time.time() - lookback_h * 3600
            out = []
            for r in rows:                       # [time, o, h, l, c, vwap, vol, cnt]
                t = float(r[0])
                if t < cutoff:
                    continue
                out.append({"timestamp": int(t * 1000), "price": float(r[4])})
            return out or None
        except Exception as e:  # noqa: BLE001
            print(f"[Kraken] history {symbol}: {e}")
            return None

    # -- Credentialed account access. -----------------------------------------
    def free_balance(self, asset: str) -> Optional[float]:
        try:
            bal = self._client().balance() or {}
            for key in (asset, "X" + asset, "Z" + asset, asset + ".F"):
                if key in bal:
                    return float(bal[key] or 0.0)
            return 0.0
        except Exception as e:  # noqa: BLE001
            print(f"[Kraken] balance {asset}: {e}")
            return None

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        """Return own pair fills filtered by side and age.

        Prefer the fresh shared cross-process cache for one fetch across processes
        and a common guard view; otherwise fall back safely to TradesHistory.
        """
        try:
            rows = self._fills_from_cache(symbol)
            if rows is None:                                  # Missing or stale cache: direct API.
                rows = self._fills_from_api(symbol)
            cutoff_ms = (time.time() - since_s) * 1000.0
            want = (side or "").upper()
            out = []
            for r in rows:
                if r["timestamp"] < cutoff_ms:
                    continue
                if want and r["side"] != want:
                    continue
                out.append(_normalize_order(r))
            return out
        except Exception as e:  # noqa: BLE001
            print(f"[Kraken] get_orders {symbol}: {e}")
            return []

    def open_orders(self, symbol: str) -> List[dict]:
        """Return a strict Binance-shaped snapshot of active Kraken orders."""
        try:
            raw_orders = self._client().open_orders()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"open_orders({symbol}): {exc}") from exc
        if not isinstance(raw_orders, dict):
            raise ProviderError(
                f"open_orders({symbol}): payload Kraken invalid")

        normalized = []
        for order_id, raw in raw_orders.items():
            if not isinstance(raw, dict):
                raise ProviderError(
                    f"open_orders({symbol}): ordin {order_id} invalid")
            description = raw.get("descr") or {}
            if not isinstance(description, dict):
                raise ProviderError(
                    f"open_orders({symbol}): descriere {order_id} invalida")
            pair = str(description.get("pair") or raw.get("pair") or "")
            if not pair:
                raise ProviderError(
                    f"open_orders({symbol}): ordin {order_id} fara pair")
            if not _kraken_pair_matches(symbol, pair):
                continue
            side = str(description.get("type") or raw.get("type") or "").upper()
            if side not in {"BUY", "SELL"}:
                raise ProviderError(
                    f"open_orders({symbol}): side invalid pentru {order_id}")
            try:
                price = float(description.get("price", raw.get("price", 0.0)) or 0.0)
                original_qty = float(raw.get("vol") or 0.0)
                executed_qty = float(raw.get("vol_exec") or 0.0)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ProviderError(
                    f"open_orders({symbol}): valori invalide pentru {order_id}") from exc
            if (not all(math.isfinite(value) for value in (
                    price, original_qty, executed_qty))
                    or price < 0 or original_qty <= 0 or executed_qty < 0
                    or executed_qty > original_qty):
                raise ProviderError(
                    f"open_orders({symbol}): cantitati/pret invalide pentru {order_id}")
            normalized.append({
                "orderId": str(order_id),
                "clientOrderId": raw.get("cl_ord_id"),
                "side": side,
                "price": price,
                "origQty": original_qty,
                "executedQty": executed_qty,
                "status": str(raw.get("status") or "open").upper(),
            })
        return normalized

    def _fills_from_cache(self, symbol: str):
        """Read symbol fills from the shared cache, or return None for API fallback."""
        try:
            with open(_KRAKEN_CACHE_FILE) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None
        items = data.get("items") or {}
        ft = data.get("fetchtime") or {}
        su = symbol.upper()
        key = next((k for k in items if su in k.upper() or k.upper() in su), None)
        if key is None:
            return None
        if (time.time() * 1000.0 - float(ft.get(key, 0))) > _CACHE_MAX_STALE_S * 1000.0:
            return None                                       # Stale cachemanager data.
        return [{
            "side": "BUY" if t.get("isBuyer") else "SELL",
            "price": t.get("price"), "qty": t.get("qty"),
            "timestamp": int(t.get("time", 0)),
        } for t in items[key]]

    def _fills_from_api(self, symbol: str):
        """Fall back to direct TradesHistory, matching pre-cachemanager behavior."""
        cli = self._client()
        res = cli._private("TradesHistory")
        trades = (res or {}).get("trades", {}) or {}
        su = symbol.upper()
        rows = []
        for tr in trades.values():
            p = str(tr.get("pair", "")).upper()
            if su not in p and p not in su:
                continue
            rows.append({
                "side": "BUY" if str(tr.get("type", "")).lower() == "buy" else "SELL",
                "price": tr.get("price"), "qty": tr.get("vol"),
                "timestamp": int(float(tr.get("time", 0)) * 1000),
            })
        return rows

    # -- Placement remains dry until KRAKEN_LIVE_ORDERS=true. ------------------
    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        live = _live()
        s = (side or "").lower()
        s = "buy" if s.startswith("b") else "sell"
        if not live:
            print(f"[Kraken][DRY] as plasa {side} {symbol} qty={qty} @ {price} "
                  f"(real off; seteaza KRAKEN_LIVE_ORDERS=true)")
            try:                                 # Server-side validation without placement.
                return self._client().add_order(symbol, s, qty, price, ordertype="limit", validate=True)
            except Exception as e:  # noqa: BLE001
                print(f"[Kraken][DRY] validate {symbol}: {e}")
                return None
        try:
            print(f"[Kraken][LIVE] {side} {symbol} qty={qty} @ {price}")
            return self._client().add_order(symbol, s, qty, price, ordertype="limit", validate=False)
        except Exception as e:  # noqa: BLE001
            print(f"[Kraken] place_order {symbol}: {e}")
            return None

    # -- StrategyExecutor contract delegated to kraken_client. -----------------
    # Unlike place_order, this raw method has no KRAKEN_LIVE_ORDERS gate, returns
    # an order id, and raises ProviderError so strategy reconciliation sees failure.
    # The strategy's own dry_run setting governs it.
    def submit_order(self, symbol: str, side: str, qty: float,
                     price: Optional[float] = None, *, market: bool = False,
                     kind: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> str:
        s = "buy" if (side or "").lower().startswith("b") else "sell"
        ordertype = "market" if (market or price is None) else "limit"
        try:
            order_kwargs = {"ordertype": ordertype, "validate": False}
            if client_order_id is not None:
                order_kwargs["cl_ord_id"] = client_order_id
            res = self._client().add_order(
                symbol, s, qty, None if ordertype == "market" else price,
                **order_kwargs) or {}
        except Exception as e:  # noqa: BLE001 — Normalize venue errors.
            raise ProviderError(f"submit_order {symbol} {s}: {e}") from e
        txids = res.get("txid") or []
        if not txids:
            raise ProviderError(f"submit_order {symbol}: raspuns fara txid ({res})")
        return str(txids[0])

    def preflight_order(self, symbol: str, side: str, qty: float,
                        price: Optional[float] = None, *, market: bool = False,
                        kind: Optional[str] = None) -> None:
        """Reject an impossible SELL before creating a durable submit intent.

        Kraken previously received the same TP repeatedly when persisted strategy
        quantity exceeded the account balance. A read-only balance gate stops that
        deterministic venue rejection without guessing a replacement quantity or
        mutating strategy state. BUY funding remains venue-authoritative because its
        required fee/slippage reserve depends on order type and execution.
        """
        if not str(side or "").lower().startswith("s"):
            return
        try:
            requested = float(qty)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ProviderError(
                f"preflight_order {symbol}: cantitate SELL invalida") from exc
        if not math.isfinite(requested) or requested <= 0:
            raise ProviderError(
                f"preflight_order {symbol}: cantitate SELL invalida")

        precision = self.pair_precision(symbol)
        base_asset = precision.base_asset if precision else ""
        if not base_asset:
            raise ProviderError(
                f"preflight_order {symbol}: activul de baza este indisponibil")
        available = self.free_balance(base_asset)
        if available is None or not math.isfinite(float(available)):
            raise ProviderError(
                f"preflight_order {symbol}: balanta {base_asset} indisponibila")
        available = max(0.0, float(available))
        decimals = precision.volume_decimals if precision else 8
        tolerance = 0.5 * (10.0 ** -max(0, decimals))
        if requested > available + tolerance:
            raise ProviderError(
                f"preflight_order {symbol}: insufficient funds SELL "
                f"qty={requested} available={available} {base_asset}"
            )

    def order_status(self, symbol: str, order_id: str) -> OrderStatus:
        try:
            res = self._client().query_orders(order_id) or {}
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"order_status {order_id}: {e}") from e
        info = res.get(order_id)
        if info is None:
            raise ProviderError(f"order_status: ordinul {order_id} nu a fost gasit")
        return OrderStatus(
            status=str(info.get("status", "")),
            filled_qty=float(info.get("vol_exec") or 0.0),
            cost=float(info.get("cost") or 0.0),
            fee=float(info.get("fee") or 0.0),
            venue_status=str(info.get("status") or ""),
        )

    def order_by_client_id(self, symbol: str, client_order_id: str):
        """Recover an open or recently terminal Kraken order by ``cl_ord_id``."""
        wanted = str(client_order_id).lower()
        try:
            groups = (self._client().open_orders(), self._client().closed_orders())
        except Exception as e:  # noqa: BLE001
            raise ProviderError(
                f"order_by_client_id({symbol},{client_order_id}): {e}") from e
        for orders in groups:
            for order_id, raw in dict(orders or {}).items():
                if str(raw.get("cl_ord_id") or "").lower() != wanted:
                    continue
                descr = raw.get("descr") or {}
                pair = str(descr.get("pair") or raw.get("pair") or "")
                if pair and not _kraken_pair_matches(symbol, pair):
                    continue
                return {"orderId": str(order_id), "status": raw.get("status")}
        return None

    def cancel_order_by_id(self, symbol: str, order_id: str) -> None:
        """Cancel by id, treating an already closed or missing order as success.

        The explicit method name avoids future ambiguity; cancel_order below is the
        StrategyExecutor contract alias.
        """
        try:
            result = self._client().cancel_order(order_id) or {}
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "unknown order" in msg or "already" in msg:
                return                       # Idempotent: already closed or canceled.
            raise ProviderError(f"cancel_order {order_id}: {e}") from e
        if "count" in result:
            try:
                if int(result["count"]) < 1:
                    raise ProviderError(
                        f"cancel_order {order_id}: Kraken nu a confirmat anularea"
                    )
            except (TypeError, ValueError) as e:
                raise ProviderError(
                    f"cancel_order {order_id}: raspuns invalid ({result})"
                ) from e

    def cancel_order(self, symbol: str, order_id: str) -> None:  # contract StrategyExecutor
        self.cancel_order_by_id(symbol, order_id)

    def pair_precision(self, symbol: str) -> Optional[PairPrecision]:
        try:
            info = self._client().pair_info(symbol)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"pair_precision {symbol}: {e}") from e
        if not info:
            return None                      # Not listed yet; strategy uses its default.
        try:
            return PairPrecision(
                price_decimals=int(info.get("pair_decimals", 2)),
                volume_decimals=int(info.get("lot_decimals", 8)),
                order_min=float(info.get("ordermin", 0) or 0.0),
                base_asset=str(info.get("base", "")),
            )
        except (TypeError, ValueError) as e:
            raise ProviderError(f"pair_precision {symbol}: info malformat ({e})") from e

    def ohlc_closes(self, symbol: str, interval_min: int) -> list:
        try:
            return self._client().ohlc_closes(symbol, interval_min)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"ohlc_closes {symbol}: {e}") from e
