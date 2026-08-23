import os
import time
import datetime
import math
import uuid
from collections import deque
import threading
from decimal import Decimal, ROUND_DOWN
from concurrent.futures import ThreadPoolExecutor, wait


# my imports
import log
import alertnotifiers as alert
import utils as u
import symbols as sym
from binance_api import bapi as api
from binance_api import bapi_placeorder as po   # pastrat pt WeightLimitBlock (dead-safe)
from providers.market_api import api as mkt      # proxy unic guardat (Instrument.place)
from providers.execution_audit import AuditedStrategyExecutor
from providers.quantity import decide_quantity
from market_regime import MarketRegimeDecision, MarketRegimeEvaluator
from rtrade_pair_store import RTradePairStore, rtrade_client_order_id
from strategies.rtrade_pair import (
    OrderSnapshot as PairOrderSnapshot,
    OrderTicket as PairOrderTicket,
    PairCoordinator,
    PairPolicy,
)


# 23 iul: incarca parametrii tunabili din rtrade_config.env (versionat, se
# COMITE — fara secrete) INAINTE de a citi orice os.environ.get(...) de mai jos.
# botcore.load_dotenv NU suprascrie variabile deja setate in mediul real.
from botcore import load_dotenv as _load_dotenv
_load_dotenv("rtrade_config.env")

# Intervalul de timp între încercările de anulare și recreere a ordinului (în secunde)
WAIT_FOR_ORDER = float(os.environ.get("RTRADE_WAIT_FOR_ORDER_SEC", "32"))
MIN_adjustment_percent = float(os.environ.get("RTRADE_MIN_ADJUSTMENT_PCT", "0.01"))

# Bugetul per runda este exprimat explicit in moneda de cotare. Cantitatea de
# TAO se calculeaza o singura data din pretul de start al rundei.
RTRADE_NOTIONAL_USDC = float(os.environ.get("RTRADE_NOTIONAL_USDC", "500"))

# Ghicit initial de spread (fractie) pt filled_buy_price/filled_sell_price la
# pornire, inainte de primul ordin real executat.
RTRADE_INITIAL_SPREAD_PCT = float(os.environ.get("RTRADE_INITIAL_SPREAD_PCT", "0.1"))

# Rata de "relaxare" a adjustment_percent cand partea opusa e deja umpluta.
# ASIMETRIC intre BUY si SELL — pastrat exact ca in cod (nu se unifica).
RTRADE_BUY_DECAY_PCT = float(os.environ.get("RTRADE_BUY_DECAY_PCT", "0.005"))
RTRADE_SELL_DECAY_PCT = float(os.environ.get("RTRADE_SELL_DECAY_PCT", "0.01"))

# Baza ferestrei "hours" (impartita la failure_count) pt calea disperata
# (partea opusa deja umpluta). ASIMETRIC intre BUY (0.3) si SELL (0.23).
RTRADE_BUY_DESPERATE_HOURS_BASE = float(os.environ.get("RTRADE_BUY_DESPERATE_HOURS_BASE", "0.3"))
RTRADE_SELL_DESPERATE_HOURS_BASE = float(os.environ.get("RTRADE_SELL_DESPERATE_HOURS_BASE", "0.23"))

# safeback_seconds pt calea disperata (1h + 60s), comun BUY/SELL.
RTRADE_DESPERATE_SAFEBACK_SEC = float(os.environ.get("RTRADE_DESPERATE_SAFEBACK_SEC", str(1 * 3600 + 60)))

# "hours" pt calea normala (nimic disperat inca). ASIMETRIC — BUY asteapta mai
# mult (16h) decat SELL (12h).
RTRADE_BUY_NORMAL_HOURS = float(os.environ.get("RTRADE_BUY_NORMAL_HOURS", "16"))
RTRADE_SELL_NORMAL_HOURS = float(os.environ.get("RTRADE_SELL_NORMAL_HOURS", "12"))

# Ofset de pret (fractie) pt ordinul de urmarire "disperat" imediat dupa ce
# partea opusa s-a umplut (SELL la +X% dupa BUY umplut / BUY la -X% dupa SELL
# umplut) si fereastra "hours" a acelui ordin de urmarire. Comun ambelor directii.
RTRADE_FOLLOWUP_OFFSET_PCT = float(os.environ.get("RTRADE_FOLLOWUP_OFFSET_PCT", "0.01"))
RTRADE_FOLLOWUP_HOURS = float(os.environ.get("RTRADE_FOLLOWUP_HOURS", "2.7"))

# Toleranta are_close pt detectia "zi proasta" (pretul a trecut de referinta) si
# multiplicatorul aplicat lui adjustment_percent in acel caz. Comun ambelor directii.
RTRADE_BAD_DAY_TOLERANCE_PCT = float(os.environ.get("RTRADE_BAD_DAY_TOLERANCE_PCT", "0.1"))
RTRADE_BAD_DAY_MULTIPLIER = float(os.environ.get("RTRADE_BAD_DAY_MULTIPLIER", "1.7"))

# Epsilon ca sa evite diviziunea la zero in calculul ratei profit/pierdere.
RTRADE_ZERO_EPSILON = float(os.environ.get("RTRADE_ZERO_EPSILON", "0.0001"))

# Numarul maxim de esecuri acceptate inainte sa renunte la un ordin BUY/SELL.
RTRADE_MAX_FAILURES = int(os.environ.get("RTRADE_MAX_FAILURES", "10"))
# FILTRU DE TREND (11 aug): rtrade e un bot de spread -> pierde in trend CLAR (selectie
# adversa: in declin BUY-ul se umple, SELL-ul de flip nu, ajunge sa vanda desperat in pierdere;
# vazut pe TAO). Sta deoparte cand |gradient_recent| > K*epsilon (epsilon = podeaua de zgomot,
# auto-calibrata pe volatilitate din cacheManager) pe o fereastra scurta. Kill-switch + prag.
RTRADE_TREND_FILTER_ENABLED = os.environ.get("RTRADE_TREND_FILTER_ENABLED", "true").strip().lower() == "true"
RTRADE_TREND_FILTER_K = float(os.environ.get("RTRADE_TREND_FILTER_K", "2.0"))
RTRADE_TREND_WINDOW_SEC = float(os.environ.get("RTRADE_TREND_WINDOW_SEC", "900"))
RTRADE_DYNAMIC_MARKET_EXIT_MODE = os.environ.get(
    "RTRADE_DYNAMIC_MARKET_EXIT_MODE", "shadow").strip().lower()
RTRADE_EMERGENCY_HARD_STOP_PCT = float(os.environ.get(
    "RTRADE_EMERGENCY_HARD_STOP_PCT", "0.12"))

# Coordonatorul nou este candidat financiar si ramane OFF pana la replay/walk-forward.
# Cand este activ, un singur owner gestioneaza perechea si expunerea; calea veche cu
# doi workeri ramane intacta sub kill-switch.
RTRADE_PAIR_COORDINATOR_ENABLED = os.environ.get(
    "RTRADE_PAIR_COORDINATOR_ENABLED", "false").strip().lower() == "true"
RTRADE_PAIR_POLL_SEC = float(os.environ.get("RTRADE_PAIR_POLL_SEC", "1"))
RTRADE_PAIR_MAX_ACTIVE_ROUNDS = int(os.environ.get(
    "RTRADE_PAIR_MAX_ACTIVE_ROUNDS", "4"))
RTRADE_PAIR_START_INTERVAL_SEC = float(os.environ.get(
    "RTRADE_PAIR_START_INTERVAL_SEC", "8"))
RTRADE_PAIR_DIRECTIONS = tuple(
    side.strip().upper()
    for side in os.environ.get("RTRADE_PAIR_DIRECTIONS", "BUY,SELL").split(",")
    if side.strip()
)
RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC = float(os.environ.get(
    "RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC", "180"))
RTRADE_PLACE_FAILURE_BACKOFF_SEC = float(os.environ.get(
    "RTRADE_PLACE_FAILURE_BACKOFF_SEC", "180"))
RTRADE_FAST_FILL_RATIO = float(os.environ.get("RTRADE_FAST_FILL_RATIO", "0.25"))
RTRADE_MIN_EDGE_PCT = float(os.environ.get("RTRADE_MIN_EDGE_PCT", "0.0115"))
RTRADE_SHOCK_HARD_STOP_PCT = float(os.environ.get(
    "RTRADE_SHOCK_HARD_STOP_PCT", "0.04"))
RTRADE_HARD_STOP_PCT = float(os.environ.get("RTRADE_HARD_STOP_PCT", "0.08"))


class _LivePairVenue:
    """Adaptorul subtire dintre coordonatorul pur si Binance-ul curent."""

    def __init__(self, symbol, pair_store=None):
        self.symbol = symbol
        # Folosit numai pentru eliberarea cooldown-ului la cancel. Rundele isi
        # pastreaza separat starea financiara; aici este suficient un LRU mic.
        self._known_tickets = deque(maxlen=max(32, RTRADE_PAIR_MAX_ACTIVE_ROUNDS * 8))
        self._last_place_failures = {}
        provider_name = mkt.provider_name_for(symbol)
        self.provider_name = provider_name
        self.executor = mkt.provider_by_name(provider_name)
        if self.executor is None:
            raise RuntimeError(f"provider executor indisponibil pentru {symbol}")
        self.audited_executor = AuditedStrategyExecutor(
            self.executor, venue=provider_name)
        self.pair_store = pair_store

    def current_price(self):
        return mkt.get_current_price(self.symbol)

    def _ticket(self, order, side, requested_price, requested_qty, pair_id=None):
        if not order or order.get("orderId") is None:
            return None
        actual_price = float(order.get("price") or requested_price)
        actual_qty = float(order.get("origQty") or order.get("quantity") or requested_qty)
        return PairOrderTicket(
            order_id=str(order["orderId"]), side=side.upper(),
            price=actual_price, qty=actual_qty, pair_id=pair_id)

    def place_limit(self, side, price, qty, pair_id):
        side = side.upper()
        self._last_place_failures.pop(side, None)
        from providers.quantity import balance_cap_quantity
        available_qty, required_asset = balance_cap_quantity(
            self.executor.free_balance, self.symbol, side, price)
        if required_asset:
            # Nu compara cu qty cerut: pipeline-ul comun QuantityDecision + mecanica
            # providerului reduc deja ordinul la soldul permis. Backoff numai cand
            # activul necesar nu are deloc sold liber utilizabil.
            if available_qty is not None and float(available_qty) <= 1e-12:
                self._last_place_failures[side] = (
                    f"{side.lower()}_insufficient_funds:{required_asset}")
                print(
                    f"[{self.symbol}] {side} fonduri insuficiente: "
                    f"disponibil=0.00000000 {required_asset}")
                return None
        hours = (RTRADE_BUY_NORMAL_HOURS if side.upper() == "BUY"
                 else RTRADE_SELL_NORMAL_HOURS)
        client_id = rtrade_client_order_id(pair_id, side, "limit")
        if self.pair_store is not None:
            self.pair_store.intent(
                pair_id, side, price, qty, client_id, kind="limit",
                symbol=self.symbol, start_side=side)
        order = mkt.place(
            self.symbol, side, price, qty,
            force=False, cancelorders=False, hours=hours, smart=False,
            cooldown_pair_id=pair_id,
            # Coordonatorul detine retry/reconcile; outbox-ul global nu trebuie sa
            # recreeze ulterior un picior dintr-o pereche deja expirata.
            caller_owns_retry=True, motivation="rtrade_pair_quote",
            client_order_id=client_id)
        ticket = self._ticket(order, side, price, qty, pair_id=pair_id)
        if ticket is not None:
            if self.pair_store is not None:
                self.pair_store.accepted(
                    pair_id, side, ticket.order_id, kind="limit")
            self._known_tickets.append(ticket)
        return ticket

    def last_place_failure_reason(self, side):
        return self._last_place_failures.get(side.upper())

    def market_exit_allowed(self, exposure_side, loss_fraction, reason):
        """Decide daca hard-stop-ul normal are confirmare de trend pentru MARKET.

        ``shadow`` doar observa; ``live`` aplica decizia. Pragul emergency ramane
        obligatoriu indiferent de trend, ca un semnal stale sa nu lase risc nelimitat.
        """
        emergency = loss_fraction >= RTRADE_EMERGENCY_HARD_STOP_PCT
        decision = _market_regime_decision(self.symbol)
        adverse = decision.adverse_to(exposure_side)
        justified = adverse or emergency
        print(
            f"[{self.symbol}] dynamic-market-exit mode={RTRADE_DYNAMIC_MARKET_EXIT_MODE} "
            f"exposure={exposure_side} regime={decision.regime} "
            f"strength={decision.strength} signal_reason={decision.reason} "
            f"loss={loss_fraction:.4%} emergency={emergency} "
            f"would_market={justified} reason={reason}")
        if RTRADE_DYNAMIC_MARKET_EXIT_MODE in {"off", "shadow"}:
            return True
        return justified

    def place_market_exit(self, side, qty, reason, pair_id=None):
        """Iesire spot MARKET, rezervata reducerii unei expuneri hard-stop.

        Ordinele normale raman pe mkt.place/place_safe_order. Iesirea de risc
        sare profit/cooldown/weight, dar NU sare soldul, fee-cap-ul, precizia,
        preflight-ul sau auditul cu client-order-id.
        """
        side = side.upper()
        price = float(self.current_price() or 0.0)
        if price <= 0:
            print(f"[{self.symbol}] {side} hard-stop BLOCAT: pret indisponibil")
            return None
        decision = decide_quantity(
            self.executor, self.symbol, side, price, qty, apply_policy=False)
        final_qty = float(decision.final_qty)
        if final_qty <= 0:
            print(f"[{self.symbol}] {side} hard-stop BLOCAT: "
                  f"{decision.refuse_reason} asset={decision.balance_asset}")
            return None
        precision = self.audited_executor.pair_precision(self.symbol)
        if precision is not None:
            quantum = Decimal(1).scaleb(-int(precision.volume_decimals))
            final_qty = float(Decimal(str(final_qty)).quantize(quantum, rounding=ROUND_DOWN))
            if final_qty <= 0 or final_qty < float(precision.order_min or 0.0):
                print(f"[{self.symbol}] {side} hard-stop BLOCAT: qty {final_qty} "
                      f"sub minim {precision.order_min}")
                return None
        kind = f"rtrade:{reason}:{pair_id or 'unknown-pair'}"
        intent_id = f"rtrade-{pair_id or 'unknown'}-{reason}-{side.lower()}"
        client_id = rtrade_client_order_id(
            pair_id or "unknown", side, "hard_stop")
        if self.pair_store is not None and pair_id:
            self.pair_store.intent(
                pair_id, side, None, final_qty, client_id, kind="hard_stop",
                symbol=self.symbol)
        self.audited_executor.preflight_order(
            self.symbol, side, final_qty, price=None, market=True, kind=kind)
        order_id = self.audited_executor.submit_order_with_intent(
            intent_id, self.symbol, side, final_qty, price=None,
            market=True, kind=kind, reference_price=price,
            client_order_id=client_id)
        ticket = PairOrderTicket(
            order_id=str(order_id), side=side, price=price, qty=final_qty,
            pair_id=pair_id)
        self._known_tickets.append(ticket)
        if self.pair_store is not None and pair_id:
            self.pair_store.accepted(
                pair_id, side, ticket.order_id, kind="hard_stop")
        return ticket

    def order_status(self, order_id):
        status = self.executor.order_status(self.symbol, str(order_id))
        return PairOrderSnapshot(
            status=status.status, filled_qty=status.filled_qty,
            cost=status.cost, fee=status.fee)

    def cancel(self, order_id):
        try:
            self.executor.cancel_order(self.symbol, str(order_id))
            for ticket in getattr(self, "_known_tickets", []):
                if ticket.order_id == str(order_id) and ticket.pair_id:
                    from lock import trade_cooldown
                    trade_cooldown.release_pair_leg(
                        self.symbol, ticket.pair_id, ticket.side)
                    break
            return True
        except Exception as exc:  # noqa: BLE001 — coordonatorul decide fail-closed
            print(f"[{self.symbol}] pair cancel {order_id} esuat: {exc}")
            return False


def _trend_too_strong(symbol):
    """True daca `symbol` trend-uieste CLAR (|gradient_recent| > K*epsilon) -> rtrade (spread-bot)
    sta deoparte, sa nu fie prins de selectia adversa a trendului. Fail-OPEN: trend
    indisponibil/eroare -> False (nu blocheaza, la fel ca trend-wait din pipeline)."""
    if not RTRADE_TREND_FILTER_ENABLED:
        return False
    try:
        import cacheManager as cm
        dyn = cm.get_short_trend_manager().get_instant_trend_for_window(symbol, RTRADE_TREND_WINDOW_SEC)
    except Exception as e:  # noqa: BLE001 — gate oportunist, esec -> nu blocam tranzactionarea
        print(f"[{symbol}] rtrade trend-filter indisponibil ({e}) -> nu blochez")
        return False
    if not dyn:
        return False
    grad = abs(float(dyn.get("gradient_recent", 0.0) or 0.0))
    eps = abs(float(dyn.get("epsilon", 0.0) or 0.0))
    strong = eps > 0 and grad > RTRADE_TREND_FILTER_K * eps
    if strong:
        print(f"[{symbol}] rtrade STA DEOPARTE: trend clar (|grad|={grad:.4g} > "
              f"{RTRADE_TREND_FILTER_K}xeps={RTRADE_TREND_FILTER_K * eps:.4g}, fereastra {RTRADE_TREND_WINDOW_SEC:.0f}s)")
    return strong


def _market_regime_decision(symbol) -> MarketRegimeDecision:
    """Adapteaza sursa cache existenta la evaluatorul comun provider-neutral."""
    evaluator = MarketRegimeEvaluator(RTRADE_TREND_FILTER_K)
    if not RTRADE_TREND_FILTER_ENABLED:
        return evaluator.unknown("disabled")
    try:
        import cacheManager as cm
        dyn = cm.get_short_trend_manager().get_instant_trend_for_window(
            symbol, RTRADE_TREND_WINDOW_SEC)
    except Exception as exc:
        return evaluator.unknown(f"source_error:{exc.__class__.__name__}")
    return evaluator.evaluate(dyn)


def _place_failure_backoff(reason):
    """Intoarce (side, secunde) pentru un esec terminal de plasare.

    Fondurile complet absente au motiv explicit. Orice alt ``*_place_failed``
    (gard de pret, min-notional, weight-limit, API refuzat) trebuie de asemenea
    rarit: altfel coordonatorul creeaza un pair_id nou la fiecare interval de
    start si poate transforma o eroare tranzitorie intr-o bucla de ordine.
    """
    reason = str(reason or "").strip().lower()
    marker = "_insufficient_funds:"
    if marker in reason:
        side = reason.split(marker, 1)[0].upper()
        if side in {"BUY", "SELL"}:
            return side, RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC
    suffix = "_place_failed"
    if reason.endswith(suffix):
        side = reason[:-len(suffix)].upper()
        if side in {"BUY", "SELL"}:
            return side, RTRADE_PLACE_FAILURE_BACKOFF_SEC
    return None, 0.0


def _followup_force(symbol, side):
    """Pt ordinul de flip (followup) dupa un fill: FORCE (piata, flip imediat) DOAR daca
    trendul NU e ADVERS. Advers = SELL intr-un DECLIN clar (ar vinde jos) sau BUY intr-un
    URCUS clar (ar cumpara sus). Advers -> intoarce False = limita RABDATOARE la pretul de
    flip (se umple cand pretul revine, nu dumpeaza la piata). Trend slab/plat/favorabil sau
    indisponibil, ori kill-switch off -> True (piata, ca inainte). Astfel, chiar si cand se
    epuizeaza (disperat), rtrade NU vinde la piata / nu cumpara disperat impotriva trendului."""
    if not RTRADE_TREND_FILTER_ENABLED:
        return True
    try:
        import cacheManager as cm
        dyn = cm.get_short_trend_manager().get_instant_trend_for_window(symbol, RTRADE_TREND_WINDOW_SEC)
    except Exception:  # noqa: BLE001 — indisponibil -> comportament vechi (force)
        return True
    if not dyn:
        return True
    grad = float(dyn.get("gradient_recent", 0.0) or 0.0)
    eps = abs(float(dyn.get("epsilon", 0.0) or 0.0))
    if eps <= 0 or abs(grad) <= RTRADE_TREND_FILTER_K * eps:
        return True   # trend slab/plat -> flip imediat e ok
    su = (side or "").upper()
    adverse = (su == "SELL" and grad < 0) or (su == "BUY" and grad > 0)
    if adverse:
        print(f"[{symbol}] followup {su}: trend ADVERS (grad={grad:.4g}) -> limita rabdatoare, NU piata")
        return False
    return True


class TradingBot:
    def __init__(self, symbol, qty, DEFAULT_ADJUSTMENT_PERCENT):
        self.symbol = symbol
        self.qty = qty
        self.transaction_state = "COMPLETED"  # Starea inițială
        current_price = api.get_current_price(symbol)
        self.filled_buy_price = round(current_price * (1 - RTRADE_INITIAL_SPREAD_PCT), 4)
        self.filled_sell_price = round(current_price * (1 + RTRADE_INITIAL_SPREAD_PCT), 4)
        self.buy_filled = False
        self.sell_filled = False
        self.DEFAULT_ADJUSTMENT_PERCENT = DEFAULT_ADJUSTMENT_PERCENT
        self.lock = threading.Lock()  # Lock pentru sincronizare

    @property
    def is_buy_filled(self):
        with self.lock:
            return self.buy_filled

    @property
    def is_sell_filled(self):
        with self.lock:
            return self.sell_filled
        
    def mark_buy_filled(self, filled_buy_price=None):
        with self.lock:
            self.buy_filled = True
            self.sell_filled = False
            if filled_buy_price:
                self.filled_buy_price = filled_buy_price
            return self.filled_buy_price

    def mark_sell_filled(self, filled_sell_price=None):
        with self.lock:
            self.buy_filled = False
            self.sell_filled = True
            if filled_sell_price:
                self.filled_sell_price = filled_sell_price
            return self.filled_sell_price
        
    def repetitive_buy(self, current_price, filled_sell_price):
        adjustment_percent = self.DEFAULT_ADJUSTMENT_PERCENT
        failure_count = 1  # Adaugăm un contor pentru numărul de eșecuri
        max_failures = RTRADE_MAX_FAILURES  # Definim numărul maxim de eșecuri acceptabile

        while True:

            current_price = api.get_current_price(self.symbol)

            if self.is_sell_filled:
                adjustment_percent = max(MIN_adjustment_percent, adjustment_percent - adjustment_percent * RTRADE_BUY_DECAY_PCT)

            target_buy_price = round(current_price * (1 - adjustment_percent), 4)
            print(f"[{self.symbol}] Order BUY initiated at {target_buy_price:.2f} procent {adjustment_percent}%")

            if self.is_buy_filled:
                print(f"[{self.symbol}] Ignore BUY order. It was previously filled at {self.filled_buy_price:.2f}")
                return self.mark_buy_filled(self.filled_buy_price)

            buy_order = None
            h = RTRADE_BUY_DESPERATE_HOURS_BASE / failure_count
            try:
                if self.is_sell_filled: # sunt disperat
                    if adjustment_percent == MIN_adjustment_percent:
                        print(f"[{self.symbol}] sunt disperat!")
                        buy_order = mkt.place(self.symbol, "BUY", target_buy_price, self.qty,
                            safeback_seconds=RTRADE_DESPERATE_SAFEBACK_SEC, force=False, cancelorders=True, hours=h, smart=False)
                    else:
                        buy_order = mkt.place(self.symbol, "BUY", target_buy_price, self.qty,
                            safeback_seconds=RTRADE_DESPERATE_SAFEBACK_SEC, force=False, cancelorders=True, hours=h, smart=False)
                else:
                    buy_order = mkt.place(self.symbol, "BUY", target_buy_price, self.qty, cancelorders=True, hours=RTRADE_BUY_NORMAL_HOURS, smart=False)
            except po.WeightLimitBlock as e:
                print(f"[{self.symbol}] Limita 24h atinsă — ies fără retry ({e})")
                return None

            if buy_order is None:
                print(f"[{self.symbol}] Order BUY failed, retryed {failure_count} times. Retrying again ...")
                time.sleep(WAIT_FOR_ORDER)
                failure_count += 1
                if failure_count > max_failures:
                    print(f"[{self.symbol}] Order BUY failed {failure_count} times. Exiting.")
                    return None
                continue

            failure_count = 1 # reset failure count after a successful order placement
            
            time.sleep(WAIT_FOR_ORDER)
            order_id = buy_order['orderId']
            self.filled_buy_price = round(float(buy_order['price']), 4)
            
            if api.check_order_filled(order_id, self.symbol):
                print(f"[{self.symbol}] BUY order filled at {self.filled_buy_price:.2f}")
                print(f"[{self.symbol}] SELL disperat tot 1....")
                mkt.place(self.symbol, "SELL", api.get_current_price(self.symbol) * (1 + RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                    force=_followup_force(self.symbol, "SELL"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                return self.mark_buy_filled(self.filled_buy_price)


            filled_buy_price = api.check_order_filled_by_time("BUY", self.symbol, time_back_in_seconds=WAIT_FOR_ORDER)
            if filled_buy_price is not None:
                print(f"[{self.symbol}] BUY order may have been filled :-) at {filled_buy_price:.2f}")
                print(f"[{self.symbol}] SELL disperat tot 2 ....")
                mkt.place(self.symbol, "SELL", api.get_current_price(self.symbol) * (1 + RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                    force=_followup_force(self.symbol, "SELL"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                return self.mark_buy_filled(filled_buy_price)

            current_price = api.get_current_price(self.symbol)
            if current_price > filled_sell_price and not u.are_close(current_price, filled_sell_price, RTRADE_BAD_DAY_TOLERANCE_PCT):
                print(f"[{self.symbol}] Bed day :-(. Trying BUY at current price - x2 {current_price:.2f}")
                adjustment_percent = RTRADE_BAD_DAY_MULTIPLIER * self.DEFAULT_ADJUSTMENT_PERCENT
            # if arrived here it means
            # current order was not filled , so try cancel and retry in the loop
            if not api.cancel_order(self.symbol, order_id):
                if api.check_order_filled(order_id, self.symbol):
                    print(f"[{self.symbol}] Cancel BUY order failed. Maybe it was filled :-)? Moving to SELL ...")
                    print(f"[{self.symbol}] SELL disperat tot 3 ....")
                    mkt.place(self.symbol, "SELL", api.get_current_price(self.symbol) * (1 + RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                    force=_followup_force(self.symbol, "SELL"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                    return self.mark_buy_filled(self.filled_buy_price)
                else:
                    print(f"[{self.symbol}] Cancel BUY order failed. Someone canceled it. Continuing BUY...")


    def repetitive_sell(self, current_price, filled_buy_price):
        adjustment_percent = self.DEFAULT_ADJUSTMENT_PERCENT
        failure_count = 1  # Adaugăm un contor pentru numărul de eșecuri
        max_failures = RTRADE_MAX_FAILURES  # Definim numărul maxim de eșecuri acceptabile

        while True:

            current_price = api.get_current_price(self.symbol)

            if self.is_buy_filled:
                adjustment_percent = max(MIN_adjustment_percent, adjustment_percent - adjustment_percent * RTRADE_SELL_DECAY_PCT)

            target_sell_price = round(current_price * (1 + adjustment_percent), 4)
            print(f"[{self.symbol}] Order SELL initiated at {target_sell_price:.2f} procent {adjustment_percent}%")

            if self.is_sell_filled:
                print(f"[{self.symbol}] Ignore SELL order. It was previously filled at {self.filled_sell_price:.2f}")
                return self.mark_sell_filled(self.filled_sell_price)

            sell_order = None
            h = RTRADE_SELL_DESPERATE_HOURS_BASE / failure_count
            try:
                if self.is_buy_filled: # sunt disperat
                    if adjustment_percent == MIN_adjustment_percent:
                        print(f"[{self.symbol}] sunt disperat!")
                        sell_order = mkt.place(self.symbol, "SELL", target_sell_price, self.qty,
                            safeback_seconds=RTRADE_DESPERATE_SAFEBACK_SEC, force=False, cancelorders=True, hours=h, smart=False)
                    else:
                        sell_order = mkt.place(self.symbol, "SELL", target_sell_price, self.qty,
                            safeback_seconds=RTRADE_DESPERATE_SAFEBACK_SEC, force=False, cancelorders=True, hours=h, smart=False)
                else:
                    sell_order = mkt.place(self.symbol, "SELL", target_sell_price, self.qty, cancelorders=True, hours=RTRADE_SELL_NORMAL_HOURS, smart=False)
            except po.WeightLimitBlock as e:
                print(f"[{self.symbol}] Limita 24h atinsă (SELL) — ies fără retry ({e})")
                return None

            if sell_order is None:
                print(f"[{self.symbol}] Order SELL failed, retryed {failure_count} times. Retrying again ...")
                time.sleep(WAIT_FOR_ORDER)
                failure_count += 1
                if failure_count > max_failures:
                    print(f"[{self.symbol}] Order SELL failed {failure_count} times. Exiting.")
                    return None
                continue
            
            failure_count = 1 # reset failure count after a successful order placement

            time.sleep(WAIT_FOR_ORDER)
            order_id = sell_order['orderId']
            self.filled_sell_price = round(float(sell_order['price']), 4)

            if api.check_order_filled(order_id, self.symbol):
                print(f"[{self.symbol}] SELL order filled at {self.filled_sell_price:.2f}")
                print(f"[{self.symbol}] BUY disperat tot 1....")
                mkt.place(self.symbol, "BUY", api.get_current_price(self.symbol) * (1 - RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                    force=_followup_force(self.symbol, "BUY"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                return self.mark_sell_filled(self.filled_sell_price)


            filled_sell_price = api.check_order_filled_by_time("SELL", self.symbol, time_back_in_seconds=WAIT_FOR_ORDER)
            if filled_sell_price is not None:
                print(f"[{self.symbol}] SELL order may have been filled :-) at {filled_sell_price:.2f}")
                print(f"[{self.symbol}] BUY disperat tot 2....")
                mkt.place(self.symbol, "BUY", api.get_current_price(self.symbol) * (1 - RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                    force=_followup_force(self.symbol, "BUY"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                return self.mark_sell_filled(filled_sell_price)

            current_price = api.get_current_price(self.symbol)
            if current_price < filled_buy_price and not u.are_close(current_price, filled_buy_price, RTRADE_BAD_DAY_TOLERANCE_PCT):
                print(f"[{self.symbol}] Bed day :-(. Trying SELL at current price + x2 {current_price:.2f}")
                adjustment_percent = RTRADE_BAD_DAY_MULTIPLIER * self.DEFAULT_ADJUSTMENT_PERCENT
            # if arrived here it means
            # current order was not filled , so try cancel and retry in the loop
            if not api.cancel_order(self.symbol, order_id):
                if api.check_order_filled(order_id, self.symbol):
                    print(f"[{self.symbol}] Cancel SELL order failed. Maybe it was filled :-)? Moving to BUY ...")
                    print(f"[{self.symbol}] BUY disperat tot 3....")
                    mkt.place(self.symbol, "BUY", api.get_current_price(self.symbol) * (1 - RTRADE_FOLLOWUP_OFFSET_PCT), self.qty,
                        force=_followup_force(self.symbol, "BUY"), cancelorders=True, hours=RTRADE_FOLLOWUP_HOURS)
                    return self.mark_sell_filled(self.filled_sell_price)
                else:
                    print(f"[{self.symbol}] Cancel SELL order failed. Someone canceled it. Continuing sell...")

    def _run_pair(self, executor, current_price):
        """Ruleaza cele doua laturi concurent pe workerii persistenti ai botului.

        Future.result() propaga exceptiile workerilor in bucla principala, unde exista
        deja reconcilierea defensiva prin anularea ordinelor recente. Varianta veche
        pornea doua Thread-uri la fiecare runda si pierdea exceptiile in stderr.
        """
        buy_future = executor.submit(
            self.repetitive_buy, current_price, self.filled_sell_price)
        sell_future = executor.submit(
            self.repetitive_sell, current_price, self.filled_buy_price)
        # Asteapta ambele laturi inainte sa propage o exceptie. Daca am apela
        # buy_future.result() imediat, un BUY esuat rapid ar lasa SELL-ul vechi
        # activ, iar bucla principala ar putea pune o runda noua peste el.
        wait((buy_future, sell_future))
        return buy_future.result(), sell_future.result()

    def _run_coordinator_forever(self):
        pair_store = getattr(self, "pair_store", None) or RTradePairStore()
        venue = _LivePairVenue(self.symbol, pair_store=pair_store)
        policy = PairPolicy(
            adjustment_fraction=self.DEFAULT_ADJUSTMENT_PERCENT,
            quote_ttl_sec=WAIT_FOR_ORDER,
            poll_sec=RTRADE_PAIR_POLL_SEC,
            fast_fill_ratio=RTRADE_FAST_FILL_RATIO,
            min_edge_fraction=RTRADE_MIN_EDGE_PCT,
            shock_hard_stop_fraction=RTRADE_SHOCK_HARD_STOP_PCT,
            hard_stop_fraction=RTRADE_HARD_STOP_PCT,
        )
        if RTRADE_PAIR_MAX_ACTIVE_ROUNDS < 1:
            raise ValueError("RTRADE_PAIR_MAX_ACTIVE_ROUNDS trebuie sa fie >= 1")
        if RTRADE_PAIR_START_INTERVAL_SEC <= 0:
            raise ValueError("RTRADE_PAIR_START_INTERVAL_SEC trebuie sa fie > 0")
        if (not RTRADE_PAIR_DIRECTIONS
                or any(side not in {"BUY", "SELL"} for side in RTRADE_PAIR_DIRECTIONS)):
            raise ValueError("RTRADE_PAIR_DIRECTIONS accepta numai BUY,SELL")
        if RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC <= 0:
            raise ValueError("RTRADE_INSUFFICIENT_FUNDS_BACKOFF_SEC trebuie sa fie > 0")
        if RTRADE_PLACE_FAILURE_BACKOFF_SEC <= 0:
            raise ValueError("RTRADE_PLACE_FAILURE_BACKOFF_SEC trebuie sa fie > 0")
        if RTRADE_DYNAMIC_MARKET_EXIT_MODE not in {"off", "shadow", "live"}:
            raise ValueError("RTRADE_DYNAMIC_MARKET_EXIT_MODE: off|shadow|live")
        if not RTRADE_HARD_STOP_PCT <= RTRADE_EMERGENCY_HARD_STOP_PCT < 1:
            raise ValueError(
                "RTRADE_EMERGENCY_HARD_STOP_PCT trebuie sa fie >= hard-stop si < 1")

        # Fiecare coordonator detine exclusiv order-id-urile si inventarul unei
        # runde. O runda expusa continua sa-si urmareasca exit-ul, dar nu mai
        # blocheaza lansarea altor runde pe acelasi simbol pana la limita setata.
        active = []
        recovery_blocked = False
        for record in pair_store.active(self.symbol):
            state = record.get("state")
            if not state:
                try:
                    tickets = []
                    snapshots = {}
                    for intent in record.get("intents", {}).values():
                        order = venue.executor.order_by_client_id(
                            self.symbol, intent["client_order_id"])
                        if not order:
                            # Intentia a fost fsync-uit-a inainte de submit. Daca
                            # procesul a murit exact in acea fereastra, retrimiterea
                            # cu acelasi clientOrderId este idempotenta la Binance.
                            # Pipeline-ul normal pastreaza toate guard-urile si
                            # reconcilierea de cantitate.
                            if intent.get("kind", "limit") != "limit":
                                continue
                            ticket = venue.place_limit(
                                intent["side"], float(intent["price"]),
                                float(intent["qty"]), record["pair_id"])
                            if ticket is None:
                                # Daca submit-ul a ajuns la venue, dar raspunsul
                                # s-a pierdut/SDK-ul a intors None, ordinul poate
                                # exista totusi. Reconciliem inca o data inainte
                                # sa declaram intentia neplasata.
                                order = venue.executor.order_by_client_id(
                                    self.symbol, intent["client_order_id"])
                                if not order:
                                    continue
                            else:
                                order = venue.executor.order_by_client_id(
                                    self.symbol, intent["client_order_id"])
                            if not order:
                                # Unele implementari provider nu expun imediat
                                # lookup-ul dupa client id; ticket-ul confirmat este
                                # suficient pentru reconstructia aceleiasi runde.
                                order = {
                                    "orderId": ticket.order_id,
                                    "price": ticket.price,
                                    "origQty": ticket.qty,
                                }
                        order_id = str(order["orderId"])
                        pair_store.accepted(
                            record["pair_id"], intent["side"], order_id,
                            kind=intent.get("kind", "limit"))
                        snap = venue.order_status(order_id)
                        snapshots[order_id] = vars(snap).copy()
                        tickets.append({
                            "order_id": order_id, "side": intent["side"],
                            "price": float(order.get("price") or intent.get("price") or 0),
                            "qty": float(order.get("origQty") or intent["qty"]),
                            "active": snap.status not in {"closed", "canceled", "expired"},
                            "pair_id": record["pair_id"],
                        })
                    if not tickets:
                        terminal_state = {
                            "pair_id": record["pair_id"], "qty": record["qty"],
                            "start_side": record["start_side"], "phase": "failed",
                            "reason": "recovery_intent_not_submitted", "shock": False,
                            "elapsed_sec": 0, "first_fill_elapsed_sec": None,
                            "first_fill_side": None, "tickets": [], "snapshots": {},
                        }
                        pair_store.checkpoint(
                            record["pair_id"], terminal_state, terminal=True)
                        print(f"[{self.symbol}] pair={record['pair_id']} recovery: "
                              "intentia nu a putut fi plasata; inchisa controlat")
                        continue
                    state = {
                        "pair_id": record["pair_id"], "qty": record["qty"],
                        "start_side": record["start_side"], "phase": "quoting",
                        "reason": "recovered_by_client_order_id", "shock": False,
                        "elapsed_sec": max(0.0, time.time() - record["created_ts"]),
                        "first_fill_elapsed_sec": None, "first_fill_side": None,
                        "tickets": tickets, "snapshots": snapshots,
                    }
                    pair_store.checkpoint(record["pair_id"], state, terminal=False)
                except Exception as exc:
                    print(f"[{self.symbol}] RECOVERY BLOCAT: pair={record.get('pair_id')} {exc}")
                    recovery_blocked = True
                    continue
            try:
                coordinator = PairCoordinator.from_state(venue, policy, state)
                active.append(coordinator)
                print(f"[{self.symbol}] pair={coordinator.pair_id} adoptat "
                      f"phase={coordinator.phase} tickets={len(coordinator.tickets)}")
            except Exception as exc:
                print(f"[{self.symbol}] RECOVERY BLOCAT: {exc}")
                recovery_blocked = True

        try:
            known_client_ids = {
                intent.get("client_order_id")
                for rec in pair_store.active(self.symbol)
                for intent in rec.get("intents", {}).values()
            }
            orphan_orders = [
                order for order in venue.executor.open_orders(self.symbol)
                if str(order.get("clientOrderId") or "").startswith("RT_")
                and order.get("clientOrderId") not in known_client_ids
            ]
            for order in orphan_orders:
                order_id = str(order["orderId"])
                client_id = order.get("clientOrderId")
                # Un ordin RT_ fara intentie locala nu poate fi asociat sigur
                # unei runde (ID-ul este hash-uit). Il anulam, nu inventam stare.
                venue.executor.cancel_order(self.symbol, order_id)
                print(f"[{self.symbol}] recovery: ordin RT_ orfan anulat "
                      f"order_id={order_id} client_id={client_id}")
            if orphan_orders:
                remaining = {
                    str(order.get("orderId"))
                    for order in venue.executor.open_orders(self.symbol)
                }
                not_canceled = [
                    str(order["orderId"]) for order in orphan_orders
                    if str(order["orderId"]) in remaining
                ]
                if not_canceled:
                    print(f"[{self.symbol}] RECOVERY BLOCAT: anulare neconfirmata "
                          f"pentru ordinele RT_ {not_canceled}")
                    recovery_blocked = True
        except Exception as exc:
            print(f"[{self.symbol}] RECOVERY BLOCAT: inventar exchange indisponibil ({exc})")
            recovery_blocked = True
        last_start_at = float("-inf")
        next_direction = 0
        side_backoff_until = {"BUY": 0.0, "SELL": 0.0}
        while True:
            try:
                now = time.monotonic()
                survivors = []
                checkpoints = []
                for coordinator in active:
                    outcome = coordinator.step(now=now)
                    export_state = getattr(coordinator, "export_state", None)
                    if callable(export_state):
                        checkpoints.append((
                            coordinator.pair_id, export_state(), outcome.terminal))
                    if outcome.terminal:
                        print(
                            f"[{self.symbol}] pair={outcome.pair_id} "
                            f"phase={outcome.phase} shock={outcome.shock} "
                            f"latency={outcome.fill_latency_sec} "
                            f"buy={outcome.buy_qty:.6f} sell={outcome.sell_qty:.6f} "
                            f"net={outcome.net_qty:.6f} "
                            f"cashflow={outcome.gross_pnl:.2f} "
                            f"fees={outcome.fees:.2f} reason={outcome.reason}")
                    else:
                        survivors.append(coordinator)
                pair_store.checkpoint_many(checkpoints)
                active = survivors

                can_start = (
                    not recovery_blocked
                    and
                    len(active) < RTRADE_PAIR_MAX_ACTIVE_ROUNDS
                    and now - last_start_at >= RTRADE_PAIR_START_INTERVAL_SEC
                )
                if can_start and not _trend_too_strong(self.symbol):
                    current_price = venue.current_price()
                    if current_price is not None:
                        start_side = None
                        for _ in range(len(RTRADE_PAIR_DIRECTIONS)):
                            candidate = RTRADE_PAIR_DIRECTIONS[next_direction]
                            next_direction = (
                                next_direction + 1) % len(RTRADE_PAIR_DIRECTIONS)
                            if now >= side_backoff_until[candidate]:
                                start_side = candidate
                                break
                        if start_side is None:
                            time.sleep(RTRADE_PAIR_POLL_SEC)
                            continue
                        round_qty = RTRADE_NOTIONAL_USDC / float(current_price)
                        reserved_pair_id = uuid.uuid4().hex
                        coordinator = PairCoordinator(
                            venue, round_qty, policy, start_side=start_side)
                        outcome = coordinator.start(
                            current_price, pair_id=reserved_pair_id)
                        export_state = getattr(coordinator, "export_state", None)
                        if callable(export_state):
                            pair_store.checkpoint(
                                coordinator.pair_id, export_state(),
                                terminal=outcome.terminal)
                        last_start_at = now
                        if outcome.terminal:
                            failed_side, backoff_sec = _place_failure_backoff(
                                outcome.reason)
                            if failed_side in side_backoff_until:
                                side_backoff_until[failed_side] = now + backoff_sec
                                print(
                                    f"[{self.symbol}] {failed_side} backoff "
                                    f"{backoff_sec:.0f}s dupa esec de plasare "
                                    f"({outcome.reason})")
                            print(
                                f"[{self.symbol}] pair={outcome.pair_id} "
                                f"direction={start_side}-first "
                                f"phase={outcome.phase} reason={outcome.reason}")
                        else:
                            active.append(coordinator)
                            print(
                                f"[{self.symbol}] pair={outcome.pair_id} started "
                                f"direction={start_side}-first "
                                f"active={len(active)}/"
                                f"{RTRADE_PAIR_MAX_ACTIVE_ROUNDS}")
                time.sleep(RTRADE_PAIR_POLL_SEC)
            except Exception as exc:  # noqa: BLE001 — rundele raman pentru reconciliere
                print(f"[{self.symbol}] pair coordinator error: {exc}")
                # Nu anulam global ordinele recente: intr-un registru multi-runda
                # asta ar putea distruge picioarele valide ale altor pair_id-uri.
                time.sleep(RTRADE_PAIR_POLL_SEC)

    def run(self):
        if RTRADE_PAIR_COORDINATOR_ENABLED:
            return self._run_coordinator_forever()
        # Exact doi workeri per bot, reutilizati intre runde. Operatiile BUY/SELL raman
        # concurente; se elimina doar churn-ul de Thread-uri si pierderea exceptiilor.
        prefix = f"rtrade-{self.symbol}"
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix=prefix) as executor:
            while True:
                try:
                    current_price = api.get_current_price(self.symbol)
                    if current_price is None:
                        print(f"[{self.symbol}] Failed to fetch current price. Retrying in {WAIT_FOR_ORDER} seconds...")
                        time.sleep(WAIT_FOR_ORDER)
                        continue
                    print(f"[{self.symbol}] Current price: {current_price:.2f}")

                    # FILTRU DE TREND: daca activul trend-uieste clar, rtrade (spread-bot) sta
                    # deoparte tot ciclul (nu prinde cutitul). Reia la urmatoarea iteratie.
                    if _trend_too_strong(self.symbol):
                        time.sleep(WAIT_FOR_ORDER)
                        continue

                    buy_result, sell_result = self._run_pair(executor, current_price)

                    if not buy_result or not sell_result:
                        continue

                    filled_buy_price = buy_result + RTRADE_ZERO_EPSILON  # avoid zero
                    filled_sell_price = sell_result

                    print(f"[{self.symbol}] Transaction complete: Bought at {filled_buy_price:.2f}, Sold at {filled_sell_price:.2f}")
                    if filled_buy_price < filled_sell_price:
                        print(f"[{self.symbol}] PROFIT: Profit ratio {filled_sell_price / filled_buy_price:.2f}")
                    else:
                        print(f"[{self.symbol}] LOSS: Loss ratio {filled_sell_price / filled_buy_price:.2f}")

                    time.sleep(1)

                    # Reset pentru următoarea rundă
                    with self.lock:
                        self.buy_filled = self.sell_filled = False
                except Exception as e:
                    print(f"[{self.symbol}] Unexpected error: {e}")
                    # Exceptiile workerilor ajung aici prin Future.result().
                    api.cancel_recent_orders("SELL", self.symbol, WAIT_FOR_ORDER)
                    api.cancel_recent_orders("BUY", self.symbol, WAIT_FOR_ORDER)
                    time.sleep(1)
                
                
_default_adj = round(u.calculate_difference_percent(60000, 60000 - 380) / 100, 4)
DEFAULT_ADJUSTMENT_PERCENT = float(os.environ.get("RTRADE_DEFAULT_ADJUSTMENT_PCT", str(_default_adj)))
print(f"[INFO] DEFAULT_ADJUSTMENT_PERCENT = {DEFAULT_ADJUSTMENT_PERCENT}")

# 23 iul: blocul de pornire efectiva (WS + instantiere bot + bot.run(), care e o
# bucla LIVE infinita cu ordine reale) mutat sub __name__=="__main__" — inainte
# rula necondiionat la IMPORT, ceea ce ar fi pornit tranzactionarea live doar prin
# `import rtrade` (ex. dintr-un test). Nimic altceva nu importa acest modul azi
# (verificat prin grep) si flota_start.sh il ruleaza cu `python rtrade.py`, deci
# comportamentul de PRODUCTIE ramane identic — doar importul devine sigur.
if __name__ == "__main__":
    # WS user-data bridge explicit: rtrade plasează ordine prin place_order_smart,
    # care verifică intern istoricul de orders/trades (guard-uri max_daily_trades,
    # politica zilnică de cantitate). WS ține acel cache proaspăt (altfel doar polling la 3 min).
    import cacheManager as cm
    cm.enable_real_ws_event_sync()

    initial_price = float(api.get_current_price(sym.taosymbol) or 0.0)
    if initial_price <= 0:
        raise RuntimeError(f"Pret indisponibil pentru {sym.taosymbol}")
    initial_qty = RTRADE_NOTIONAL_USDC / initial_price
    bot = TradingBot(sym.taosymbol, initial_qty,
                     DEFAULT_ADJUSTMENT_PERCENT=DEFAULT_ADJUSTMENT_PERCENT)
    #bot = TradingBot(sym.taosymbol, api.quantities[sym.taosymbol], DEFAULT_ADJUSTMENT_PERCENT=DEFAULT_ADJUSTMENT_PERCENT)
    bot.run()

    
