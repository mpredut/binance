# providers/replay_provider.py
"""ReplayMarketDataProvider — implementeaza MarketDataProvider peste date
ISTORICE (cache_price_{symbol}.jsonl), pt backtest/replay
(23 iul, research/UNIFIED_BACKTEST_PLAN.md Faza 1). NU bate reteaua NICIODATA.

De ce exista: verificat in aceeasi sesiune ca `Instrument.__init__(api=...)` +
`instruments_config.load_for(api=...)` accepta DEJA un `MarketDataProvider`
injectat — monitortrades.py NU trebuie schimbat la liniile unde citeste
pret/ordine (inst.price(), inst.orders(...)) ca sa ruleze pe replay, doar
construit cu un `MarketApi([ReplayMarketDataProvider(...)])` in loc de cel live.

Ceas: `now(symbol)` intoarce timestamp-ul ULTIMULUI pret citit prin
get_current_price (nu un ceas separat care avanseaza independent) — timpul
"vine din pretul obtinut", cerinta explicita din sesiune. Codul care are
nevoie de "acum" (ex. monitortrades.monitor_price_and_trade(now_fn=...))
primeste acest `now` ca now_fn, ca ambele sa avanseze IMPREUNA.

Broker simulat MINIMAL (nu retea): place_order()/get_orders()/free_balance()
tin cont intern de pozitie (qty/cost mediu) per symbol, suficient pt
monitortrades.get_position_stats/get_relevant_trade/monitor_price_and_trade
sa functioneze identic structural ca live. NU modeleaza limit-order-uri
neexecutate (place_order() executa INSTANT, la pretul cerut) — simplificare
deliberata pt Faza 1 (monitortrades plaseaza market-like azi oricum, prin
place_order_smart cu safeback; fidelitatea de umplere e o rafinare de Faza 2).
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

from providers.market_api import MarketDataProvider

_QUOTE_SUFFIXES = ("USDC", "USDT", "BUSD", "FDUSD", "USD")


def _base_asset(symbol: str) -> str:
    """Acelasi heuristic ca monitortrades._as_instrument/get_available_qty:
    strip sufixul de cotare -> asset de baza (BTCUSDC -> BTC)."""
    for q in _QUOTE_SUFFIXES:
        if symbol.endswith(q):
            return symbol[: -len(q)]
    return symbol


def load_price_series(path: str, symbol: str) -> List[Tuple[float, float]]:
    """Citeste cache_price_{symbol}.jsonl (format {"s":symbol,"i":[ts_ms,price]})
    -> lista (ts_sec, price) ASCENDENTA dupa timp. [] daca fisierul lipseste."""
    out: List[Tuple[float, float]] = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("s") != symbol:
                continue
            try:
                ts_ms, price = rec["i"]
                out.append((ts_ms / 1000.0, float(price)))
            except (KeyError, TypeError, ValueError):
                continue
    out.sort(key=lambda x: x[0])
    return out


class ReplayMarketDataProvider(MarketDataProvider):
    """Serveste market-data + cont SIMULAT pt (potential) mai multe simboluri
    simultan, fiecare cu propriul cursor — un backtest tipic are un provider
    per rulare, folosit de toate Instrument-ele acelei rulari."""

    def __init__(self, price_series: Dict[str, List[Tuple[float, float]]],
                 fee_pct: float = 0.1):
        self._series = price_series
        self._cursor: Dict[str, int] = {s: 0 for s in price_series}
        self._last_ts: Dict[str, float] = {}
        self._fee_pct = fee_pct
        self._orders: Dict[str, List[dict]] = {s: [] for s in price_series}
        self._positions: Dict[str, Tuple[float, float]] = {}   # symbol -> (qty, cost_total)

    @property
    def name(self) -> str:
        return "Replay"

    def supports_symbol(self, symbol: str) -> bool:
        return symbol in self._series

    # ── avansare ceas: apelata de driver-ul de backtest, NU de codul botului ──
    def advance(self, symbol: str, steps: int = 1) -> Optional[float]:
        """Muta cursorul simbolului cu `steps` pasi inainte; intoarce noul pret
        curent, sau None daca seria s-a terminat (nu mai avanseaza dincolo)."""
        series = self._series.get(symbol)
        if not series:
            return None
        new_idx = min(self._cursor[symbol] + steps, len(series))
        if new_idx == self._cursor[symbol] and new_idx >= len(series):
            return None   # deja la capat, nimic de avansat
        self._cursor[symbol] = new_idx
        if new_idx == 0:
            return None
        ts, price = series[new_idx - 1]
        self._last_ts[symbol] = ts
        return price

    def has_more(self, symbol: str) -> bool:
        return self._cursor.get(symbol, 0) < len(self._series.get(symbol, []))

    def now(self, symbol: Optional[str] = None) -> float:
        """Timpul "curent" al replay-ului = timestamp-ul ULTIMULUI pret citit
        (nu un ceas separat) — fara `symbol`, cel mai recent dintre toate cele
        avansate pana acum (0.0 daca niciunul inca)."""
        if symbol is not None:
            return self._last_ts.get(symbol, 0.0)
        return max(self._last_ts.values(), default=0.0)

    # ── market-data ──────────────────────────────────────────────────────────
    def get_current_price(self, symbol: str) -> Optional[float]:
        series = self._series.get(symbol)
        idx = self._cursor.get(symbol, 0)
        if not series or idx == 0:
            return None
        return series[idx - 1][1]

    def get_price_history(self, symbol: str, lookback_h: float) -> Optional[List]:
        series = self._series.get(symbol)
        idx = self._cursor.get(symbol, 0)
        if not series or idx == 0:
            return None
        cutoff = series[idx - 1][0] - lookback_h * 3600
        return [{"timestamp": int(ts * 1000), "price": p}
                for ts, p in series[:idx] if ts >= cutoff]

    # ── cont (broker simulat, fara retea) ───────────────────────────────────
    def free_balance(self, asset: str) -> Optional[float]:
        # cauta simbolul ale carui pozitii au acest asset ca baza (BTCUSDC -> BTC)
        for symbol in self._series:
            if _base_asset(symbol) == asset:
                qty, _cost = self._positions.get(symbol, (0.0, 0.0))
                return qty
        return 0.0

    def get_orders(self, symbol: str, side: Optional[str], since_s: float) -> List[dict]:
        now = self.now(symbol)
        cutoff_ms = (now - since_s) * 1000.0
        orders = self._orders.get(symbol, [])
        out = [o for o in orders if o["timestamp"] >= cutoff_ms]
        if side:
            out = [o for o in out if o["side"] == side.upper()]
        return out

    def place_order(self, symbol: str, side: str, price: float, qty: float, **kwargs):
        """Executie INSTANTA la pretul cerut (simplificare Faza 1 — vezi docstring
        modulului). Actualizeaza pozitia (qty/cost mediu) si istoricul de ordine."""
        side = side.upper()
        price = float(price)
        qty = float(qty)
        ts_ms = int(self.now(symbol) * 1000)
        self._orders.setdefault(symbol, []).append(
            {"side": side, "price": price, "qty": qty, "timestamp": ts_ms})

        pos_qty, pos_cost = self._positions.get(symbol, (0.0, 0.0))
        if side == "BUY":
            pos_qty += qty
            pos_cost += qty * price
        else:
            sell_qty = min(qty, pos_qty)
            if pos_qty > 1e-12:
                pos_cost -= (pos_cost / pos_qty) * sell_qty
            pos_qty -= sell_qty
            pos_qty = max(pos_qty, 0.0)
            pos_cost = max(pos_cost, 0.0)
        self._positions[symbol] = (pos_qty, pos_cost)
        return {"orderId": -1, "backtest": True}

    def guards_internally(self) -> bool:
        # True (simplificare DELIBERATA Faza 1): gardul agnostic (order_guard.py) are
        # propriile praguri/ferestre CONFIGURATE per nume de venue (order_guard.conf) —
        # "Replay" n-ar avea o intrare acolo, deci ar cadea pe un comportament neclar/
        # fail-closed. Rafinarea (simuland si order_guard) ramane pt o iteratie viitoare;
        # azi place_order() executa direct, fara acel strat suplimentar.
        return True
