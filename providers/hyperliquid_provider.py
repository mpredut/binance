# hyperliquid_provider.py
"""HyperliquidProvider — implementarea `MarketDataProvider` (facada market_api) pentru
HYPE pe Hyperliquid SPOT. Faza 2b/3 a decuplarii de Binance.

DELIMITARE STRICTA: acest provider citeste/scrie DOAR SPOT-ul HYPE (perechea @index
TOKEN/USDC). NU atinge perp-ul si NU atinge botul DELTA-NEUTRAL (dn_bot/delta_neutral):
- get_current_price / get_price_history -> pretul SPOT (perechea @index), public, fara cheie.
- free_balance(HYPE/USDC) -> soldul SPOT HL (total - hold), NU perp/margine.
- get_orders / get_trades -> DOAR fill-urile SPOT (coin == perechea @index); fill-urile
  perp (coin == 'HYPE') sunt EXCLUSE, deci activitatea DN nu se amesteca aici.

⚠ ATENTIE (co-mingling spot): pe Hyperliquid soldul SPOT e UNUL singur pe wallet. Daca
botul delta-neutral tine un picior LONG spot in HYPE, acel HYPE apare in acelasi sold.
=> Pentru CITIRE (dry-run) e doar o observatie. Pentru ORDINE REALE de SELL, vinderea
'a tot ce e disponibil' ar putea desface piciorul spot al DN-ului. De aceea place_order
e DRY implicit (vezi mai jos) si ordinele reale raman poarta finala, separata.

IMPORT LAZY OBLIGATORIU: fleet-ul importa market_api (deci si acest modul) la pornire.
SDK-ul Hyperliquid (`hyperliquid`, `eth_account`) poate LIPSI din venv-ul flotei (myenv
pe server). De aceea modulul ASTA nu importa NIMIC din SDK la nivel de modul: clientul HL
(hl_client din hyperliquid/) e creat LENES, in try/except, la prima folosire. Daca SDK-ul
sau cheile lipsesc, metodele degradeaza curat (None/[]) -> Binance ramane neafectat.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from typing import List, Optional

from .base import MarketDataProvider, _normalize_order
from .strategy_executor import OrderStatus, PairPrecision, ProviderError

# Radacina repo-ului + dir-ul hyperliquid/ (pt importurile bare `common`, `hl_client`).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # providers/ -> radacina
_HL_DIR = os.path.join(_REPO_ROOT, "hyperliquid")

# Comuta plasarea de ordine REALE pe HL. Implicit DRY (doar logheaza intentia).
# Poarta finala dupa ce dry-run-ul confirma SI dupa rezolvarea co-mingling-ului DN.
_LIVE_ENV = "HL_LIVE_ORDERS"


def _hype_symbol(symbol: str) -> bool:
    """True pentru variantele symbolului HYPE pe care le serveste acest provider:
    'HYPE', 'HYPEUSDC', 'HYPE/USDC', sau perechea @index rezolvata (ex '@107')."""
    if not symbol:
        return False
    s = symbol.upper()
    return s == "HYPE" or s.startswith("HYPE")


class HyperliquidProvider(MarketDataProvider):
    """Provider SPOT HYPE peste hl_client (SDK Hyperliquid). Vezi nota din modul:
    constructorul NU atinge SDK-ul; totul e lazy + defensiv."""

    #: tokenul SPOT servit (din HL_SPOT_TOKEN/HL_COIN, default HYPE).
    def __init__(self, token: str = "HYPE"):
        self._token = (token or "HYPE").upper()
        self._lock = threading.Lock()
        self._client = None          # HLClient read-only (lazy)
        self._client_tried = False   # ca sa nu reincercam la nesfarsit daca SDK lipseste
        self._spot_pair: Optional[str] = None  # ex '@107' (memoizat)
        self._env_loaded = False

    @property
    def name(self) -> str:
        return "Hyperliquid"

    def supports_symbol(self, symbol: str) -> bool:
        # Revendica DOAR HYPE. Perechile Binance (BTCUSDC/TAOUSDC) si asset-urile bare
        # (BTC/TAO/USDC) NU sunt revendicate -> raman pe BinanceProvider/default.
        return _hype_symbol(symbol)

    # ── infra lazy ─────────────────────────────────────────────────────────────
    def _load_env(self) -> None:
        """Incarca cheile/adresa HL din hyperliquid/.env + config.env (o singura data).
        load_dotenv seteaza DOAR variabilele inca neprezente in os.environ (nu clobber)."""
        if self._env_loaded:
            return
        self._env_loaded = True
        try:
            if _HL_DIR not in sys.path:
                sys.path.insert(0, _HL_DIR)
            from common import load_dotenv  # hyperliquid/common.py
            load_dotenv(os.path.join(_HL_DIR, ".env"))
            load_dotenv(os.path.join(_HL_DIR, "config.env"))
        except Exception as e:  # noqa: BLE001 — fara .env mergem doar pe market-data public
            print(f"[HL] _load_env esuat: {e}")

    def _hl(self):
        """Client HL read-only (secret=None). Lazy + memoizat. None daca SDK/conexiune
        indisponibile (atunci metodele de cont degradeaza curat)."""
        if self._client is not None or self._client_tried:
            return self._client
        with self._lock:
            if self._client is not None or self._client_tried:
                return self._client
            self._client_tried = True
            self._load_env()
            try:
                if _HL_DIR not in sys.path:
                    sys.path.insert(0, _HL_DIR)
                from hl_client import HLClient  # hyperliquid/hl_client.py (reutilizat)
                mainnet = os.environ.get("HL_MAINNET", "true").strip().lower() != "false"
                addr = os.environ.get("HL_ACCOUNT_ADDRESS")
                # secret=None -> client de CITIRE (Info). Pretul/history nu cer nici adresa.
                self._client = HLClient(secret_key=None, account_address=addr, mainnet=mainnet)
            except Exception as e:  # noqa: BLE001
                print(f"[HL] client indisponibil (SDK/conexiune): {e}")
                self._client = None
        return self._client

    def _pair(self) -> Optional[str]:
        """Perechea SPOT @index (ex '@107' pt HYPE/USDC), memoizata."""
        if self._spot_pair:
            return self._spot_pair
        c = self._hl()
        if c is None:
            return None
        try:
            self._spot_pair = c.resolve_spot_pair(self._token)
        except Exception as e:  # noqa: BLE001
            print(f"[HL] resolve_spot_pair({self._token}) esuat: {e}")
        return self._spot_pair

    # ── market-data (public, fara cheie) ─────────────────────────────────────────
    def get_current_price(self, symbol: str) -> Optional[float]:
        c = self._hl()
        pair = self._pair()
        if c is None or pair is None:
            return None
        try:
            return c.spot_mid(pair)
        except Exception as e:  # noqa: BLE001
            print(f"[HL] get_current_price({symbol}) esuat: {e}")
            return None

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        """Istoric SPOT granular pe ultimele `lookback_h` ore, ascendent dupa timp,
        ca lista de {'timestamp'(ms), 'price'(close)}. Bonus pt backfill ferestre trend
        (nu e inca wire-uit la cacheManager in faza asta)."""
        c = self._hl()
        pair = self._pair()
        if c is None or pair is None:
            return None
        try:
            lookback_h = max(float(lookback_h or 0), 0.0)
            interval = "1m" if lookback_h <= 24 else "15m"
            end = int(time.time() * 1000)
            start = end - int(lookback_h * 3600 * 1000)
            candles = c.info.candles_snapshot(pair, interval, start, end) or []
            out = []
            for k in candles:
                try:
                    out.append({"timestamp": int(k.get("t")), "price": float(k.get("c"))})
                except (TypeError, ValueError):
                    continue
            out.sort(key=lambda x: x["timestamp"])
            return out
        except Exception as e:  # noqa: BLE001
            print(f"[HL] get_price_history({symbol}) esuat: {e}")
            return None

    # ── cont SPOT (citire) ───────────────────────────────────────────────────────
    def free_balance(self, asset: str) -> Optional[float]:
        """Soldul SPOT LIBER (disponibil) = total - hold (semantica 'free' ca la Binance).
        Doar pentru asset-urile SPOT pe care le revendicam (HYPE; si USDC daca cerut explicit)."""
        c = self._hl()
        if c is None:
            return None
        try:
            addr = os.environ.get("HL_ACCOUNT_ADDRESS")
            if not addr:
                return None
            for b in c.info.spot_user_state(addr).get("balances", []):
                if b.get("coin") == asset:
                    total = float(b.get("total") or 0.0)
                    hold = float(b.get("hold") or 0.0)
                    return max(total - hold, 0.0)
            return 0.0
        except Exception as e:  # noqa: BLE001
            print(f"[HL] free_balance({asset}) esuat: {e}")
            return None

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        """Fill-urile SPOT (coin == perechea @index) din ultimele `since_s` secunde,
        optional filtrate pe side ('BUY'/'SELL'), NORMALIZATE la {side,price,qty,timestamp(ms)}.
        Fill-urile PERP (coin == 'HYPE') sunt EXCLUSE -> DN-ul nu se amesteca."""
        c = self._hl()
        pair = self._pair()
        if c is None or pair is None:
            return []
        try:
            addr = os.environ.get("HL_ACCOUNT_ADDRESS")
            if not addr:
                return []
            want = side.upper() if side else None
            cutoff_ms = (time.time() - float(since_s)) * 1000.0
            out = []
            for f in (c.info.user_fills(addr) or []):
                if f.get("coin") != pair:        # DOAR spot pair; exclude perp 'HYPE'
                    continue
                t = f.get("time")
                if t is None or float(t) < cutoff_ms:
                    continue
                # HL: side 'B' = buy, 'A' = sell (ask).
                norm_side = "BUY" if f.get("side") == "B" else "SELL"
                if want and norm_side != want:
                    continue
                out.append(_normalize_order({
                    "side": norm_side,
                    "price": f.get("px"),
                    "qty": f.get("sz"),
                    "timestamp": int(t),
                }))
            return out
        except Exception as e:  # noqa: BLE001
            print(f"[HL] get_orders({symbol},{side}) esuat: {e}")
            return []

    def open_orders(self, symbol: str) -> List[dict]:
        """Ordinele SPOT DESCHISE (resting) pt perechea @index, normalizate."""
        c = self._hl()
        pair = self._pair()
        if c is None or pair is None:
            return []
        try:
            out = []
            for o in c.open_orders(pair):
                out.append(_normalize_order({
                    "side": "BUY" if (o.get("side") == "B") else "SELL",
                    "price": o.get("limitPx"),
                    "qty": o.get("sz"),
                    "timestamp": o.get("timestamp"),
                }))
            return out
        except Exception as e:  # noqa: BLE001
            print(f"[HL] open_orders({symbol}) esuat: {e}")
            return []

    # ── plasare ordine SPOT — DRY implicit (vezi nota co-mingling din modul) ──────
    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        """Plaseaza ordin SPOT pe HL. DRY implicit: doar logheaza intentia si intoarce
        None. Devine REAL doar daca HL_LIVE_ORDERS=true (poarta finala — DUPA dry-run SI
        dupa ce co-mingling-ul DN e rezolvat, altfel un SELL ar putea desface piciorul DN)."""
        side = (side or "").upper()
        live = os.environ.get(_LIVE_ENV, "false").strip().lower() == "true"
        if not live:
            print(f"[HL][DRY] as plasa {side} {symbol} qty={qty} @ {price} "
                  f"(real dezactivat; seteaza {_LIVE_ENV}=true pt ordine reale)")
            return None
        # ── cale REALA (gated) ──────────────────────────────────────────────────
        pair = self._pair()
        if pair is None:
            print(f"[HL] place_order: perechea spot indisponibila pt {symbol}")
            return None
        try:
            if _HL_DIR not in sys.path:
                sys.path.insert(0, _HL_DIR)
            from hl_client import HLClient
            secret = os.environ.get("HL_SECRET_KEY")
            if not secret:
                print("[HL] place_order: HL_SECRET_KEY lipsa — nu pot semna")
                return None
            mainnet = os.environ.get("HL_MAINNET", "true").strip().lower() != "false"
            signer = HLClient(secret_key=secret,
                              account_address=os.environ.get("HL_ACCOUNT_ADDRESS"),
                              mainnet=mainnet)
            sz_dec = signer.sz_decimals(self._token)
            ok, oid, msg = signer.spot_order(pair, side == "BUY", float(qty), float(price),
                                             sz_decimals=sz_dec)
            print(f"[HL] place_order {side} {symbol} -> ok={ok} oid={oid} ({msg})")
            return {"orderId": oid, "ok": ok, "msg": msg} if ok else None
        except Exception as e:  # noqa: BLE001
            print(f"[HL] place_order({side} {symbol}) esuat: {e}")
            return None

    # ── CONTRACT StrategyExecutor (Faza 3: cablare API HL reala) ────────────────
    # get_current_price / free_balance de mai sus satisfac deja contractul.
    def _signer(self):
        """Client HL cu cheie (semnare ordine/cancel). ProviderError daca lipseste cheia."""
        if _HL_DIR not in sys.path:
            sys.path.insert(0, _HL_DIR)
        from hl_client import HLClient
        secret = os.environ.get("HL_SECRET_KEY")
        if not secret:
            raise ProviderError("HL_SECRET_KEY lipsa — nu pot semna ordine HL")
        mainnet = os.environ.get("HL_MAINNET", "true").strip().lower() != "false"
        return HLClient(secret_key=secret,
                        account_address=os.environ.get("HL_ACCOUNT_ADDRESS"), mainnet=mainnet)

    def pair_precision(self, symbol: str):
        c = self._hl()
        if c is None:
            return None
        try:
            szd = int(c.sz_decimals(self._token))
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"pair_precision({symbol}): {e}") from e
        # HL spot: pretul admite (8 - szDecimals) zecimale (vezi _round_px in hl_client);
        # volumul admite szDecimals. order_min nu e expus simplu -> 0 (gardul de notional
        # ramane la nivelul strategiei/venue). base_asset = tokenul spot servit.
        return PairPrecision(price_decimals=max(8 - szd, 0), volume_decimals=szd,
                             order_min=0.0, base_asset=self._token)

    def ohlc_closes(self, symbol: str, interval_min: int) -> list:
        c = self._hl()
        pair = self._pair()
        if c is None or pair is None:
            raise ProviderError(f"ohlc_closes({symbol}): client/pereche indisponibile")
        iv = {1: "1m", 5: "5m", 15: "15m", 60: "1h", 240: "4h", 1440: "1d"}.get(int(interval_min), "1h")
        lookback_h = max(1, int(90 * int(interval_min) / 60))   # ~90 bare, ca la Kraken
        try:
            candles = c.candles(pair, iv, lookback_h) or []
            closes = [float(k.get("c")) for k in candles if k.get("c") is not None]
            return closes[:-1] if closes else []                # exclude bara in formare
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"ohlc_closes({symbol}): {e}") from e

    def submit_order(self, symbol: str, side: str, qty: float,
                     price: Optional[float] = None, *, market: bool = False,
                     kind: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> str:
        # SIGURANTA: ordine REALE pe HL doar cu HL_LIVE_ORDERS=true (co-mingling spot cu DN).
        if os.environ.get(_LIVE_ENV, "false").strip().lower() != "true":
            raise ProviderError(f"HL_LIVE_ORDERS=false — refuz ordin real pe HL ({side} {symbol})")
        pair = self._pair()
        if pair is None:
            raise ProviderError(f"submit_order({symbol}): perechea spot indisponibila")
        is_buy = (side or "").lower().startswith("b")
        px = price
        if market or px is None:
            mid = self.get_current_price(symbol)
            if not mid:
                raise ProviderError(f"submit_order({symbol}) market: pret indisponibil")
            px = mid * (1.05 if is_buy else 0.95)               # limita agresiva -> fill imediat
        self.preflight_order(
            symbol, side, qty, px, market=market, kind=kind,
        )
        try:
            signer = self._signer()
            szd = signer.sz_decimals(self._token)
            order_kwargs = {"sz_decimals": szd}
            if client_order_id is not None:
                order_kwargs["cloid"] = client_order_id
            ok, oid, msg = signer.spot_order(
                pair, is_buy, float(qty), float(px), **order_kwargs,
            )
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"submit_order({symbol}): {e}") from e
        if not ok or oid is None:
            raise ProviderError(f"submit_order({symbol}) respins: {msg}")
        return str(oid)

    def preflight_order(self, symbol: str, side: str, qty: float,
                        price: Optional[float] = None, *, market: bool = False,
                        kind: Optional[str] = None) -> None:
        """Refuza un BUY pe care soldul USDC liber nu-l poate finanta integral.

        Hyperliquid poate accepta un ordin supradimensionat, executa doar soldul
        disponibil si anula restul. Pentru DCA asta consuma o runda cu o fractiune
        din suma intentionata. Verificarea se repeta in ``submit_order`` pentru a
        inchide cursa dintre preflight-ul motorului si trimiterea efectiva.
        """
        if not (side or "").lower().startswith("b"):
            return
        if price is None:
            mid = self.get_current_price(symbol)
            if not mid:
                raise ProviderError(f"preflight_order({symbol}): pret indisponibil")
            price = mid * (1.05 if market else 1.0)
        required = float(qty) * float(price)
        available = self.free_balance("USDC")
        if available is None:
            raise ProviderError(
                f"preflight_order({symbol}): soldul USDC nu poate fi confirmat"
            )
        tolerance = max(1e-9, required * 1e-12)
        if float(available) + tolerance < required:
            raise ProviderError(
                f"preflight_order({symbol}) {kind or 'BUY'}: sold USDC insuficient "
                f"({float(available):.8f} < {required:.8f}) — ordin netrimis"
            )

    def order_status(self, symbol: str, order_id: str):
        c = self._hl()
        pair = self._pair()
        if c is None or pair is None:
            raise ProviderError(f"order_status({order_id}): client/pereche indisponibile")
        addr = os.environ.get("HL_ACCOUNT_ADDRESS")
        if not addr:
            raise ProviderError("order_status: HL_ACCOUNT_ADDRESS lipsa")
        try:
            oid = int(order_id)
            query = getattr(c.info, "query_order_by_oid", None)
            if not callable(query):
                raise ProviderError(
                    "SDK Hyperliquid prea vechi: lipseste query_order_by_oid"
                )
            raw_status = query(addr, oid) or {}
            if raw_status.get("status") != "order":
                raise ProviderError(
                    f"order_status({order_id}): status nedeterminat ({raw_status})"
                )
            status_payload = raw_status.get("order") or {}
            venue_status = str(status_payload.get("status") or "")
            order_payload = status_payload.get("order") or {}

            # user_fills este sursa cumulativa pentru cantitate/cost/fee. Il citim
            # si cand ordinul este inca open, ca partial fills sa nu fie pierdute.
            filled = cost = fee = 0.0
            for f in (c.info.user_fills(addr) or []):
                if int(f.get("oid", -1)) != oid:
                    continue
                sz = float(f.get("sz") or 0.0)
                fill_price = float(f.get("px") or 0.0)
                filled += sz
                cost += sz * fill_price
                raw_fee = float(f.get("fee") or 0.0)
                fee_token = str(f.get("feeToken") or "").upper()
                # La BUY, HL taxeaza de regula in activul de baza (HYPE), iar
                # la SELL in quote (USDC). Motorul contabilizeaza totul in quote,
                # deci convertim fee-ul HYPE la pretul exact al fill-ului.
                if fee_token == self._token:
                    fee += raw_fee * fill_price
                elif not fee_token or fee_token == "USDC":
                    fee += raw_fee
                else:
                    raise ProviderError(
                        f"order_status({order_id}): feeToken necunoscut {fee_token!r}"
                    )
            try:
                original = float(order_payload.get("origSz") or 0.0)
                remaining = float(order_payload.get("sz") or 0.0)
            except (TypeError, ValueError) as e:
                raise ProviderError(
                    f"order_status({order_id}): dimensiuni ordin invalide"
                ) from e
            expected_filled = max(0.0, original - remaining)
            tolerance = max(1e-12, original * 1e-9)
            if expected_filled > filled + tolerance:
                # Endpointul de status poate ajunge inaintea user_fills. Nu
                # declarăm terminal un ordin pana nu putem contabiliza costul si fee-ul.
                raise ProviderError(
                    f"order_status({order_id}): fills incomplete "
                    f"({filled} < {expected_filled})"
                )
            if venue_status == "open":
                normalized = "open"
            elif venue_status == "filled":
                normalized = "closed"
            else:
                # Toate respingerile/anularile sunt terminale si nu trebuie
                # confundate cu un ordin temporar absent din open_orders.
                normalized = "canceled"
            return OrderStatus(normalized, filled, cost, fee)
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"order_status({order_id}): {e}") from e

    def cancel_order(self, symbol: str, order_id: str) -> None:
        pair = self._pair()
        if pair is None:
            raise ProviderError(f"cancel_order({order_id}): perechea indisponibila")
        try:
            canceled = self._signer().cancel(pair, int(order_id))
            if not canceled:
                raise ProviderError(
                    f"cancel_order({order_id}): venue-ul nu a confirmat anularea"
                )
        except ProviderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"cancel_order({order_id}): {e}") from e
