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
        # `is_retry` (30 iul): setat de order_retry_worker cand REIA un ordin din coada —
        # impiedica re-enqueue-ul (fara recursie infinita). Scos din kwargs (nu merge la provider).
        is_retry = bool(kwargs.pop("is_retry", False))
        bypass = bool(kwargs.pop("bypass_profit_guard", False))
        side_u = side.upper()
        if self._provider.guards_internally():
            return self._provider.place_order(self.symbol, side, price, qty, **kwargs)

        # Capturate INAINTE de orice pop/reassign, pt eventualul enqueue de re-plasare:
        # intentia ORIGINALA (qty ne-plafonat, pretul CERUT) + kwargs care reproduc apelul.
        orig_qty = qty
        orig_price = price   # pretul cerut = intentia apelantului -> gardul de pret la retry
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
        # `smart` (30 iul, CORECTIE): distinge cele DOUA interfete vechi colapsate aici —
        # place_order_smart (SMART: cancel ordine opuse + nudge pret ±0.1% INAINTE de gard)
        # vs place_safe_order (SAFE: FARA cancel-opuse, FARA nudge). Colapsarea initiala
        # aplica gresit pre-procesarea "smart" si apelantilor SAFE. Default True (monitortrades/
        # tradeall/place_order_smart, calea principala); apelantii fostului place_safe_order
        # (rtrade normal, monitororder, assetguardian, trailing_stop, legacy) trec smart=False.
        smart = bool(kwargs.pop("smart", True))
        # kwargs care reproduc EXACT acest apel la un retry (bypass/smart au fost pop-uite;
        # restul — safeback_seconds/force/cancelorders/hours/motivation — raman in kwargs).
        retry_kwargs = dict(kwargs)
        retry_kwargs["bypass_profit_guard"] = bypass
        retry_kwargs["smart"] = smart
        # Un ordin MARKET ignora pretul cerut de apelant. Retinem daca trebuie sa
        # revalidam gardul chiar inainte de submit, la pretul executabil curent.
        # Altfel un SELL cerut la +1% putea trece gardul, apoi `force=True` il
        # executa imediat la piata sub marja validata (TOCTOU financiar).
        is_market = bool(kwargs.get("force", False))
        profit_margin = None
        profit_window_ref = None
        try:
            # 0. AJUSTARE PRET + curatare ordine opuse (MECANICA venue, hook) — DOAR daca
            # smart, RULATA INAINTE de gardul de profit (ca gardul sa vada acelasi pret ca
            # vechiul place_order_smart). Pe Binance: nudge ±0.1% + round + cancel ordine
            # opuse contraproductive (neconditionat, ca in place_order_smart). smart=False ->
            # pretul ramane neschimbat aici (ca vechiul place_safe_order); place_order_mechanics
            # tot face clamp+round final la trimitere.
            if smart:
                price = self._provider.adjust_order_price(self.symbol, side_u, price,
                                                          cancel_opposite=True)

            # 1. PLAFON ZILNIC + ANTI-SPAM (agnostic) — NU sarit de bypass_profit_guard.
            ok, reason = order_guard.daily_limit_guard(self._provider, self.symbol, side_u,
                                                       safeback_sec=safeback_override)
            if not ok:
                return None

            if not bypass:
                profit_margin = order_guard.margin_for(self._provider.name)
                # tier 1: referinta min/max via hook-ul providerului. Default (Kraken/HL):
                # fereastra per-venue din order_guard.conf; Binance: fereastra safeback_sec
                # (Order-cache). Fereastra goala/dezactivata -> profit_guard cade pe
                # last_opposite_fill.
                profit_window_ref = self._provider.profit_guard_window_ref(
                    self.symbol, side_u, safeback_override)
                ok = order_guard.profit_guard(
                    self._provider, self.symbol, side_u, price, profit_margin,
                    window_ref=profit_window_ref)
                if not ok:
                    reason = "profit_guard"
                    return None
                # PLAFON de CANTITATE pe AMBELE directii — via hook-ul providerului
                # (30 iul): default agnostic = order_guard.weight_limit (gauss, ex.
                # HYPE-Kraken); Binance suprascrie cu apply_weight_limit (API real).
                # Nu tranzactiona tot dintr-o data. Side-aware: SELL->balanta base,
                # BUY->balanta quote/pret.
                # `cancelorders`/`hours` fac parte din politica bogata Binance:
                # daca balanta e blocata in ordine vechi/outlier, manage_quantity le
                # poate elibera. Rewire-ul generic le pastra in kwargs, dar nu le
                # transmitea hook-ului si dezactiva silentios comportamentul cerut
                # explicit de rtrade/monitororder.
                qty = self._provider.cap_quantity(
                    self.symbol, side_u, price, qty,
                    base=self.base, quote=self.quote,
                    cancelorders=bool(kwargs.get("cancelorders", False)),
                    hours=float(kwargs.get("hours", 5) or 5),
                )
                if qty is None or qty <= 0:
                    print(f"[{self.symbol}] {side_u} qty 0 dupa weight -> skip")
                    reason = "qty_zero_after_weight"
                    return None

            # 2. TREND-WAIT (agnostic, delay-nu-block — la fel ca Binance
            # wait_for_favorable_entry). Deja calculat pt simboluri non-Binance inregistrate
            # in instruments.conf (cacheManager.py); indisponibil -> should_wait cade pe
            # False (nu asteapta), niciodata blocaj. Import LAZY: cacheManager -> market_api
            # ar inchide ciclul la nivel de modul.
            waited = False
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

            # Revalidare finala dupa orice asteptare/repricing. Pentru MARKET folosim
            # obligatoriu cotatia curenta, nu pretul-limită decorativ primit de la
            # apelant. `bypass_profit_guard` ramane calea explicita pentru iesiri de
            # protectie care trebuie executate chiar si in pierdere.
            if not bypass and (is_market or waited):
                guard_price = price
                if is_market:
                    guard_price = self._provider.get_current_price(self.symbol)
                    if guard_price is None or float(guard_price) <= 0:
                        print(f"[{self.symbol}] {side_u} MARKET BLOCAT: pret curent indisponibil")
                        reason = "market_price_unavailable"
                        return None
                    guard_price = float(guard_price)
                ok = order_guard.profit_guard(
                    self._provider, self.symbol, side_u, guard_price, profit_margin,
                    window_ref=profit_window_ref)
                if not ok:
                    reason = "profit_guard"
                    return None

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
            # OUTBOX: o plasare normala reusita satisface orice intentie pending pe
            # acelasi symbol+side. Este esential pt apelantii cu retry local (rtrade):
            # altfel esecul initial ramane in coada si poate produce un ordin duplicat
            # dupa ce retry-ul local a reusit deja. Workerul nu are nevoie de resolve:
            # el face claim atomic inainte de plasare.
            if order is not None and not is_retry:
                try:
                    import order_retry
                    order_retry.resolve(self.symbol, side_u)
                except Exception as _e:  # noqa: BLE001
                    print(f"[{self.symbol}] {side_u} resolve retry esuat (ignor): {_e}")
            # Daca a esuat si NU e deja un retry, salveaza intentia persistenta.
            # Best-effort: orice eroare aici NU afecteaza returul.
            elif order is None and not is_retry:
                try:
                    import order_retry
                    if order_retry.RETRY_ENABLED:
                        # pretul de piata la momentul esecului (best-effort) — pt gardul de
                        # pret/dedup la retry. Il luam DOAR cand chiar enqueue-am.
                        ref_price = None
                        try:
                            ref_price = self._provider.get_current_price(self.symbol)
                        except Exception:  # noqa: BLE001
                            ref_price = None
                        order_retry.enqueue(self.symbol, side_u, orig_qty, retry_kwargs,
                                            requested_price=orig_price, ref_price=ref_price)
                except Exception as _e:  # noqa: BLE001
                    print(f"[{self.symbol}] {side_u} enqueue retry esuat (ignor): {_e}")

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
