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
import time
import math
import importlib
from typing import Optional, List

from .base import MarketDataProvider, _normalize_order, env_value
from .strategy_executor import OrderStatus, PairPrecision, ProviderError

_T212_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "212trading")
_PRICE_DECIMALS = 2
_VOLUME_DECIMALS = 2
_ORDER_MIN = 0.01

_OPEN_STATUSES = {
    "LOCAL", "UNCONFIRMED", "CONFIRMED", "NEW", "CANCELLING",
    "PARTIALLY_FILLED", "REPLACING",
}
_STATUS_MAP = {
    "FILLED": "closed",
    "CANCELLED": "canceled",
    "CANCELED": "canceled",
    "REPLACED": "canceled",
    "REJECTED": "expired",
    "EXPIRED": "expired",
}


def _live() -> bool:
    return os.environ.get("T212_LIVE_ORDERS", "false").strip().lower() == "true"


class T212Provider(MarketDataProvider):
    def __init__(self, client=None, live_enabled: Optional[bool] = None,
                 order_validity: Optional[str] = None):
        self._cli = client
        # None pastreaza gate-ul istoric T212_LIVE_ORDERS. Launcherul autonom,
        # care are deja STRAT_EXECUTE/dry_run per profil, injecteaza explicit bool.
        self._live_enabled = live_enabled
        # Profilurile autonome au istoric GOOD_TILL_CANCEL; contractul generic
        # poate ramane configurat prin T212_ORDER_VALIDITY.
        self._order_validity = order_validity

    def _orders_live(self) -> bool:
        return _live() if self._live_enabled is None else bool(self._live_enabled)

    @property
    def name(self) -> str:
        return "T212"

    def supports_symbol(self, symbol: str) -> bool:
        return False  # explicit-only

    def _client(self):
        if self._cli is not None:
            return self._cli
        # importlib accepta numele istoric al folderului; evitam mutatia globala
        # sys.path si acelasi modul intra acum si in wheel-ul instalabil.
        T212Client = importlib.import_module("212trading.t212_client").T212Client
        # Cheile din 212trading/.env (secrete gitignored). env-ul flotei are prioritate.
        key = os.environ.get("T212_API_KEY") or env_value(_T212_DIR, "T212_API_KEY")
        if not key:
            raise RuntimeError("Lipseste T212_API_KEY (212trading/.env sau env)")
        secret = os.environ.get("T212_API_SECRET") or env_value(_T212_DIR, "T212_API_SECRET")
        env = os.environ.get("T212_ENV") or env_value(_T212_DIR, "T212_ENV") or "live"
        self._cli = T212Client(key, secret, env)
        return self._cli

    def _position(self, symbol: str, *, strict: bool = False) -> Optional[dict]:
        """Pozitia din portofoliu pt ticker (sau None)."""
        try:
            port = self._client().get_portfolio()
            if port is None:
                raise ProviderError("portofoliul T212 este indisponibil")
            for p in port:
                if str(p.get("ticker", "")) == symbol:
                    return p
            return None
        except Exception as e:  # noqa: BLE001
            if strict:
                if isinstance(e, ProviderError):
                    raise
                raise ProviderError(f"portfolio({symbol}): {e}") from e
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
        # Contract comun: eroarea devine None, niciodata zero legitim.
        try:
            p = self._position(asset, strict=True)
        except ProviderError as e:
            print(f"[T212] balance {asset}: {e}")
            return None
        if not p:
            return 0.0
        try:
            qty = float(p.get("quantity") or 0.0)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(qty) or qty < 0:
            return None
        return qty

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        """Sintetizeaza pozitia ca UN buy la averagePrice (model portofoliu)."""
        want = (side or "").upper()
        if want == "SELL":
            return []                         # nu modelam vanzari istorice
        # Folosit de gardurile financiare: eroarea de cont trebuie sa blocheze
        # ordinul, nu sa semene cu un portofoliu legitim gol.
        p = self._position(symbol, strict=True)
        if not p:
            return []
        try:
            avg = float(p.get("averagePrice"))
            qty = float(p.get("quantity") or 0.0)
        except (TypeError, ValueError) as e:
            raise ProviderError(f"portfolio({symbol}): pozitie invalida") from e
        if not math.isfinite(avg) or not math.isfinite(qty) or avg <= 0 or qty < 0:
            raise ProviderError(f"portfolio({symbol}): pozitie invalida")
        if qty <= 0:
            return []
        return [_normalize_order({
            "side": "BUY", "price": avg, "qty": qty,
            "timestamp": int((time.time() - 2 * 3600) * 1000),  # in fereastra, nu "prea recent"
        })]

    # ── plasare (DRY pana la T212_LIVE_ORDERS=true) ────────────────────────────
    @staticmethod
    def _signed_qty(side: str, qty: float) -> float:
        side_u = (side or "").strip().upper()
        try:
            qty_f = round(abs(float(qty)), _VOLUME_DECIMALS)
        except (TypeError, ValueError) as e:
            raise ProviderError(f"cantitate T212 invalida: {qty!r}") from e
        if not math.isfinite(qty_f) or qty_f < _ORDER_MIN:
            raise ProviderError(f"cantitate T212 invalida: {qty!r}")
        if side_u in {"BUY", "B"}:
            return qty_f
        if side_u in {"SELL", "S"}:
            return -qty_f
        raise ProviderError(f"directie T212 invalida: {side!r}")

    def _send_order(self, symbol: str, side: str, qty: float,
                    price: Optional[float], *, market: bool) -> tuple[int, dict]:
        signed = self._signed_qty(side, qty)
        if market:
            return self._client().place_market_order(symbol, signed, extended_hours=False)
        if price is None:
            raise ProviderError("ordinul T212 LIMIT necesita pret")
        try:
            price_f = float(price)
        except (TypeError, ValueError) as e:
            raise ProviderError(f"pret T212 invalid: {price!r}") from e
        if not math.isfinite(price_f) or price_f <= 0:
            raise ProviderError(f"pret T212 invalid: {price!r}")
        configured = (
            self._order_validity
            if self._order_validity is not None
            else os.environ.get("T212_ORDER_VALIDITY", "DAY")
        )
        validity = str(configured).strip().upper() or "DAY"
        if validity not in {"DAY", "GOOD_TILL_CANCEL"}:
            raise ProviderError(f"T212_ORDER_VALIDITY invalid: {validity!r}")
        return self._client().place_limit_order(symbol, signed, price_f, validity=validity)

    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        if not self._orders_live():
            print(f"[T212][DRY] as plasa {side} {symbol} qty={qty} @ {price} "
                  f"(real off; seteaza T212_LIVE_ORDERS=true)")
            return None
        try:
            print(f"[T212][LIVE] {side} {symbol} qty={qty} @ {price}")
            status, data = self._send_order(
                symbol, side, qty, price, market=bool(kwargs.get("force", False)))
            if status not in (200, 201):
                return None
            # Instrument.place foloseste cheia comuna orderId pentru cooldown. Pastram
            # si `id` nativ T212, dar expunem aliasul mecanic fara a schimba payload-ul.
            if isinstance(data, dict) and data.get("id") is not None and "orderId" not in data:
                data = dict(data)
                data["orderId"] = str(data["id"])
            return data
        except Exception as e:  # noqa: BLE001
            print(f"[T212] place_order {symbol}: {e}")
            return None

    # ── CONTRACT StrategyExecutor ──────────────────────────────────────────────
    def submit_order(self, symbol: str, side: str, qty: float,
                     price: Optional[float] = None, *, market: bool = False,
                     kind: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> str:
        """Plasare stricta pentru motorul generic; nu ocoleste poarta live T212."""
        # API-ul public T212 v0 nu accepta nici tag de strategie, nici client ID;
        # corelarea ramane in ExecutionAudit dupa ID-ul returnat de venue.
        del kind, client_order_id
        if not self._orders_live():
            raise ProviderError("T212_LIVE_ORDERS nu este true; ordinul real este blocat")
        try:
            status, data = self._send_order(
                symbol, side, qty, price, market=bool(market or price is None))
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"submit_order({symbol}): {e}") from e
        if status not in (200, 201):
            raise ProviderError(f"submit_order({symbol}): T212 HTTP {status}: {data}")
        order_id = data.get("id") if isinstance(data, dict) else None
        if order_id is None:
            raise ProviderError(f"submit_order({symbol}): raspuns fara id: {data}")
        return str(order_id)

    def order_status(self, symbol: str, order_id: str) -> OrderStatus:
        try:
            raw = self._client().get_order_status(order_id)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"order_status({order_id}): {e}") from e
        if not isinstance(raw, dict):
            raise ProviderError(f"order_status({order_id}): ordin indisponibil")

        instrument = raw.get("instrument")
        instrument_ticker = instrument.get("ticker") if isinstance(instrument, dict) else ""
        ticker = str(raw.get("ticker") or instrument_ticker or "")
        if ticker and ticker != symbol:
            raise ProviderError(
                f"order_status({order_id}): ticker {ticker!r}, asteptat {symbol!r}")

        venue_status = str(raw.get("status") or "").strip().upper()
        if venue_status in _OPEN_STATUSES:
            normalized = "open"
        else:
            normalized = _STATUS_MAP.get(venue_status)
        if normalized is None:
            raise ProviderError(
                f"order_status({order_id}): status T212 necunoscut {venue_status!r}")

        try:
            filled_qty = abs(float(raw.get("filledQuantity") or 0.0))
            filled_value_raw = raw.get("filledValue")
            cost = abs(float(filled_value_raw or 0.0))
            fee = float(raw.get("fee", raw.get("commission", 0.0)) or 0.0)
        except (TypeError, ValueError) as e:
            raise ProviderError(f"order_status({order_id}): valori de fill invalide") from e

        fee_currencies = {
            str(currency).strip().upper()
            for currency in (raw.get("_feeCurrencies") or [])
            if str(currency).strip()
        }
        order_currency = str(raw.get("currency") or "").strip().upper()
        if fee and order_currency and fee_currencies != {order_currency}:
            raise ProviderError(
                f"order_status({order_id}): fee {sorted(fee_currencies)} "
                f"nu este in moneda ordinului {order_currency}"
            )

        if filled_qty > 0 and cost <= 0:
            # Unele payload-uri vechi expun pretul de executie in loc de filledValue.
            # Nu folosim limitPrice: nu este pretul real si ar falsifica P&L-ul.
            fill_price = raw.get("fillPrice", raw.get("averagePrice"))
            try:
                cost = abs(float(fill_price)) * filled_qty
            except (TypeError, ValueError):
                raise ProviderError(
                    f"order_status({order_id}): fill {filled_qty} fara cost executat"
                ) from None

        return OrderStatus(
            status=normalized,
            filled_qty=filled_qty,
            cost=cost,
            fee=fee,
        )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        if not self._orders_live():
            raise ProviderError("T212_LIVE_ORDERS nu este true; anularea reala este blocata")
        try:
            if self._client().cancel_order(order_id):
                return
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"cancel_order({order_id}): {e}") from e

        # DELETE poate raspunde 404 daca ordinul s-a inchis intre decizie si anulare.
        # Acceptam idempotent doar un status terminal confirmat, nu presupunem succes.
        try:
            terminal = self.order_status(symbol, order_id).status
        except ProviderError as e:
            raise ProviderError(
                f"cancel_order({order_id}): T212 nu a confirmat anularea"
            ) from e
        if terminal not in {"closed", "canceled", "expired"}:
            raise ProviderError(
                f"cancel_order({order_id}): ordinul este inca {terminal}"
            )

    def pair_precision(self, symbol: str) -> Optional[PairPrecision]:
        if not symbol:
            return None
        # Clientul live rotunjeste deja pret/cantitate la doua zecimale. API-ul de
        # metadata nu publica tickSize/minQty; contractul reflecta mecanica reala.
        return PairPrecision(
            price_decimals=_PRICE_DECIMALS,
            volume_decimals=_VOLUME_DECIMALS,
            order_min=_ORDER_MIN,
            base_asset=symbol,
        )

    def min_order_qty(self, symbol: str) -> float:
        return _ORDER_MIN if symbol else 0.0

    def ohlc_closes(self, symbol: str, interval_min: int) -> list[float]:
        # Feed-ul contului T212 nu ofera OHLC. Motorul T212 isi pastreaza feed-ul
        # Yahoo si propria strategie; motorul generic trateaza [] ca semnal indisponibil.
        return []
