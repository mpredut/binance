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
import os
import sys
import math
import time
from typing import Optional, List

from market_api import MarketDataProvider, _normalize_order

_KRAKEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kraken")


def _live() -> bool:
    return os.environ.get("KRAKEN_LIVE_ORDERS", "false").strip().lower() == "true"


class KrakenProvider(MarketDataProvider):
    def __init__(self):
        self._cli = None  # lazy

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
        self._cli = KrakenClient(os.environ.get("KRAKEN_API_KEY"),
                                 os.environ.get("KRAKEN_API_SECRET"))
        return self._cli

    # ── market-data (public, fara chei) ────────────────────────────────────────
    def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            return self._client().last_price(symbol)
        except Exception as e:  # noqa: BLE001
            print(f"[Kraken] pret {symbol}: {e}")
            return None

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
        """Istoric tranzactii proprii (TradesHistory) pt pereche, filtrat pe side+varsta."""
        try:
            cli = self._client()
            res = cli._private("TradesHistory")
            trades = (res or {}).get("trades", {}) or {}
            cutoff = time.time() - since_s
            want = (side or "").upper()
            out = []
            su = symbol.upper()
            for tr in trades.values():
                p = str(tr.get("pair", "")).upper()
                if su not in p and p not in su:
                    continue
                if float(tr.get("time", 0)) < cutoff:
                    continue
                s = "BUY" if str(tr.get("type", "")).lower() == "buy" else "SELL"
                if want and s != want:
                    continue
                out.append(_normalize_order({
                    "side": s, "price": tr.get("price"),
                    "qty": tr.get("vol"), "timestamp": int(float(tr.get("time", 0)) * 1000),
                }))
            return out
        except Exception as e:  # noqa: BLE001
            print(f"[Kraken] get_orders {symbol}: {e}")
            return []

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
