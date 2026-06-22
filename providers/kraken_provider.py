# kraken_provider.py
"""KrakenProvider — market-data + cont SPOT Kraken, peste kraken/kraken_client.py.

Pt monitortrades GENERIC pe Kraken: xStocks (TSLAx...) si HYPE (Kraken NU are DN ->
fara co-mingling cu hedge-ul). `symbol` = perechea NATIVA Kraken (ex 'HYPEUSD').

EXPLICIT-ONLY: supports_symbol -> False. Providerul e raol DOAR prin descriptorul
Instrument (provider="kraken" -> provider_by_name), NU prin rutarea pe sablon a facadei.
Asa nu se bate cu HyperliquidProvider pe 'HYPE*' (HYPEUSD vs HYPEUSDC).

Chei: KRAKEN_API_KEY / KRAKEN_API_SECRET (env). Pretul/istoricul merg si fara chei
(public). Plasarea: DRY (add_order validate=True) pana la KRAKEN_LIVE_ORDERS=true.
Import LAZY al clientului (sys.path pe kraken/), ca flota sa nu cada daca lipseste ceva.
"""
import json
import os
import sys
import math
import time
from typing import Optional, List

from .market_api import MarketDataProvider, _normalize_order, env_value

_KRAKEN_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kraken")
# Cache PARTAJAT de fills, produs de kraken/kraken_cachemanager.py (cross-proces).
_KRAKEN_CACHE_FILE = os.path.join(os.path.dirname(_KRAKEN_DIR), "cachedb", "cache_trade_kraken.json")
_CACHE_MAX_STALE_S = 30.0   # peste asta -> cachemanager probabil oprit -> fallback TradesHistory


def _live() -> bool:
    # os.environ are prioritate; fallback pe kraken/.env (la fel ca cheile API).
    v = os.environ.get("KRAKEN_LIVE_ORDERS")
    if v is None:
        v = env_value(_KRAKEN_DIR, "KRAKEN_LIVE_ORDERS")
    return (v or "false").strip().lower() == "true"


class KrakenProvider(MarketDataProvider):
    def __init__(self):
        self._cli = None  # lazy
        self._minqty = {}  # cache symbol -> ordermin (din pair_info)

    @property
    def name(self) -> str:
        return "Kraken"

    def supports_symbol(self, symbol: str) -> bool:
        # EXPLICIT-ONLY: nu revendica nimic prin sablon; reachable doar prin Instrument.
        return False

    # ── client lazy ────────────────────────────────────────────────────────────
    def _client(self):
        if self._cli is not None:
            return self._cli
        # kraken/ are 'common.py' cu ACELASI nume ca hyperliquid/common.py. Daca HL a fost
        # deja folosit in proces, sys.modules['common'] = al lui HL -> kraken_client
        # (`from common import http_get`) ar lua modulul GRESIT. Importam kraken_client cu
        # kraken/ in fata si 'common' evacuat, apoi RESTAURAM sys.path + cache-ul ca HL sa nu
        # fie afectat. kraken_client isi leaga http_get la IMPORT -> o rezolvare corecta e de ajuns.
        saved_path = list(sys.path)
        saved_common = sys.modules.pop("common", None)
        sys.modules.pop("kraken_client", None)
        try:
            sys.path.insert(0, _KRAKEN_DIR)
            from kraken_client import KrakenClient  # noqa: import lazy
        finally:
            sys.path[:] = saved_path                  # restaureaza ordinea (HL neafectat)
            sys.modules.pop("common", None)           # scoate eventualul common al krakenului
            if saved_common is not None:
                sys.modules["common"] = saved_common  # repune common-ul lui HL
        # Cheile din kraken/.env (NU din env-ul flotei). env-ul are prioritate daca e setat.
        api_key = os.environ.get("KRAKEN_API_KEY") or env_value(_KRAKEN_DIR, "KRAKEN_API_KEY")
        api_secret = os.environ.get("KRAKEN_API_SECRET") or env_value(_KRAKEN_DIR, "KRAKEN_API_SECRET")
        self._cli = KrakenClient(api_key, api_secret)
        return self._cli

    # ── market-data (public, fara chei) ────────────────────────────────────────
    def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            return self._client().last_price(symbol)
        except Exception as e:  # noqa: BLE001
            print(f"[Kraken] pret {symbol}: {e}")
            return None

    def min_order_qty(self, symbol: str) -> float:
        """Volumul minim (ordermin) al perechii, din pair_info (public). Cache-uit."""
        if symbol in self._minqty:
            return self._minqty[symbol]
        mn = 0.0
        try:
            info = self._client().pair_info(symbol) or {}
            mn = float(info.get("ordermin", 0) or 0.0)
        except Exception as e:  # noqa: BLE001
            print(f"[Kraken] ordermin {symbol}: {e}")
        self._minqty[symbol] = mn
        return mn

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        """OHLC public -> [{timestamp(ms), price=close}] ascendent."""
        try:
            cli = self._client()
            # alege intervalul (minute) ca sa incapa ~<=720 puncte
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

    # ── cont (chei) ────────────────────────────────────────────────────────────
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
        """Tranzactii proprii pt pereche, filtrat pe side+varsta. Sursa: cache PARTAJAT
        (kraken_cachemanager, cross-proces) daca e PROASPAT -> 1 fetch / N procese + vedere
        comuna pt gard; altfel TradesHistory direct (fallback, sigur si fara cachemanager)."""
        try:
            rows = self._fills_from_cache(symbol)
            if rows is None:                                  # cache lipsa/vechi -> API direct
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

    def _fills_from_cache(self, symbol: str):
        """Fills pt symbol din cache-ul PARTAJAT (kraken_cachemanager). None daca fisierul
        lipseste / e prea vechi (cachemanager oprit) / n-are symbol -> get_orders cade pe API."""
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
            return None                                       # stale -> cachemanager probabil mort
        return [{
            "side": "BUY" if t.get("isBuyer") else "SELL",
            "price": t.get("price"), "qty": t.get("qty"),
            "timestamp": int(t.get("time", 0)),
        } for t in items[key]]

    def _fills_from_api(self, symbol: str):
        """Fallback: TradesHistory direct (comportamentul de dinainte de cachemanager)."""
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

    # ── plasare (DRY pana la KRAKEN_LIVE_ORDERS=true) ──────────────────────────
    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        live = _live()
        s = (side or "").lower()
        s = "buy" if s.startswith("b") else "sell"
        if not live:
            print(f"[Kraken][DRY] as plasa {side} {symbol} qty={qty} @ {price} "
                  f"(real off; seteaza KRAKEN_LIVE_ORDERS=true)")
            try:                                 # validare server-side fara plasare
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
