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
import os
import sys
import time
from typing import Optional, List, Callable, Any

from providers.market_api import api as _default_api
import order_guard
import order_outcomes_log as _outcomes_log
from lock import trade_cooldown


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
        # GARD complet AGNOSTIC — profit, plafon-zilnic/anti-spam, cooldown anti-rapid-fire,
        # trend-wait, jurnal FLEET-WIDE. Se aplica DOAR providerilor care NU guardeaza intern
        # (Binance are deja o implementare PROPRIE, mai bogata — foloseste date REALE de
        # permisiuni API in loc de curba gauss generica — vezi binance_api/bapi_placeorder.py;
        # ISI PASTREAZA acea implementare, NU trece pe aici, ca sa nu se dubleze/coliziona
        # cooldown-ul). 30 iul, cerere user: Kraken/Hyperliquid capata acum ACELEASI 4
        # protectii ca Binance, prin cod PARTAJAT (order_guard.py, lock/trade_cooldown.py,
        # cacheManager.should_wait — deja generice — si order_outcomes_log.py, nou).
        #
        # bypass_profit_guard=True (ex. disjunctor de crash) sare DOAR gardul de profit +
        # plafonul de weight (comportament PREEXISTENT, neschimbat) — la fel ca la Binance,
        # NU sare plafonul zilnic, cooldown-ul sau trend-wait-ul (raman active si la disjunctor).
        bypass = bool(kwargs.pop("bypass_profit_guard", False))
        side_u = side.upper()
        if self._provider.guards_internally():
            return self._provider.place_order(self.symbol, side, price, qty, **kwargs)

        reason = None
        order = None
        # 30 iul, fix: `safeback_seconds` e chiar parametrul pe care monitortrades.py
        # (sbs=MT_GUARD_WINDOW_DAYS zile, implicit 12) si tradeall.py (14 zile) il
        # SUPRASCRIU explicit la fiecare apel real — defaultul din config (48h) e
        # aproape niciodata folosit efectiv pe Binance. instruments.conf are deja
        # [KRAKEN_HYPE] enabled=yes sub "mt" -> acelasi `sbs` (12-14 zile) ar trebui
        # sa se aplice IDENTIC si acolo, nu doar la Binance. NU se scoate din kwargs
        # (ramane si pt provider.place_order(), desi Kraken/HL il ignora azi).
        safeback_override = kwargs.get("safeback_seconds")
        cancel_opposite = bool(kwargs.get("cancelorders", True))
        try:
            # 0. AJUSTARE PRET + curatare ordine opuse (MECANICA venue, hook) — RULATA
            # INAINTE de gardul de profit, ca gardul sa vada exact acelasi pret ca azi
            # (pe Binance: nudge ±0.1% + round + cancel ordine opuse contraproductive).
            # Default agnostic (Kraken/HL): identitate -> pretul ramane neschimbat.
            price = self._provider.adjust_order_price(self.symbol, side_u, price,
                                                      cancel_opposite=cancel_opposite)

            # 1. PLAFON ZILNIC + ANTI-SPAM (agnostic) — NU sarit de bypass_profit_guard.
            ok, reason = order_guard.daily_limit_guard(self._provider, self.symbol, side_u,
                                                       safeback_sec=safeback_override)
            if not ok:
                return None

            if not bypass:
                margin = order_guard.margin_for(self._provider.name)
                # tier 1: referinta min/max via hook-ul providerului. Default (Kraken/HL):
                # fereastra per-venue din order_guard.conf; Binance: fereastra safeback_sec
                # (Order-cache). Fereastra goala/dezactivata -> profit_guard cade pe
                # last_opposite_fill.
                window_ref = self._provider.profit_guard_window_ref(
                    self.symbol, side_u, safeback_override)
                ok = order_guard.profit_guard(self._provider, self.symbol, side_u, price, margin,
                                              window_ref=window_ref)
                if not ok:
                    reason = "profit_guard"
                    return None
                # PLAFON de CANTITATE pe AMBELE directii — via hook-ul providerului
                # (30 iul): default agnostic = order_guard.weight_limit (gauss, ex.
                # HYPE-Kraken); Binance suprascrie cu apply_weight_limit (API real).
                # Nu tranzactiona tot dintr-o data. Side-aware: SELL->balanta base,
                # BUY->balanta quote/pret.
                qty = self._provider.cap_quantity(self.symbol, side_u, price, qty,
                                                  base=self.base, quote=self.quote)
                if qty is None or qty <= 0:
                    print(f"[{self.symbol}] {side_u} qty 0 dupa weight -> skip")
                    reason = "qty_zero_after_weight"
                    return None

            # 2. TREND-WAIT (agnostic, delay-nu-block — la fel ca Binance
            # wait_for_favorable_entry). Deja calculat pt simboluri non-Binance inregistrate
            # in instruments.conf (cacheManager.py); indisponibil -> should_wait cade pe
            # False (nu asteapta), niciodata blocaj. Import LAZY: cacheManager -> market_api
            # ar inchide ciclul la nivel de modul.
            try:
                import cacheManager as cm
                waited = cm.get_short_trend_manager().wait_for_favorable_entry(side_u, self.symbol)
                if waited:
                    print(f"[{self.symbol}] {side_u} așteptat {waited:.1f}s (trend favorabil)")
                    fresh = self._provider.get_current_price(self.symbol)
                    if fresh is not None:
                        price = fresh
            except Exception as e:  # noqa: BLE001 — gate oportunist, esec -> trimite oricum
                print(f"[{self.symbol}] {side_u} trend-wait indisponibil: {e}")

            # 3. COOLDOWN anti-rapid-fire (agnostic, acelasi modul global ca Binance —
            # cheile sunt symbol-uri, deci fara coliziune intre venue-uri diferite).
            with trade_cooldown.trade_slot(side_u, self.symbol) as slot:
                if not slot.allowed:
                    age = time.time() - slot.info.get("timestamp", 0)
                    print(f"[{self.symbol}] {side_u} BLOCAT de cooldown: ultim ordin "
                          f"({slot.info.get('side')}) acum {age:.0f}s")
                    reason = "cooldown"
                    return None
                order = self._provider.place_order(self.symbol, side, price, qty, **kwargs)
                if order:
                    order_id = order.get("orderId") if isinstance(order, dict) else None
                    slot.commit(order_id)
                else:
                    reason = reason or "no_fill"
                return order
        except Exception as e:  # noqa: BLE001 — nu pot verifica -> nu tranzactionez orb
            print(f"[{self.symbol}] {side_u} BLOCAT (fail-closed): {e}")
            reason = "guard_check_failed"
            return None
        finally:
            try:
                caller = os.path.basename(sys._getframe(1).f_code.co_filename)
            except Exception:
                caller = None
            _outcomes_log.log_order_outcome(
                self.symbol, side_u, price, qty, "executed" if order else "refused",
                None if order else reason, kwargs.get("motivation"), caller=caller)

    def min_qty(self) -> float:
        """Volumul minim de ordin al venue-ului pt symbol (0 = fara gard de volum)."""
        try:
            return float(self._provider.min_order_qty(self.symbol) or 0.0)
        except Exception:  # noqa: BLE001
            return 0.0

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
