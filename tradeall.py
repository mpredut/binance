import os
import time
import datetime
import math
import threading
from binance.exceptions import BinanceAPIException
from collections import deque

#my imports
import log
import alertnotifiers as alert
import utils as u
import symbols as sym
import bapi as api
import bapi_placeorder as po

import bapi_trades as apitrades
import bapi_allorders as apiorders

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
    def __init__(self, max_duration_seconds, expiration_trend_time, fresh_trend_time):
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

    def start_trend(self, new_state):
        #self.end_trend()  # Marcheaza sfârsitul trendului anterior
        assert new_state in ['UP', 'DOWN', 'HOLD'], "Invalid trend state"
        self.old_state = self.state
        self.state = new_state
        self.start_time = time.time()
        self.last_confirmation_time = self.start_time
        self.confirm_count = 1
        self.end_time = None
        self.expired = False
        print(f"Start of {self.state} trend at {u.timeToHMS(self.start_time)}")
        return self.old_state

    def confirm_trend(self):
        assert self.start_time is not None, "Trend must be started before confirming"
        self.last_confirmation_time = time.time()
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
        return time.time() - self.start_time
        
    
    def is_trend_fresh(self, fresh_trend_time=None):
        if fresh_trend_time is None:
            fresh_trend_time = self.fresh_trend_time
        assert self.start_time is not None, "Trend must be started before checking freshness"

        elapsed_time = time.time() - self.start_time
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
            time_since_last_confirmation = time.time() - self.last_confirmation_time
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
        self.state == 'HOLD'
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
  


# Cache-ul care va fi actualizat periodic
default_values_sell_recommendation = {
    "BTCUSDT": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7,
        'slope': 0.0,      # Valoare default pentru slope
        'pos': 0,          # Valoare default pentru pos
        'gradient': 0.0,   # Valoare default pentru gradient
        'tick': 0,         # Valoare default pentru tick
        'min': 0.0,        # Valoare default pentru min
        'max': 0.0         # Valoare default pentru max
    },
    "ETHUSDT": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7,
        'slope': 0.0,      # Valoare default pentru slope
        'pos': 0,          # Valoare default pentru pos
        'gradient': 0.0,   # Valoare default pentru gradient
        'tick': 0,         # Valoare default pentru tick
        'min': 0.0,        # Valoare default pentru min
        'max': 0.0         # Valoare default pentru max
    }
}
def initialize_csv_file(file_path):
    if not os.path.exists(file_path):
        # Convert the default values dictionary to a DataFrame
        df = pd.DataFrame.from_dict(default_values_sell_recommendation, orient='index').reset_index()
        df.rename(columns={'index': 'symbol'}, inplace=True)
        
        # Write the DataFrame to CSV
        df.to_csv(file_path, index=False)
        print(f"CSV file created with default values at {file_path}.")
    else:
        print(f"CSV file already exists at {file_path}.")

def update_csv_file(file_path, symbol, slope, tick, min_val, max_val, pos, gradient):
    try:
        # Load the existing CSV data
        df = pd.read_csv(file_path)

        # Check if the symbol already exists in the CSV
        if symbol in df['symbol'].values:
            # Update existing row with new values
            df.loc[df['symbol'] == symbol, ['slope', 'tick', 'min', 'max', 'pos', 'gradient']] = [
                slope, tick, min_val, max_val, pos, gradient
            ]
        else:
            # Append a new row if symbol does not exist
            new_row = {
                'symbol': symbol,
                'slope': slope,
                'tick': tick,
                'min': min_val,
                'max': max_val,
                'pos': pos,
                'gradient': gradient,
                # Ensure other columns are populated from default values
                **default_values_sell_recommendation.get(symbol, {})
            }
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

        # Write the updated DataFrame back to the CSV
        df.to_csv(file_path, index=False)
        print(f"Updated {symbol} in CSV with slope: {slope}, tick: {tick}, min: {min_val}, max: {max_val}, pos: {pos}, gradient: {gradient}")
    except Exception as e:
        print(f"Error updating CSV file: {e}")
    
filename = "sell_recommendation.csv"



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
                if enable:
                    po.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                        force=False, cancelorders=True, hours=1)
                print(f"place_order_smart BUY")
        else:
            old_trend = trend_state.start_trend('UP')  # Incepem un trend nou de crestere
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
                if enable:
                    po.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                        force=False, cancelorders=True, hours=1)
                print(f"place_order_smart SELL")
        else:
            old_trend = trend_state.start_trend('DOWN')  # Incepem un trend nou de scadere
            #track_and_place_order('SELL', symbol, 1, proposed_price, current_price, order_ids=order_ids)
            #po.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=16*3600+60,
            #    force=True, cancelorders=True, hours=1)

    proposed_price = current_price
    #18 de confirmari per minut * 3 minute ->defapt 6 confirmari per minut
    if slope <= 0 and trend_state.is_trend_up():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE BUY ALL {win} .... ")
            if enable:
                po.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=False, cancelorders=True, hours=1)
    #18 de confirmari per minut * 3 minute
    if slope >= 0 and trend_state.is_trend_down():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE SELL ALL {win} .... ")
            if enable:
                po.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=False, cancelorders=True, hours=1)
                    
    #
    #new case
    #
    if slope <= -5.1 and trend_state.is_trend_up():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 2: BUY ALL {win} .... ")
            if enable:
                po.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=False, cancelorders=True, hours=1)
    #18 de confirmari per minut * 3 minute
    if slope >= 5.1 and trend_state.is_trend_down():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 2: SELL ALL {win} .... ")
            if enable:
                po.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=False, cancelorders=True, hours=1)
                                                                                                                                                                 
    #
    #new case
    #
    if slope <= -5.1 and trend_state.is_trend_down():
        if (trend_state.is_trend_consistent_validated()
        and trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 3: BUY ALL {win} .... ")
            if enable:
                po.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=False, cancelorders=True, hours=1)
    #18 de confirmari per minut * 3 minute
    if slope >= 5.1 and trend_state.is_trend_up():
        if (trend_state.is_trend_consistent_validated()
        and trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 3: SELL ALL {win} .... ")
            if enable:
                po.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=False, cancelorders=True, hours=1)
   
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

    update_csv_file(filename, symbol, slope, count, 0, 0, pos, gradient)

    for moneda in web.monede:
        if moneda["nume"] == symbol:
            moneda["watch"] = True if slope_big != 0 else False

    # Snapshot pentru cache (citibil rapid din API buy/sell)
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
EPSILON_K = 1.0                # multiplicator pt pragul de zgomot (k * stddev gradient)


class TrendCoordinator:
    def __init__(self, symbols, cache24_managers, current_price_mgr,
                 min_interval=MIN_EVAL_INTERVAL_SEC, max_interval=MAX_EVAL_INTERVAL_SEC):
        self.symbols = list(symbols)
        self.current_price_mgr = current_price_mgr
        self.min_interval = min_interval
        self.max_interval = max_interval

        self._event = threading.Event()
        self._lock = threading.Lock()
        self._dirty = {s: True for s in self.symbols}      # forțăm o primă evaluare
        self._last_eval = {s: 0.0 for s in self.symbols}
        self._trend_cache = {}                              # {symbol: snapshot dict}

        self.windows = {}
        self.windows_big = {}
        self.analyzers = {}
        self.analyzers_big = {}
        self.trend_states = {}
        self.trend_states_big = {}

        for symbol in self.symbols:
            cache24 = cache24_managers[symbol]
            current_price_mgr.subscribe_price(cache24)   # CurrentPrice → Cache24

            w = PriceWindow.from_cache24(symbol, window_seconds=WINDOW_SECONDS_SMALL, cache24=cache24)
            wb = PriceWindow.from_cache24(symbol, window_seconds=WINDOW_SECONDS_BIG, cache24=cache24)
            self.windows[symbol] = w
            self.windows_big[symbol] = wb
            self.analyzers[symbol] = WindowAnalyzer(w)
            self.analyzers_big[symbol] = WindowAnalyzer(wb)
            self.trend_states[symbol] = TrendState(max_duration_seconds=2.5 * 60 * 60,
                                                   expiration_trend_time=2.7 * 60, fresh_trend_time=3.7 * 60)
            self.trend_states_big[symbol] = TrendState(max_duration_seconds=3 * 60 * 60,
                                                       expiration_trend_time=2.7 * 60, fresh_trend_time=3.7 * 60)

            cache24.subscribe_price(self)   # primim semnal de tick

            print(f"[{symbol}] window small: {len(w.prices)} sample-uri (rate={w.sample_rate_sec:.2f}s)")
            print(f"[{symbol}] window big:   {len(wb.prices)} sample-uri (rate={wb.sample_rate_sec:.2f}s)")

    # ── Semnal de la Cache24 (subscriber) ─────────────────────────────────────
    def on_price_update(self, symbol: str, ts_ms: int, price: float) -> None:
        with self._lock:
            if symbol not in self._dirty:
                return
            self._dirty[symbol] = True
        self._event.set()   # trezește bucla de evaluare (eval completă, throttled)

        # Canal RAPID: publică gradientul instant la fiecare tick (ieftin, tăcut)
        # ca gate-ul buy/sell să reacționeze în ~latența unui tick, nu la 1.5s.
        try:
            import trend_api
            win = self.windows[symbol]
            g = win.get_recent_gradient()
            eps = win.get_noise_epsilon(k=EPSILON_K)   # prag de zgomot informat per simbol
            trend_api.update_instant(
                symbol,
                gradient_recent=g,
                epsilon=eps,
                final_trend=(1 if g > 0 else -1 if g < 0 else 0),
                current_price=price,
                ts=time.time(),
            )
        except Exception as e:
            print(f"[TrendCoordinator] update_instant {symbol}: {e}")

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
            self.windows[symbol], self.windows_big[symbol],
            self.analyzers[symbol], self.analyzers_big[symbol],
            self.trend_states[symbol], self.trend_states_big[symbol],
        )
        with self._lock:
            self._trend_cache[symbol] = snapshot
            self._dirty[symbol] = False
            self._last_eval[symbol] = time.time()
        # Publică pentru consumatori externi (ex. bapi_placeorder)
        import trend_api
        trend_api.publish_trend(symbol, snapshot)
        return snapshot

    # ── API rapid pentru buy/sell ─────────────────────────────────────────────
    def get_cached_trend(self, symbol):
        """O(1) — ultimul snapshot de trend pentru decizii buy/sell."""
        with self._lock:
            return self._trend_cache.get(symbol)

    def get_all_cached_trends(self):
        with self._lock:
            return dict(self._trend_cache)

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
    initialize_csv_file(filename)

    trades = apitrades.get_my_trades_24(order_type=None, symbol=sym.btcsymbol, days_ago=0, limit=1000)
    print(f" --------- {len(trades)}")
    print(f" my trades of today : {trades}")

    order_ids = []

    # Chain de pret: WebSocket market-data → CacheCurrentPrice → Cache24 → PriceWindow.
    # IMPORTANT: creem singleton-ul CacheCurrentPrice INAINTE de Cache24 cu sync_ts
    # corect (altfel Cache24.get_remote_items l-ar crea intern cu sync_ts=30).
    import cacheManager as cm
    import bapi_ws
    current_price_mgr = cm.get_current_price_manager(
        ws_manager=bapi_ws.bapi_ws_manager,
        sync_ts=TIME_SLEEP_GET_PRICE,
    )
    cache24_managers = cm.CacheFactory.get("Price24")   # dict {symbol: Cache24PriceManager}

    print(f"Quantities: {api.quantities}")

    coordinator = TrendCoordinator(
        symbols=sym.symbols,
        cache24_managers=cache24_managers,
        current_price_mgr=current_price_mgr,
    )
    coordinator.run()

