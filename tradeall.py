import os
import time
import datetime
import json
import math
import threading
from binance.exceptions import BinanceAPIException
from collections import deque

#my imports
import log
import alertnotifiers as alert
import utils as u
import symbols as sym
from binance_api import bapi as api
from binance_api import bapi_placeorder as po

from binance_api import bapi_trades as apitrades
from binance_api import bapi_allorders as apiorders

#import priceprediction as pp
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import linregress
from sklearn.preprocessing import PolynomialFeatures
from sklearn.linear_model import LinearRegression


import generateweb as web

from pricewindow import (PriceTrendAnalyzer, PriceWindow, WindowAnalyzer,
                         RECENT_GRADIENT_SECONDS,
                         WINDOW_SECONDS_SMALL, WINDOW_SECONDS_BIG)


TIME_SLEEP_GET_PRICE = 0.8       # seconds to sleep for price collection — valoare nominală
EXP_TIME_BUY_ORDER = (2.6 * 60) * 60 # dupa 1.6 ore
EXP_TIME_SELL_ORDER = EXP_TIME_BUY_ORDER
TIME_SLEEP_EVALUATE = TIME_SLEEP_GET_PRICE + 60  # seconds to sleep for buy/sell evaluation
# am voie 6 ordere per perioada de expirare care este 2.6 ore. deaceea am impartit la 6
TIME_SLEEP_PLACE_ORDER = TIME_SLEEP_EVALUATE + EXP_TIME_SELL_ORDER/ 6 + 4*79  # seconds to sleep for order placement

SELL_BUY_THRESHOLD = 5  # Threshold for the number of consecutive signals

TREND_TO_BE_OLD_SECONDS = 60 * 60 * 1.9
PRICE_CHANGE_THRESHOLD_EUR = u.calculate_difference_percent(60000, 60000 - 310)
PRICE_CHANGE_THRESHOLD_BIG_EUR = u.calculate_difference_percent(97000, 95000 - 377)

# 22 iul: cooldown per instanta de trend (investigat pe date reale, 21-22 iul,
# 7 experimente in research/tradeall_trigger_gate/) — logic() nu avea niciun
# "fire o singura data": refirerea se intampla la FIECARE evaluare cat timp
# trend_state ramane validat, chiar daca nimic nou nu s-a intamplat (gasit pe
# TAO: 186 BUY/0 SELL dintr-un singur trend). Testat: cooldown-ul REDUCE
# tranzactiile la cateva pe simbol si transforma un rezultat sub buy&hold
# (-57.56$) intr-unul care il bate (-1.0$) — FARA sa schimbe deloc conditiile
# de start/confirmare de mai jos.
FIRE_MIN_RETRY_INTERVAL_SEC = 30 * 60  # interval minim intre incercari RESPINSE (gate/weight-limit/buget)

DECISIONS_LOG_DIR = "logger"


def _sanitize_field(value):
    """Elimina caractere care ar sparge formatul pipe-delimited (A3)."""
    return str(value).replace("|", "/").replace("\n", " ")


def log_decision(symbol, event, **fields):
    """Jurnal CONDENSAT (doar trend_start): un rand pipe-delimited per
    tranzitie reala de trend, rotit zilnic ca restul din logger/.
    Observational — nu influenteaza logica de trading."""
    try:
        os.makedirs(DECISIONS_LOG_DIR, exist_ok=True)
        path = os.path.join(DECISIONS_LOG_DIR,
                             f"tradeall_decisions_{datetime.date.today().isoformat()}.log")
        cols = [time.time(), symbol, event,
                fields.get("state", ""), fields.get("old_state", ""), fields.get("price", ""),
                fields.get("prev_confirm_count", "")]
        line = "|".join(_sanitize_field(c) for c in cols)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print(f"[log_decision] eroare scriere jurnal decizii: {e}")


# ── KALMAN GATE (aprobat 19 iul): Kalman decide daca ordinele modelului actual
# ajung la bani reali. Moduri (env KALMAN_GATE_MODE, citit la fiecare ordin):
#   strict     BUY doar pe kalman UP, SELL doar pe kalman DOWN (default, aprobat)
#   permissive blocheaza DOAR contra-trend (BUY pe DOWN / SELL pe UP)
#   off        gate dezactivat (comportamentul dinainte)
# FAIL-OPEN: daca shadow-ul lipseste/e vechi (>5 min), ordinul TRECE (gate-ul
# nu are voie sa opreasca tradingul din cauza unei defectiuni de semnal).
_shadow_ref = None                      # setat de TrendCoordinator.__init__
GATE_OUTCOME_LOG = None                 # backtestul il redirectioneaza (nu scrie in jurnalul live)
GATE_STALE_SEC = 300


# Mod per simbol (env KALMAN_GATE_MODE_<SIMBOL> > env global > acest dict > strict).
# TAO permissive (A/B 4 zile): kalman e aproape mereu FLAT pe TAO (zgomot cuantizat
# la 0.1$ -> incertitudine mare) — un veto strict pe un semnal neinformativ ar opri
# complet BUY-urile TAO; permissive blocheaza doar contra-trend (DOWN confirmat).
GATE_MODE_DEFAULTS = {"TAOUSDC": "permissive"}

# KALMAN-PRIMAR (19 iul, pe cifre A/B 4 zile: BTC net +6.62$ vs 0$ model actual
# si -3.97$ buy&hold): Kalman INITIAZA ordine la tranzitii, DOAR pe simbolurile
# de mai jos. Iesirile raman pe mecanismele existente ale flotei (monitortrades/
# trailing/profit-guard); ordinele trec prin _fire_order => TOATE garzile
# (weight-limit, buget zilnic, cooldown, profit-guard) + gate-ul raman active.
# Env: KALMAN_PRIMARY_SYMBOLS="BTCUSDC,TAOUSDC" sau "" (dezactivat).
KALMAN_PRIMARY_SYMBOLS = set(
    s.strip() for s in os.environ.get("KALMAN_PRIMARY_SYMBOLS", "BTCUSDC").split(",") if s.strip())


def _kalman_gate_blocks(symbol, action):
    mode = (os.environ.get(f"KALMAN_GATE_MODE_{symbol}")
            or os.environ.get("KALMAN_GATE_MODE")
            or GATE_MODE_DEFAULTS.get(symbol, "strict")).strip().lower()
    if mode == "off" or _shadow_ref is None:
        return False, mode, None
    try:
        trend, age = _shadow_ref.current_trend(symbol)
    except Exception:
        return False, mode, None
    if trend is None or age > GATE_STALE_SEC:
        return False, mode, None        # fail-open pe semnal absent/vechi
    wanted = 1 if action == "BUY" else -1
    if mode == "permissive":
        return trend == -wanted, mode, trend
    return trend != wanted, mode, trend  # strict


def _fire_order(symbol, action, price, reason, **kwargs):
    """Wrapper peste place_order_smart: paseaza motivul declansarii (Pas A2)
    — executarea/refuzul se jurnalizeaza centralizat in bapi_placeorder.py.
    KALMAN GATE: ordinul pleaca spre executie DOAR daca trece de gate.
    NU se da cantitate (21 iul, model uniform) — place_order_smart(qty=None)
    foloseste maximul permis de apply_weight_limit + clamp pe balanta reala;
    vechiul api.quantities[symbol] era doar un placeholder numeric arbitrar
    ($1000/$10000 nominal, inconsistent), oricum mereu taiat de acelasi gard."""
    blocked, mode, trend = _kalman_gate_blocks(symbol, action)
    if blocked:
        print(f"[KALMAN-GATE] {action} {symbol} BLOCAT (kalman_trend={trend}, mode={mode}, motiv={reason})")
        try:
            logger_fn = GATE_OUTCOME_LOG or po._log_order_outcome
            logger_fn(symbol, action, price, None,
                      "refused", f"kalman_gate_{mode}(trend={trend})", reason)
        except Exception as _e:  # noqa: BLE001
            print(f"[KALMAN-GATE] eroare jurnal ({_e}) — blocarea ramane")
        return None
    return po.place_order_smart(action, symbol, price, motivation=reason, **kwargs)


def track_and_place_order(action, symbol, count, proposed_price, current_price, order_ids=None):
    quantity = api.quantities[symbol]
    print(f"Iteration {count} generated price {proposed_price} versus {current_price}")
                    
    if order_ids is None:
        order_ids = []

    if action == 'HOLD':
        return order_ids

    # Cancel any existing orders
    if order_ids:
        for order_id in order_ids:
            if not api.cancel_order(symbol, order_id):
                alert.check_alert(True, f"Order executed! be Happy :-){order_id:.2f}")
        order_ids.clear()
        
    api.cancel_expired_orders(action, symbol, EXP_TIME_BUY_ORDER if action == 'BUY' else EXP_TIME_SELL_ORDER)
        
    num_orders, price_step = (1, 0.2) if action == "BUY" else (1, 0.08)

    # Price is rising, place fewer, larger orders. # Increase the spacing between orders as percents
    # Price is falling, place more, smaller orders # Reduce the spacing between orders as percents
   
    if action == 'BUY':
        api.cancel_expired_orders(action, symbol, EXP_TIME_BUY_ORDER)

        buy_price = min(proposed_price, current_price * 0.999)
        print(f"BUY price: {buy_price:.2f} USDT")

        alert.check_alert(True, f"BUY order {buy_price:.2f}")
       
        # Place the custom buy orders
        for i in range(num_orders):
            adjusted_buy_price = buy_price * (1 - i * price_step / 100)
            order_quantity = quantity / num_orders  # Divide quantity among orders
            print(f"Placing buy order at price: {adjusted_buy_price:.2f} USDT for {order_quantity:.6f} BTC")
            order = po.place_order_smart("BUY", symbol, adjusted_buy_price, order_quantity, cancelorders=True, hours=0.3, pair=True)
            if order:
                #print(f"Buy order placed successfully with ID: {order['orderId']}")
                order_ids.append(order['orderId']) 

    elif action == 'SELL':
        api.cancel_expired_orders(action, symbol, EXP_TIME_SELL_ORDER)

        sell_price = max(proposed_price, current_price * 1.001)
        print(f"SELL price: {sell_price:.2f} USDT")
        
        alert.check_alert(True, f"SELL order {sell_price:.2f}")

        # Place the custom sell orders
        for i in range(num_orders):
            adjusted_sell_price = sell_price * (1 + i * price_step / 100)
            order_quantity = quantity / num_orders  # Divide quantity among orders
            print(f"Placing sell order at price: {adjusted_sell_price:.2f} USDT for {order_quantity:.6f} BTC")
            order = po.place_order_smart("SELL", symbol, adjusted_sell_price, order_quantity, cancelorders=True, hours=0.3, pair=True)
            if order:
                print(f"Sell order placed successfully with ID: {order['orderId']}")
                order_ids.append(order['orderId']) 

    return order_ids


class TrendState:
    def __init__(self, max_duration_seconds, expiration_trend_time, fresh_trend_time, now_fn=time.time):
        self.state = 'HOLD'
        self.old_state = self.state
        self.expired = False
        self.start_time = None
        self.end_time = None
        self.last_confirmation_time = None
        self.max_duration_seconds = max_duration_seconds    # Nefolosit - Durata maxima permisa pentru un trend
        self.confirm_count = 0
        self.expiration_trend_time = expiration_trend_time  # Pragul de timp Intre confirmari (In secunde)
        self.fresh_trend_time = fresh_trend_time            # Pragul pentru a fi considerat fresh
        self._now = now_fn   # ceasul real implicit; backtester-ul injecteaza timpul tick-ului replay-uit (A5)
        # Cooldown per instanta de trend (22 iul, vezi FIRE_MIN_RETRY_INTERVAL_SEC):
        # _confirmed_{up,down} = "am EXECUTAT cu succes pe acest trend" (nu doar
        # incercat) -> nu mai tragem deloc pana la un trend nou. _last_attempt_*
        # = ultima incercare RESPINSA (gate/weight-limit/buget) -> reincercam
        # abia dupa FIRE_MIN_RETRY_INTERVAL_SEC, nu la fiecare tick.
        self._confirmed_up = False
        self._confirmed_down = False
        self._last_attempt_up_ts = None
        self._last_attempt_down_ts = None

    def start_trend(self, new_state):
        #self.end_trend()  # Marcheaza sfârsitul trendului anterior
        assert new_state in ['UP', 'DOWN', 'HOLD'], "Invalid trend state"
        self.old_state = self.state
        self.state = new_state
        self.start_time = self._now()
        self.last_confirmation_time = self.start_time
        self.confirm_count = 1
        self.end_time = None
        self.expired = False
        self._confirmed_up = False
        self._confirmed_down = False
        self._last_attempt_up_ts = None
        self._last_attempt_down_ts = None
        print(f"Start of {self.state} trend at {u.timeToHMS(self.start_time)}")
        return self.old_state

    def already_confirmed(self, direction):
        return self._confirmed_up if direction == 'UP' else self._confirmed_down

    def mark_confirmed(self, direction):
        if direction == 'UP':
            self._confirmed_up = True
        else:
            self._confirmed_down = True

    def can_retry_fire(self, direction):
        last = self._last_attempt_up_ts if direction == 'UP' else self._last_attempt_down_ts
        return last is None or (self._now() - last) >= FIRE_MIN_RETRY_INTERVAL_SEC

    def mark_fire_attempt(self, direction):
        if direction == 'UP':
            self._last_attempt_up_ts = self._now()
        else:
            self._last_attempt_down_ts = self._now()

    def confirm_trend(self):
        assert self.start_time is not None, "Trend must be started before confirming"
        self.last_confirmation_time = self._now()
        self.confirm_count += 1
        print(f"{self.confirm_count} times trend confirmed: {self.state} at {u.timeToHMS(self.last_confirmation_time)}")
        return self.confirm_count

    def get_confirmed_trend_duration(self):
        if self.start_time is None or self.last_confirmation_time is None:
            raise ValueError("Start and confirmation time must be set")
        if self.last_confirmation_time <= self.start_time:
            raise ValueError("Start time must be before confirmation time")
        return self.last_confirmation_time - self.start_time

    def get_started_trend_time(self):
        if self.start_time is None:
            return 0
        return self._now() - self.start_time


    def is_trend_fresh(self, fresh_trend_time=None):
        if fresh_trend_time is None:
            fresh_trend_time = self.fresh_trend_time
        assert self.start_time is not None, "Trend must be started before checking freshness"

        elapsed_time = self._now() - self.start_time
        if elapsed_time < fresh_trend_time:
            return True

        print(f"No fresh trend! Current trend start {int(elapsed_time)} sec back > {int(fresh_trend_time)} sec ")
        return False



    def is_trend_a_minim_validated(self) :
        return self.last_confirmation_time - self.start_time > 30 and self.confirm_count > 3
    
    def is_trend_consistent_validated(self) :
         #14 de confirmari per minut * 3 minute ->defapt 6 confirmari per minut  
        return self.confirm_count > 8 * 3 and self.is_trend_uniform_confirmed() # and  self.confirm_count < 100 * 3
    
    def is_trend_uniform_confirmed(self):
        if not self.is_trend_a_minim_validated() :
            return False
        
        trend_duration = self.get_started_trend_time() #self.get_confirmed_trend_duration()
        if trend_duration == 0:
            return False
        rate = self.confirm_count * 2.5 * TIME_SLEEP_GET_PRICE / trend_duration
        print(f"uniform rate is {rate} <> 0.1")
        #10 confirmari per 1.5 minute
        return rate > 0.08 #0.1

    def is_started_trend_older_than(self, old_trend_time):
        return self.get_started_trend_time() > old_trend_time

    def check_trend_expiration(self):
        if self.expired:
            return True
        if self.last_confirmation_time:
            time_since_last_confirmation = self._now() - self.last_confirmation_time
            if time_since_last_confirmation > self.expiration_trend_time:
                print(f"Trend expired: {self.state}. Time since last confirmation: {time_since_last_confirmation} seconds")
                self.end_trend()
                self.expired = True
                return self.expired
        return False #self.expired

    def end_trend(self):
        self.old_state = self.state
        self.end_time = self.last_confirmation_time
        print(f"Trend ended: {self.state} at {u.timeToHMS(self.end_time)} after {self.confirm_count} confirmations.")
        self.old_confirm_count = self.confirm_count
        self.state = 'HOLD'
        self.confirm_count = 0

    def is_trend_up(self):
        if self.check_trend_expiration() :
           return 0
        if self.state == 'UP':
            return self.confirm_count
        return 0

    def is_trend_down(self):
        if self.check_trend_expiration() :
           return 0
        if self.state == 'DOWN':
            return self.confirm_count
        return 0

    def is_hold(self):
        if self.check_trend_expiration() or self.state == 'HOLD':
            return self.confirm_count
        return 0
  


# NOTĂ: sell_recommendation.csv a fost eliminat. Semnalele de trend
# (slope/pos/gradient/tick/min/max) sunt publicate în CachePriceShortTrendManager
# (snapshot per simbol) și citite cross-process de monitortrades etc.



def logic_small(win, enable, symbol, gradient, slope, trend_state, current_price) :

    d = 0
    h = 1
    proposed_price = current_price

    print(f" SE ACTIVEAZA DUPA 3.5 la slope: gradient={gradient}, slope={slope}")
    if gradient < 0 and slope < -3.5:
        if enable:
            #po.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
            #    force=True, cancelorders=False, hours=1)
            print(f"FINISH FORCE place_order_smart SELL")
    if gradient > 0 and slope > 3.5:
        if enable:
            #po.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
            #    force=True, cancelorders=False, hours=1)
            print(f"FINISH FORCE place_order_smart BUY")



def logic(win, enable, symbol, gradient, slope, trend_state, current_price) :

    d = 14
    h = 24
    proposed_price = current_price

    def _fire_once(direction, action, reason):
        """Cooldown per instanta de trend (22 iul) — vezi FIRE_MIN_RETRY_INTERVAL_SEC.
        "Confirmat" = executie REALA (returul lui _fire_order nu e None), nu doar
        incercare — o respingere de gate/weight-limit/buget NU blocheaza definitiv,
        doar impune un interval minim pana la reincercare."""
        if trend_state.already_confirmed(direction):
            return
        if not trend_state.can_retry_fire(direction):
            return
        trend_state.mark_fire_attempt(direction)
        if enable:
            result = _fire_order(symbol, action, current_price, reason, safeback_seconds=d*h*3600+60,
                force=False, cancelorders=True, hours=1)
            if result is not None:
                trend_state.mark_confirmed(direction)

    print(f"LOGIC gradient={gradient}, slope={slope}")
    # if gradient < 0 and slope < 0 :
        # if enable:
            # po.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                # force=True, cancelorders=True, hours=1)
            # print(f"FINISH place_order_smart SELL")
    # if gradient > 0 and slope > 0 :
        # if enable:
            # po.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                # force=True, cancelorders=True, hours=1)
        # print(f"FINISH place_order_smart BUY")

    #todo adjust safeback_seconds
    if gradient > 0 and slope < 0 :
        # Confirmam un trend de crestere
        print(f"DIFERENTA MARE {win} DOWN!")
        proposed_price = current_price # * (1 - 0.01)
        if trend_state.is_trend_up():
            count = trend_state.confirm_trend() # Confirmam ca trendul de crestere continua
            if trend_state.is_trend_uniform_confirmed() and trend_state.is_trend_fresh():
                #track_and_place_order('BUY', sym.btcsymbol, count, proposed_price, current_price, order_ids=order_ids)
                _fire_once("UP", "BUY", "trend_confirmed_up")
                print(f"place_order_smart BUY")
        else:
            prev_confirm_count = trend_state.confirm_count  # cat a "prins" trendul anterior (near-miss)
            old_trend = trend_state.start_trend('UP')  # Incepem un trend nou de crestere
            log_decision(symbol, "trend_start", state="UP", old_state=old_trend, price=current_price,
                         prev_confirm_count=prev_confirm_count)
            #track_and_place_order('BUY', sym.btcsymbol, 1, proposed_price, current_price, order_ids=order_ids)
            #po.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=16*3600+60,
            #    force=True, cancelorders=True, hours=1)

    if gradient < 0 and slope > 0 :
        # Confirmam un trend de scadere
        print(f"DIFERENTA MARE {win} UP!")
        proposed_price = current_price #  * (1 + 0.01)
        if trend_state.is_trend_down():
            count = trend_state.confirm_trend() # Confirmam ca trendul de scadere continua
            if trend_state.is_trend_uniform_confirmed() and trend_state.is_trend_fresh() :
                #track_and_place_order('SELL', symbol, count, proposed_price, current_price, order_ids=order_ids)
                _fire_once("DOWN", "SELL", "trend_confirmed_down")
                print(f"place_order_smart SELL")
        else:
            prev_confirm_count = trend_state.confirm_count  # cat a "prins" trendul anterior (near-miss)
            old_trend = trend_state.start_trend('DOWN')  # Incepem un trend nou de scadere
            log_decision(symbol, "trend_start", state="DOWN", old_state=old_trend, price=current_price,
                         prev_confirm_count=prev_confirm_count)
            #track_and_place_order('SELL', symbol, 1, proposed_price, current_price, order_ids=order_ids)
            #po.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=16*3600+60,
            #    force=True, cancelorders=True, hours=1)

    proposed_price = current_price
    #18 de confirmari per minut * 3 minute ->defapt 6 confirmari per minut
    if slope <= 0 and trend_state.is_trend_up():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE BUY ALL {win} .... ")
            _fire_once("UP", "BUY", "consistent_or_old_up")
    #18 de confirmari per minut * 3 minute
    if slope >= 0 and trend_state.is_trend_down():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE SELL ALL {win} .... ")
            _fire_once("DOWN", "SELL", "consistent_or_old_down")
                    
    #
    #new case
    #
    if slope <= -5.1 and trend_state.is_trend_up():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 2: BUY ALL {win} .... ")
            _fire_once("UP", "BUY", "slope<=-5.1_up")
    #18 de confirmari per minut * 3 minute
    if slope >= 5.1 and trend_state.is_trend_down():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 2: SELL ALL {win} .... ")
            _fire_once("DOWN", "SELL", "slope>=5.1_down")
                                                                                                                                                                 
    #
    #new case
    #
    if slope <= -5.1 and trend_state.is_trend_down():
        if (trend_state.is_trend_consistent_validated()
        and trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 3: BUY ALL {win} .... ")
            _fire_once("UP", "BUY", "slope<=-5.1_and_old_down")
    #18 de confirmari per minut * 3 minute
    if slope >= 5.1 and trend_state.is_trend_up():
        if (trend_state.is_trend_consistent_validated()
        and trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 3: SELL ALL {win} .... ")
            _fire_once("DOWN", "SELL", "slope>=5.1_and_old_up")
   
#todo ia acceleratiea pe timp scurt get minute 1-3 si daca e mare cumpara!   


# Function to handle the price logic for a specific currency.
# Ferestrele se actualizează autonom (abonate la Cache24); aici doar evaluăm.
# Returnează un snapshot al trendului care va fi pus în cache de TrendCoordinator.
def handle_symbol(symbol, current_price, price_window, price_window_big,
                  analyzer, analyzer_big, trend_state, trend_state_big):

    count = 0

    # Actualizare rata reală de sampling din CacheCurrentPriceManager
    try:
        import cacheManager as cm
        actual_rate = cm.get_current_price_manager().get_sample_rate(
            symbol, fallback=TIME_SLEEP_GET_PRICE)
        price_window.set_sample_rate(actual_rate)
        price_window_big.set_sample_rate(actual_rate)
    except Exception:
        pass

    slope, pos = analyzer.check_price_change(PRICE_CHANGE_THRESHOLD_EUR)
    print(f"small slope {slope}")
    gradient, gradient_coff, slope_full, gradient_recent = price_window.get_instant_trend()

    slope_max_min = analyzer.calculate_slope_max_min()
    if slope * gradient < 0:
        print(f"ALERT slope1 = {slope} gradient = {gradient}")
    if slope * slope_max_min < 0:
        print(f"ALERT slope2 = {slope} calculate_slope_max_min() = {slope_max_min}")
    if gradient * slope_max_min < 0:
        print(f"ALERT gradient = {gradient} calculate_slope_max_min() = {slope_max_min}")
    if slope == 0:
        count = count + 1
    else:
        count = 0

    # SMALL ONE!!
    logic_small("SMALL", True, symbol, gradient, slope, trend_state, current_price)

    # BIG ONE!!!
    slope_big, price_diff = analyzer_big.check_price_change(PRICE_CHANGE_THRESHOLD_BIG_EUR)
    logic("BIG", True, symbol, gradient, slope_big, trend_state_big, current_price)

    for moneda in web.monede:
        if moneda["nume"] == symbol:
            moneda["watch"] = True if slope_big != 0 else False

    # Snapshot pentru cache cross-process. monitortrades folosește efectiv doar
    # slope_small (→slope) și final_trend (→gradient) prin is_trend_up; restul
    # sunt metrici de trend pentru alți consumatori / gate-ul de buy/sell.
    return {
        "symbol": symbol,
        "final_trend": gradient,
        "growth_coefficient": gradient_coff,
        "slope_full": slope_full,
        "gradient_recent": gradient_recent,
        "slope_small": slope,
        "slope_big": slope_big,
        "slope_max_min": slope_max_min,
        "pos": pos,
        "current_price": current_price,
        "ts": time.time(),
    }


# ════════════════════════════════════════════════════════════════════════════
# TrendCoordinator — event-driven + heartbeat.
#
# Sursa de preț (WS → Cache24) actualizează ferestrele autonom. Coordinatorul:
#   • primește semnal la fiecare tick (on_price_update → dirty + event)
#   • evaluează un simbol DOAR când e "due":
#       - dirty ȘI a trecut ≥ MIN_EVAL_INTERVAL_SEC  (floor: nu prea des)
#       - SAU a trecut ≥ MAX_EVAL_INTERVAL_SEC        (heartbeat: nu prea rar)
#   • cache-uiește rezultatul → get_cached_trend(symbol) e O(1) pentru API buy/sell
# Single-threaded loop → fără reentranță pe plasarea ordinelor.
# ════════════════════════════════════════════════════════════════════════════

MIN_EVAL_INTERVAL_SEC = 1.5    # floor: cel mult o evaluare la 1.5s per simbol
MAX_EVAL_INTERVAL_SEC = 30.0   # ceiling/heartbeat: cel puțin o evaluare la 30s


class TrendCoordinator:
    """Bucla de EVALUARE TRADING (event-driven + heartbeat). Ferestrele, calculul
    de trend și cache-ul cross-process stau în CachePriceShortTrendManager; aici doar
    consumăm (windows/analyzers/trend) și luăm decizii (handle_symbol/logic)."""
    def __init__(self, symbols, instant_mgr, current_price_mgr, cache24_managers=None,
                 min_interval=MIN_EVAL_INTERVAL_SEC, max_interval=MAX_EVAL_INTERVAL_SEC):
        self.symbols = list(symbols)
        self.instant_mgr = instant_mgr
        self.current_price_mgr = current_price_mgr
        self.min_interval = min_interval
        self.max_interval = max_interval

        self._event = threading.Event()
        self._lock = threading.Lock()
        self._dirty = {s: True for s in self.symbols}      # forțăm o primă evaluare
        self._last_eval = {s: 0.0 for s in self.symbols}

        # Semnale SHADOW (observationale, plan 17 iul): Kalman trend + vol adaptiv.
        # Un bug aici NU are voie sa opreasca trading-ul -> instantiere + apel guarded.
        try:
            import shadow_signals
            self._shadow = shadow_signals.ShadowSet(
                state_path=os.path.join("cachedb", "shadow_state.json"))
            global _shadow_ref
            _shadow_ref = self._shadow   # gate-ul din _fire_order consulta acest semnal
            print(f"[KALMAN-GATE] activ, mode={os.environ.get('KALMAN_GATE_MODE', 'strict')}")
        except Exception as _e:  # noqa: BLE001
            print(f"[TrendCoordinator] shadow_signals indisponibil (continui fara): {_e}")
            self._shadow = None

        self.trend_states = {}
        self.trend_states_big = {}
        for symbol in self.symbols:
            self.trend_states[symbol] = TrendState(max_duration_seconds=2.5 * 60 * 60,
                                                   expiration_trend_time=2.7 * 60, fresh_trend_time=3.7 * 60)
            self.trend_states_big[symbol] = TrendState(max_duration_seconds=3 * 60 * 60,
                                                       expiration_trend_time=2.7 * 60, fresh_trend_time=3.7 * 60)
            # Ferestrele + canalul rapid sunt în instant_mgr; aici ne abonăm la
            # Cache24 DOAR pentru semnalul de evaluare (dirty + event).
            if cache24_managers is not None:
                cache24_managers[symbol].subscribe_price(self)

    # ── Semnal de la Cache24 (subscriber) — trezește evaluarea ────────────────
    def on_price_update(self, symbol: str, ts_ms: int, price: float) -> None:
        with self._lock:
            if symbol not in self._dirty:
                return
            self._dirty[symbol] = True
        self._event.set()

    # ── Decizie: simbolul trebuie evaluat acum? ───────────────────────────────
    def _is_due(self, symbol, now):
        elapsed = now - self._last_eval[symbol]
        if elapsed >= self.max_interval:          # heartbeat
            return True
        with self._lock:
            dirty = self._dirty.get(symbol, False)
        return dirty and elapsed >= self.min_interval   # floor

    def evaluate(self, symbol):
        current_price = self.current_price_mgr.get_price_value(symbol)
        if current_price is None:
            return None
        snapshot = handle_symbol(
            symbol, current_price,
            self.instant_mgr.get_window(symbol),
            self.instant_mgr.get_window(symbol, self.instant_mgr.window_big_sec),
            self.instant_mgr.get_analyzer(symbol),
            self.instant_mgr.get_analyzer(symbol, self.instant_mgr.window_big_sec),
            self.trend_states[symbol], self.trend_states_big[symbol],
        )
        with self._lock:
            self._dirty[symbol] = False
            self._last_eval[symbol] = time.time()
        # Publică snapshot-ul complet (merge) în store-ul cross-process.
        # snapshot conține deja cheia "symbol" → o scoatem (e arg pozițional).
        fields = {k: v for k, v in snapshot.items() if k != "symbol"}
        # SHADOW (observational): chei suplimentare in snapshot + jurnal propriu la
        # tranzitii Kalman. Guarded — nu poate afecta evaluarea/deciziile reale.
        if self._shadow is not None:
            try:
                win = self.instant_mgr.get_window(symbol)
                win_big = self.instant_mgr.get_window(symbol, self.instant_mgr.window_big_sec)
                prev_ktrend = fields_prev_ktrend = None
                st_prev = self._shadow._state.get(symbol)
                if st_prev:
                    prev_ktrend = st_prev.get("kalman_trend")
                shadow_fields = self._shadow.update(
                    symbol, snapshot["ts"], current_price,
                    epsilon=win.get_noise_epsilon(),
                    big_prices=list(win_big.prices),
                    big_sample_rate=win_big.sample_rate_sec,
                )
                fields.update(shadow_fields)
                # KALMAN-PRIMAR: la tranzitie de trend, initiaza ordin (doar simbolurile
                # activate; garzile + gate-ul din _fire_order raman singurele care decid
                # daca banii chiar se misca).
                new_ktrend = shadow_fields.get("kalman_trend")
                if (symbol in KALMAN_PRIMARY_SYMBOLS and prev_ktrend is not None
                        and new_ktrend != prev_ktrend):
                    d, h = 14, 24
                    if new_ktrend == 1:
                        print(f"[KALMAN-PRIMAR] {symbol} ->UP: initiez BUY")
                        _fire_order(symbol, "BUY", current_price, "kalman_primary_up",
                                    safeback_seconds=d*h*3600+60, force=False,
                                    cancelorders=True, hours=1)
                    elif new_ktrend == -1:
                        print(f"[KALMAN-PRIMAR] {symbol} ->DOWN: initiez SELL")
                        _fire_order(symbol, "SELL", current_price, "kalman_primary_down",
                                    safeback_seconds=d*h*3600+60, force=False,
                                    cancelorders=True, hours=1)
            except Exception as _e:  # noqa: BLE001
                print(f"[TrendCoordinator] eroare shadow {symbol} (continui): {_e}")
        self.instant_mgr.update_snapshot(symbol, **fields)
        return snapshot

    def get_cached_trend(self, symbol):
        return self.instant_mgr.get_snapshot(symbol)

    def get_all_cached_trends(self):
        return self.instant_mgr.get_all_snapshots()

    # ── Bucla principală event-driven + heartbeat ─────────────────────────────
    def run(self):
        while True:
            self._event.wait(timeout=self.max_interval)
            self._event.clear()
            now = time.time()
            due = [s for s in self.symbols if self._is_due(s, now)]
            if not due:
                continue
            print(f"----------------------------------")
            for symbol in due:
                try:
                    self.evaluate(symbol)
                except Exception as e:
                    print(f"[TrendCoordinator] Eroare la evaluare {symbol}: {e}")
            try:
                html_content = web.genereaza_html(web.monede)
                web.salveaza_html(html_content, "index.html")
            except Exception as e:
                print(f"[TrendCoordinator] Eroare la generare HTML: {e}")


if __name__ == "__main__":
    trades = apitrades.get_my_trades_24(order_type=None, symbol=sym.btcsymbol, days_ago=0, limit=1000)
    print(f" --------- {len(trades)}")
    print(f" my trades of today : {trades}")

    order_ids = []

    # Chain de pret: WebSocket market-data → CacheCurrentPrice → Cache24 → PriceWindow.
    # IMPORTANT: creem singleton-ul CacheCurrentPrice INAINTE de Cache24 cu sync_ts
    # corect (altfel Cache24.get_remote_items l-ar crea intern cu sync_ts=30).
    import cacheManager as cm
    from binance_api import bapi_ws
    # WS user-data bridge e opt-in; tradeall vrea execution reports (fill-uri).
    cm.enable_real_ws_event_sync()
    current_price_mgr = cm.get_current_price_manager(
        ws_manager=bapi_ws.get_ws_manager(),
        sync_ts=TIME_SLEEP_GET_PRICE,
    )
    cache24_managers = cm.CacheFactory.get("Price24")   # dict {symbol: Cache24PriceManager}

    # Managerul de trend: deține ferestrele, calculează trendul, cache cross-process.
    instant_mgr = cm.get_short_trend_manager()
    instant_mgr.start_computation(cache24_managers, current_price_mgr)

    coordinator = TrendCoordinator(
        symbols=sym.symbols,
        instant_mgr=instant_mgr,
        current_price_mgr=current_price_mgr,
        cache24_managers=cache24_managers,
    )
    coordinator.run()

