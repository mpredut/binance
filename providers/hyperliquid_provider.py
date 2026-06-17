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

from .market_api import MarketDataProvider, _normalize_order

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
