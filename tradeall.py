import os
import time
import datetime
import math
from binance.exceptions import BinanceAPIException
from collections import deque

#my imports
import log
import alert
import utils as u
import symbols as sym
import binanceapi as api
import binanceapi_trades as apitrades
import binanceapi_allorders as apiorders

#import priceprediction as pp
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import linregress
from sklearn.preprocessing import PolynomialFeatures
from sklearn.linear_model import LinearRegression


import generateweb as web

class PriceTrendAnalyzer:
    def __init__(self, prices):
        self.prices = prices

    def linear_regression_trend(self):
        if len(self.prices) < 2:  # Avem nevoie de cel puțin două puncte pentru regresie
            print("Regresie Liniară: Nu sunt suficiente date pentru a calcula trendul.")
            return None, None, None

        x = np.arange(len(self.prices))
        y = np.array(self.prices)
        
        # Verificăm variabilitatea prețurilor pentru a evita NaN
        if np.std(y) == 0:
            print("Regresie Liniară: Prețurile sunt constante, trendul nu poate fi determinat.")
            return None, None, None

        slope, intercept, r_value, _, _ = linregress(x, y)
        trend_line = slope * x + intercept
        return trend_line, slope, r_value

    def polynomial_regression_trend(self, degree=2):
        x = np.arange(len(self.prices)).reshape(-1, 1)
        y = np.array(self.prices)
        poly_features = PolynomialFeatures(degree=degree)
        x_poly = poly_features.fit_transform(x)
        
        model = LinearRegression().fit(x_poly, y)
        trend_poly = model.predict(x_poly)
        return trend_poly, model.coef_

    def exponential_moving_average(self, span=5):
        prices_list = list(self.prices)  # Convertim deque în listă
        ema = [prices_list[0]]
        alpha = 2 / (span + 1)
        
        for price in prices_list[1:]:  # Parcurgem prețurile începând de la al doilea element
            ema.append(alpha * price + (1 - alpha) * ema[-1])
    
        return ema


    def calculate_gradient(self):
        if len(self.prices) < 2:
            print("Gradient: Nu sunt suficiente date pentru a calcula gradientul.")
            return [], 0
        y = np.array(self.prices)  # Convertim la array pentru numpy
        gradient = np.gradient(y)
        avg_gradient = np.mean(gradient)
        return gradient, avg_gradient
    
    def plot_trends(self, trend_line, trend_poly, ema, gradient):
        x = np.arange(len(self.prices))

        fig, ax1 = plt.subplots(figsize=(10, 6))

        # Graficul prețurilor
        ax1.plot(x, self.prices, label='Prețuri', marker='o', color='blue')
        ax1.plot(x, trend_line, label='Regresie Liniară', color='orange')
        ax1.plot(x, trend_poly, label='Regresie Polinomială', color='purple')
        ax1.plot(x, ema, label='Media Mobilă Exponențială', color='green')
        ax1.set_xlabel("Timp")
        ax1.set_ylabel("Preț")
        
        # Graficul gradientului
        ax2 = ax1.twinx()
        ax2.plot(x, gradient, 'r--', label='Gradient')
        ax2.set_ylabel("Gradient", color='red')

        fig.legend(loc="upper left")
        plt.title("Analiza Tendinței Prețurilor")
        plt.show()
    
    def analyze_trends(self, poly_degree=2, ema_span=5):
        # Regresie liniară
        trend_line, slope, r_value = self.linear_regression_trend()
        if not slope is None:
            lin_trend = "creștere" if slope > 0 else "descreștere"
        lin_trend = "nedefinit"
        print(f"Regresie Liniară: Slope = {slope:.2f}, R = {r_value:.2f} -> Tendință estimată de {lin_trend}")

        # Regresie polinomială
        trend_poly, poly_coef = self.polynomial_regression_trend(degree=poly_degree)
        poly_trend = "creștere" if poly_coef[-1] > 0 else "descreștere"
        print(f"Regresie Polinomială (grad {poly_degree}): Coeficient final = {poly_coef[-1]:.2f} -> Tendință de {poly_trend}")

        # Media Mobilă Exponențială
        ema = self.exponential_moving_average(span=ema_span)
        print(f"Media Mobilă Exponențială: Ultima valoare EMA = {ema[-1]:.2f}")

        # Gradientul
        gradient, avg_gradient = self.calculate_gradient()
        grad_trend = "1" if avg_gradient > 0 else "-1"
        print(f"Gradient Mediu: {avg_gradient:.2f} -> Tendință locală de {grad_trend}")

        # Plotarea tendințelor
        self.plot_trends(trend_line, trend_poly, ema, gradient)

# Exemplu de utilizare:
#prices = [100, 102, 101, 105, 107, 110, 108, 112, 115, 117]  # Înlocuiește cu lista ta de prețuri
#analyzer = PriceTrendAnalyzer(prices)
#analyzer.analyze_trends(poly_degree=2, ema_span=5)

from collections import deque
from bisect import insort, bisect_left
class PriceWindow:
    def __init__(self, symbol, window_size, initial_prices=None):
        self.symbol = symbol
        self.window_size = int(window_size)
        self.prices = deque(maxlen=self.window_size)
        self.sorted_prices = []
        
        if initial_prices:
            for price in initial_prices[-self.window_size:]:  # Doar ultimele `window_size` elemente
                self.process_price(price)

    @classmethod
    def from_existing_window(cls, existing_prices, window_size):
        """ Creează o instanță nouă de PriceWindow cu ultimele `window_size` elemente din `existing_prices`. """
        return cls(window_size, initial_prices=existing_prices)

    def process_price(self, price):
        print(f"{symbol}: {price}")
        if len(self.prices) == self.window_size:
            oldest_price = self.prices.popleft()
            index = bisect_left(self.sorted_prices, oldest_price)
            if index < len(self.sorted_prices) and self.sorted_prices[index] == oldest_price:
                del self.sorted_prices[index]
            else :
                print("HAHAHAHAA")

        self.prices.append(price)
        insort(self.sorted_prices, price)
        
        if len(self.sorted_prices) != len(self.prices) :
            print("XXXXXXXXXXXXXXXXXXX")

    def get_newest_index(self):
        return len(self.prices) - 1 if self.prices else None
    
    def get_min(self):
        if not self.sorted_prices:
            return None

        #print("Sorted prices:", self.sorted_prices)  # Debug
        min_price = self.sorted_prices[0]
        close_min_values = [price for price in self.sorted_prices if u.are_close(price, min_price, 0.01)]
        
        #print("Close minimum values:", close_min_values)  # Debug
        avg_min = sum(close_min_values) / len(close_min_values) if close_min_values else min_price
        #print("Average minimum value:", avg_min)  # Debug
        return avg_min

    def get_max(self):
        if not self.sorted_prices:
            return None

        #print("Sorted prices:", self.sorted_prices)  # Debug
        max_price = self.sorted_prices[-1]
        close_max_values = [price for price in reversed(self.sorted_prices) if u.are_close(price, max_price, 0.01)]
        
        #print("Close maximum values:", close_max_values)  # Debug
        avg_max = sum(close_max_values) / len(close_max_values) if close_max_values else max_price
        #print("Average maximum value:", avg_max)  # Debug
        return avg_max

    def get_min_and_index(self):
        if not self.sorted_prices:
            print("BED1")
            return None, None

        min_price = self.get_min()
        min_indices = [i for i, price in enumerate(self.prices) if u.are_close(price, min_price, 0.01)]
        
        #print("Min indices:", min_indices)  # Debug
        centroid_index = sum(min_indices) / len(min_indices) if min_indices else None
        #print(f"Min price: {min_price}, Centroid index for min: {centroid_index}")  # Debug
        return min_price, centroid_index

    def get_max_and_index(self):
        if not self.sorted_prices:
            print("BED2")
            return None, None

        max_price = self.get_max()
        max_indices = [i for i, price in enumerate(self.prices) if u.are_close(price, max_price, 0.01)]
        
        #print("Max indices:", max_indices)  # Debug
        centroid_index = sum(max_indices) / len(max_indices) if max_indices else None
        #print(f"Max price: {max_price}, Centroid index for max: {centroid_index}")  # Debug
        return max_price, centroid_index

    def calculate_slope_max_min(self):
        if len(self.sorted_prices) < 2:
            return 0

        min_price, min_index = self.get_min_and_index()
        max_price, max_index = self.get_max_and_index()

        if min_price is None or max_price is None or max_index == min_index:
            #print(f"BED3 - Min index: {min_index}, Max index: {max_index}")  # Debug
            return 0

        slope = (max_price - min_price) / (max_index - min_index)
        #print(f"Slope: {slope}, Min price: {min_price}, Max price: {max_price}, Min index: {min_index}, Max index: {max_index}")  # Debug
        return slope


    def calculate_proximities(self, current_price):
        min_price, _ = self.get_min_and_index()
        max_price, _ = self.get_max_and_index()

        if min_price is None or max_price is None or max_price == min_price:
            return 0, 0
        min_proximity = (current_price - min_price) / (max_price - min_price)
        max_proximity = (max_price - current_price) / (max_price - min_price)
        
        # Asigurăm că valorile de proximitate sunt pozitive
        return max(min_proximity, 0), max(max_proximity, 0)

    def calculate_positions(self):
        min_price, min_index = self.get_min_and_index()
        max_price, max_index = self.get_max_and_index()
        
        min_position = min_index / self.window_size if min_index is not None else None
        max_position = max_index / self.window_size if max_index is not None else None
        return min_position, max_position
        
        
    def evaluate_buy_sell_opportunity(self, current_price, threshold_percent=1, decrease_percent=3.7):
        slope = self.calculate_slope_max_min()
     
        min_price, min_index = self.get_min_and_index()
        max_price, max_index = self.get_max_and_index()
        
        print(
            f"Min price: {min_price} at index: {min_index} "
            f"Max price: {max_price} at index: {max_index}"
        )

        min_proximity, max_proximity = self.calculate_proximities(current_price)
   
        price_change_percent = (max_price - min_price) / min_price * 100 if min_price and max_price else 0
        print(
            f"Price change percent: {price_change_percent:.2f} "
            f"slope: {slope:.4f} "
            f"Market trending: {'upwards' if slope > 0 else 'downwards'}"
        )

        if price_change_percent < threshold_percent and not u.are_close(price_change_percent, threshold_percent):
            action = 'HOLD'
            return action, current_price, price_change_percent, slope
            
        min_position, max_position = self.calculate_positions()
        if slope > 0: #slope > slope_normalized=0.0000833
            print("Market trending upwards")
            if max_position > 0.8 or u.are_close(max_position, 0.8, target_tolerance_percent=1.0):
                action = 'BUY'
                print(f"Near recent high. Action: {action}")
                proposed_price = current_price * 0.995
                print(f"Proposed price updated to {proposed_price} to be close to current price {current_price}")
                return action, proposed_price, price_change_percent, slope 
        else:
            print("Market trending downwards")
            if min_position < 0.2 or u.are_close(min_position, 0.2, target_tolerance_percent=1.0):
                action = 'SELL'
                print(f"Near recent low. Action: {action}")
                proposed_price = current_price * 1.005
                print(f"Proposed price updated  to {proposed_price} to be close to current price {current_price}")
                return action, proposed_price, price_change_percent, slope

        action = 'BUY'
        remaining_decrease_percent = max(0, decrease_percent - price_change_percent)
        print(f"Remaining decrease percent: {remaining_decrease_percent:.2f}")
        proposed_price = current_price * (1 - remaining_decrease_percent / 100)
        print(f"Trending not well defined propose price: {proposed_price:.2f} Action: {action}")
        return action, proposed_price, price_change_percent, slope

    def check_price_change(self, threshold):
        if len(self.prices) < 2:
            return 0, 1

        min_price, min_index = self.get_min_and_index()
        max_price, max_index = self.get_max_and_index()
        newest_price = self.prices[-1]
        newest_index = self.get_newest_index()

        price_diff_min = u.calculate_difference_percent(min_price, newest_price)
        price_diff_max = u.calculate_difference_percent(max_price, newest_price)

        grow = price_diff_max < price_diff_min  
        price_diff_newest = max(price_diff_min, price_diff_max)  #price_diff = max_price - min_price

        if abs(price_diff_newest) >= threshold or u.are_close(price_diff_newest, threshold):
            #todo use grow
            print(f'price_diff_minmax_versus_newest(slope)={price_diff_newest}(threshold={threshold}) are_close={u.are_close(price_diff_newest, threshold)}')
            print(f'min price ={min_price}, max_price = {max_price}, newest_price={newest_price}, min_index={min_index}, max_index={max_index}')
               
            return -price_diff_newest if price_diff_max > price_diff_min else price_diff_newest, 0
            return self._analyze_price_movement(min_price, min_index, max_price, max_index, 
                newest_price, newest_index, price_diff_newest)
        
        return 0, 0
    
    def _analyze_price_movement(self, min_price, min_index, max_price, max_index, newest_price, newest_index, price_diff):
        
     
        price_diff_min = u.calculate_difference_percent(min_price, newest_price)
        price_diff_max = u.calculate_difference_percent(max_price, newest_price)
        grow = price_diff_max < price_diff_min 
        
        slope_min = u.slope(min_price, min_index, newest_price, newest_index)
        slope_max = u.slope(max_price, max_index, newest_price, newest_index)
        #todo slope_min = pozitiv 
        # slope_max = negativ
        slope_max_min = slope_max if abs(slope_max) > abs(slope_min) else slope_min  #slope_max_min = u.slope(min_price, min_index, max_price, max_index)
        print(f"retun1 {slope_max_min}, {price_diff}")
        return slope_max_min, price_diff

        diff_min_max_close = u.are_close(price_diff_max, price_diff_min, 1.0)
        if diff_min_max_close:
            if min_index < max_index:
                grow = 1
                print(f"retun2 {-slope_max}, {price_diff}")
                return -slope_max, price_diff
            else:
                grow = 0
                print(f"retun3 {slope_min}, {price_diff}")
                return slope_min, price_diff

        min_position, max_position = self.calculate_positions()
        min_loc = 1  
        if min_position < 0.3 or u.are_close(min_position, 0.3):
            min_loc = 0  
        if min_position > 0.7 or u.are_close(min_position, 0.7):
            min_loc = 2  

        max_loc = 1  
        if max_position > 0.7 or u.are_close(max_position, 0.7):
            max_loc = 2  
        if max_position < 0.3 or u.are_close(max_position, 0.3):
            max_loc = 0  

        if grow:
            if min_loc == 0:  
                return slope_min, price_diff
            if min_loc == 1 and max_loc == 2:  
                return slope_max_min, price_diff
            else:
                print("OUTLIER!! but can indicate something will come!!!")
        else: #if not grow:
            if min_loc == 2:  
                return slope_max, price_diff
            if min_loc == 1 and max_loc == 0:  
                return slope_max_min, price_diff
            else:
                print("OUTLIER!! but can indicate something will come!!!")

        return 0, 1
    
            
            
    def current_window_size(self):
        return len(self.prices)

    def get_trend(self):
        analyzer = PriceTrendAnalyzer(self.prices)
        
        lin_trend = 0
        poly_trend = 0
        '''
        trend_line, slope, r_value = analyzer.linear_regression_trend()
        lin_trend = slope if slope is not None else 0
        print(f"Regresie Liniară: Slope = {slope:.2f} -> {'creștere' if slope > 0 else 'descreștere' if slope < 0 else 'nedefinit'}")
        
        poly_degree = 2
        trend_poly, poly_coef = analyzer.polynomial_regression_trend(degree=poly_degree)
        poly_trend = poly_coef[-1]
        print(f"Regresie Polinomială (grad {poly_degree}): Coeficient final = {poly_trend:.2f} -> {'creștere' if poly_trend > 0 else 'descreștere'}")
        '''
        
        # Media Mobilă Exponențială
        #ema_span = 5
        #ema = analyzer.exponential_moving_average(span=ema_span)
        ema_diff = 0
        #if len(ema) > 1:
        #    ema_diff = ema[-1] - ema[-2] 
        #print(f"Media Mobilă Exponențială: Ultima valoare EMA = {ema[-1]:.2f} -> {'creștere' if ema_diff > 0 else 'descreștere'}")

        # Gradientul
        gradient_lst, avg_gradient = analyzer.calculate_gradient()
        print(f"Gradient Mediu: {avg_gradient:.2f} -> {'creștere' if avg_gradient > 0 else 'descreștere'}")
        gradient = 0
        if len(gradient_lst) >= 3:
            gradient = np.mean(gradient_lst[:3])
        else:
            gradient = np.mean(gradient_lst)
        #print(f"Gradient local: {gradient:.2f} -> {'creștere' if gradient > 0 else 'descreștere'}")



        # Calculare vot final și coeficient de creștere
        trends = [#1 if slope > 0 else -1 if slope < 0 else 0,
                  #1 if poly_trend > 0 else -1,
                  1 if ema_diff > 0 else -1,
                  1 if gradient > 0 else -1,
                  1 if avg_gradient > 0 else -1]
        final_trend = sum(trends)
        final_trend = avg_gradient
        
        growth_coefficient = (lin_trend + poly_trend + ema_diff + gradient + avg_gradient) / len(trends)
        growth_coefficient = avg_gradient
        
        if final_trend > 0:
            final_trend = 1
        elif final_trend < 0:
            final_trend = -1
        else:
            final_trend = 0

        print(f"Tendință de {'creștere' if final_trend == 1 else 'descreștere' if final_trend == -1 else 'nedefinit'}")
        print(f"Coeficient : {growth_coefficient:.2f}")

        return final_trend, growth_coefficient

        
    

TIME_SLEEP_GET_PRICE = 0.4  # seconds to sleep for price collection
EXP_TIME_BUY_ORDER = (2.6 * 60) * 60 # dupa 1.6 ore
EXP_TIME_SELL_ORDER = EXP_TIME_BUY_ORDER
TIME_SLEEP_EVALUATE = TIME_SLEEP_GET_PRICE + 60  # seconds to sleep for buy/sell evaluation
# am voie 6 ordere per perioada de expirare care este 2.6 ore. deaceea am impartit la 6
TIME_SLEEP_PLACE_ORDER = TIME_SLEEP_EVALUATE + EXP_TIME_SELL_ORDER/ 6 + 4*79  # seconds to sleep for order placement
WINDOWS_SIZE_MIN = TIME_SLEEP_GET_PRICE + 3.7 * 60  # minutes
window_size = WINDOWS_SIZE_MIN / TIME_SLEEP_GET_PRICE

window_size_big = 2 * 60 * 60 / TIME_SLEEP_GET_PRICE
SELL_BUY_THRESHOLD = 5  # Threshold for the number of consecutive signals

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
            order = api.place_order_smart("BUY", symbol, adjusted_buy_price, order_quantity, cancelorders=True, hours=0.3, pair=True)
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
            order = api.place_order_smart("SELL", symbol, adjusted_sell_price, order_quantity, cancelorders=True, hours=0.3, pair=True)
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
        if time.time() < self.start_time + fresh_trend_time:
            return True
        print(f"No fresh trend! : {fresh_trend_time} vs. {self.start_time}")
        return False

    def is_trend_a_minim_validated(self) :
        return self.last_confirmation_time - self.start_time > 30 and self.confirm_count > 3
    
    def is_trend_consistent_validated(self) :
         #14 de confirmari per minut * 3 minute ->defapt 6 confirmari per minut  
        return self.confirm_count > 14 * 3 and self.is_trend_uniform_confirmed() # and  self.confirm_count < 100 * 3
    
    def is_trend_uniform_confirmed(self):
        if not self.is_trend_a_minim_validated() :
            return False
        
        trend_duration = self.get_started_trend_time() #self.get_confirmed_trend_duration()
        if trend_duration == 0:
            return False
        rate = self.confirm_count * TIME_SLEEP_GET_PRICE / trend_duration
        print(f"uniform rate is {rate} <> 0.1")
        #10 confirmari per 1.5 minute
        return rate > 0.1

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
initialize_csv_file(filename)

trades = apitrades.get_my_trades_24(order_type=None, symbol=sym.btcsymbol, days_ago=0, limit=1000)
print (f" --------- {len(trades)}");
print (f" my trades of today : {(trades)}");



def logic_small(win, enable, symbol, gradient, slope, trend_state) :

    d = 0
    h = 1
    proposed_price = current_price = api.get_current_price(symbol)
    
    print(f" SE ACTIVEAZA DUPA 3.5 la slope: gradient={gradient}, slope={slope}")
    if gradient < 0 and slope < -3.5:
        if enable:
            #api.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
            #    force=True, cancelorders=False, hours=1)
            print(f"FINISH FORCE place_order_smart SELL")
    if gradient > 0 and slope > 3.5:
        if enable:
            #api.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
            #    force=True, cancelorders=False, hours=1)
            print(f"FINISH FORCE place_order_smart BUY")



def logic(win, enable, symbol, gradient, slope, trend_state) :

    d = 14
    h = 24
    proposed_price = current_price = api.get_current_price(symbol)
    
    print(f"LOGIC gradient={gradient}, slope={slope}")
    # if gradient < 0 and slope < 0 :
        # if enable:
            # api.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                # force=True, cancelorders=True, hours=1)
            # print(f"FINISH place_order_smart SELL")
    # if gradient > 0 and slope > 0 :
        # if enable:
            # api.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
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
                    api.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                        force=True, cancelorders=True, hours=1)
                print(f"place_order_smart BUY")
        else:
            old_trend = trend_state.start_trend('UP')  # Incepem un trend nou de crestere
            #track_and_place_order('BUY', sym.btcsymbol, 1, proposed_price, current_price, order_ids=order_ids)
            #api.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=16*3600+60,
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
                    api.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                        force=True, cancelorders=True, hours=1)
                print(f"place_order_smart SELL")
        else:
            old_trend = trend_state.start_trend('DOWN')  # Incepem un trend nou de scadere
            #track_and_place_order('SELL', symbol, 1, proposed_price, current_price, order_ids=order_ids)
            #api.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=16*3600+60,
            #    force=True, cancelorders=True, hours=1)

    proposed_price = current_price
    #18 de confirmari per minut * 3 minute ->defapt 6 confirmari per minut
    if slope <= 0 and trend_state.is_trend_up():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE BUY ALL {win} .... ")
            if enable:
                api.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=True, cancelorders=True, hours=1)
    #18 de confirmari per minut * 3 minute
    if slope >= 0 and trend_state.is_trend_down():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE SELL ALL {win} .... ")
            if enable:
                api.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=True, cancelorders=True, hours=1)
                    
    #
    #new case
    #
    if slope <= -5.4 and trend_state.is_trend_up():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 2: BUY ALL {win} .... ")
            if enable:
                api.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=True, cancelorders=True, hours=1)
    #18 de confirmari per minut * 3 minute
    if slope >= 5.4 and trend_state.is_trend_down():
        if (trend_state.is_trend_consistent_validated()
        or trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 2: SELL ALL {win} .... ")
            if enable:
                api.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=True, cancelorders=True, hours=1)
                                                                                                                                                                 
    #
    #new case
    #
    if slope <= -5.4 and trend_state.is_trend_down():
        if (trend_state.is_trend_consistent_validated()
        and trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 3: BUY ALL {win} .... ")
            if enable:
                api.place_order_smart("BUY", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=True, cancelorders=True, hours=1)
    #18 de confirmari per minut * 3 minute
    if slope >= 5.4 and trend_state.is_trend_up():
        if (trend_state.is_trend_consistent_validated()
        and trend_state.is_started_trend_older_than(TREND_TO_BE_OLD_SECONDS)) :
            print(f"ATENTIE 3: SELL ALL {win} .... ")
            if enable:
                api.place_order_smart("SELL", symbol, proposed_price, api.quantities[symbol], safeback_seconds=d*h*3600+60,
                    force=True, cancelorders=True, hours=1)
   
#todo ia acceleratiea pe timp scurt get minute 1-3 si daca e mare cumpara!   


# Function to handle the price logic for a specific currency
def handle_symbol(symbol, current_price, price_window, price_window_big, trend_state, trend_state_big):
  
    count = 0

    price_window.process_price(current_price)
    price_window_big.process_price(current_price)

    slope, pos = price_window.check_price_change(PRICE_CHANGE_THRESHOLD_EUR)
    gradient, gradient_coff = price_window.get_trend()

    if(slope * gradient < 0):
        print(f"ALERT slope1 = {slope} gradient = {gradient}")
    if(slope * price_window.calculate_slope_max_min() < 0):
        print(f"ALERT slope2 = {slope} calculate_slope_max_min() = {price_window.calculate_slope_max_min()}")
    if(gradient * price_window.calculate_slope_max_min() < 0):
        print(f"ALERT gradient = {gradient} calculate_slope_max_min() = {price_window.calculate_slope_max_min()}")
    if slope == 0:
        count = count + 1
    else:
        count = 0
    #gradient = price_window.calculate_slope_max_min()
    
    # SMALL ONE!!
    #logic("SMALL" ,True, symbol, gradient, slope, trend_state)
    logic_small("SMALL" ,True, symbol, gradient, slope, trend_state)
    #
    
    #
    # BIG ONE!!!
    #
    slope_big, _ = price_window_big.check_price_change(PRICE_CHANGE_THRESHOLD_BIG_EUR)
    
    #if symbol in sym.symbols:
    logic("BIG", True, symbol, gradient, slope_big, trend_state_big)
    
    
    update_csv_file(filename, symbol, slope_big, count, 0, 0, pos, gradient)
        
    for moneda in web.monede:
        if moneda["nume"] == symbol:
            moneda["watch"] = True if slope_big != 0 else False


    # web.monede[0]["watch"] = True # for debug

#
#       MAIN 
#

order_ids = []
TREND_TO_BE_OLD_SECONDS = 60 * 60 * 1.5  # 1.5h -> 2.5h  
#todo put that threshold per PriceWindow
PRICE_CHANGE_THRESHOLD_EUR = u.calculate_difference_percent(60000, 60000 - 310)
PRICE_CHANGE_THRESHOLD_BIG_EUR = u.calculate_difference_percent(97000, 95000 - 377)

price_windows = {}
price_windows_big = {}
trend_states = {}
trend_states_big = {}
# First loop: Create instances for each symbol
for symbol in sym.symbols:
    #symbol = moneda["nume"]
    price_windows[symbol] = PriceWindow(symbol, window_size)
    price_windows_big[symbol] = PriceWindow(symbol, window_size_big)
    trend_states[symbol] = TrendState(max_duration_seconds= 2.5 * 60 * 60, expiration_trend_time=10 * 60, fresh_trend_time = 1.7 * 60)  # Expira In 10 minute
    trend_states_big[symbol] = TrendState(max_duration_seconds= 3 * 60 * 60, expiration_trend_time=10 * 60, fresh_trend_time = 1.7 * 60)  # Expira In 10 minute

TIME_SLEEP_BETWEEN_SYMBOLS=0#TIME_SLEEP_GET_PRICE
# Second loop: Call handle_symbol for each symbol indefinitely

print(f"Quantities: {api.quantities}")

while True:
    
    time.sleep(TIME_SLEEP_GET_PRICE)
    time.sleep(TIME_SLEEP_GET_PRICE)    
    print(f"----------------------------------")
    for symbol in sym.symbols:
        #symbol = moneda["nume"]
        print(f"")
        # Get the appropriate price window and trend state for the symbol
        price_window = price_windows[symbol]
        price_window_big = price_windows_big[symbol]
        trend_state = trend_states[symbol]
        trend_state_big = trend_states_big[symbol]
    
         # get the price for the current symbol
        time.sleep(TIME_SLEEP_BETWEEN_SYMBOLS)
        current_price = api.get_current_price(symbol) #current_time = time.time()
        if current_price is None:
            time.sleep(TIME_SLEEP_GET_PRICE)
            continue
        
        # Call handle_symbol for the current symbol    
        handle_symbol(symbol, current_price, price_window, price_window_big, trend_state, trend_state_big)
    
    # Generare și salvare
    html_content = web.genereaza_html(web.monede)
    web.salveaza_html(html_content, "index.html")
                    

#try
#except BinanceAPIException as e:
    #print(f"Binance API Error: {e}")
    #time.sleep(TIME_SLEEP_GET_PRICE)
#except Exception as e:
    #print(f"Error: {e}")
    #time.sleep(TIME_SLEEP_GET_PRICE)

