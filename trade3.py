import time
import datetime
import math
from binance.client import Client
from binance.exceptions import BinanceAPIException
from collections import deque

from apikeys import api_key, api_secret

# my imports

import binanceapi as api
import binanceapi_trades as apitrades
import binanceapi_allorders as apiorders
import log
import alert
import utils
import utils as u
import priceprediction as pp

import pandas as pd
import os


import numpy as np
import matplotlib.pyplot as plt
from scipy.stats import linregress
from sklearn.preprocessing import PolynomialFeatures
from sklearn.linear_model import LinearRegression

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


class PriceWindow:
    def __init__(self, window_size, max_index=230, epsilon=1e-2):
        self.window_size = window_size
        self.prices = deque()  # Store all prices in the window
        self.min_deque = deque()  # Manage the minimums
        self.max_deque = deque()  # Manage the maximums
        self.current_index = 0  # Internal counter to keep track of index
        self.max_index = max_index  # Threshold for normalization
        self.epsilon = 10  # Tolerance for approximately equal minimums
        

    def process_price(self, price):
        self.prices.append(price)
        if len(self.prices) > self.window_size:
            removed_price = self.prices.popleft()
            #print(f"Price removed from window: {removed_price}")
        self._manage_minimum(price)
        self._manage_maximum(price)

        self.current_index += 1
        print(f"BTC: {round(price, 0):.0f} Current index in window: {self.current_index}")
       
        #if self.current_index > self.max_index:
            #print(f"Start normalize indexes")
            # self._normalize_indices()

    def _normalize_indices(self):
        old_index = self.current_index
        self.current_index = 0  # Reset current index

        # Verifică indecșii din min_deque
        print("idecsi dubiosi1:")
        for price, i in self.min_deque:
            if i < old_index - self.window_size:
                print(f"  Price: {price}, Index: {i}")

        # Verifică indecșii din max_deque
        print("idecsi dubiosi2:")
        for price, i in self.max_deque:
            if i < old_index - self.window_size:
                print(f"  Price: {price}, Index: {i}")

        self.min_deque = deque([(price, i - (old_index - self.window_size + 1))
                                 for price, i in self.min_deque if i >= old_index - self.window_size])

        # Normalizează max_deque
        self.max_deque = deque([(price, i - (old_index - self.window_size + 1))
                                 for price, i in self.max_deque if i >= old_index - self.window_size])


        # Verifică dacă există indexuri negative
        negative_min_indices = [i for i, _ in self.min_deque if i < 0]
        negative_max_indices = [i for i, _ in self.max_deque if i < 0]

        if negative_min_indices:
            print(f"Warning: Negative indices found in min_deque: {negative_min_indices}")

        if negative_max_indices:
            print(f"Warning: Negative indices found in max_deque: {negative_max_indices}")

        print(f"Indices normalized. Old index: {old_index}, New index: {self.current_index}")


    def _manage_minimum(self, price):
        if self.min_deque and self.min_deque[0][1] <= self.current_index - self.window_size:
            removed_min = self.min_deque.popleft()
            print(f"Minimum removed as it is outside the window: {removed_min}")

        #do this also for max
        #for existing_price, index in self.min_deque:
        #    if abs(existing_price - price) <= self.epsilon: 
        #        return  # Don't add the current price if an equivalent exists

        while self.min_deque and self.min_deque[-1][0] > price:
            removed_min = self.min_deque.pop()
            #print(f"Minimums removed from back: {removed_min}")

        self.min_deque.append((price, self.current_index))

    def _manage_maximum(self, price):
        if self.max_deque and self.max_deque[0][1] <= self.current_index - self.window_size:
            removed_max = self.max_deque.popleft()
            print(f"Maximum removed as it is outside the window: {removed_max}")

        while self.max_deque and self.max_deque[-1][0] <= price:
            removed_max = self.max_deque.pop()
            #print(f"Maximums removed from back: {removed_max}")

        self.max_deque.append((price, self.current_index))

    def get_min(self):
        if not self.min_deque:
            return None
        return self.min_deque[0][0]  # Return the minimum price

    def get_max(self):
        if not self.max_deque:
            return None
        return self.max_deque[0][0]  # Return the maximum price

    def get_min_and_index(self):
        if not self.min_deque:
            return None
        return self.min_deque[0][0], self.min_deque[0][1]  # Return min price and its index

    def get_max_and_index(self):
        if not self.max_deque:
            return None
        return self.max_deque[0][0], self.max_deque[0][1]  # Return max price and its index

    def calculate_slope(self):
        min_price = self.get_min()
        max_price = self.get_max()

        if min_price is None or max_price is None:
            return 0

        min_index = self.min_deque[0][1]
        max_index = self.max_deque[0][1]

        if max_index == min_index:
            return 0

        price_change_percent = (max_price - min_price) / min_price
        slope_normalized = price_change_percent / (max_index - min_index)
        return slope_normalized



    def calculate_proximities(self, current_price):
        min_price, _ = self.get_min_and_index()
        max_price, _ = self.get_max_and_index()

        if max_price != min_price:
            min_proximity = (current_price - min_price) / (max_price - min_price)
            max_proximity = (max_price - current_price) / (max_price - min_price)

            # Verificare pentru valori negative si declansare excepție
            if min_proximity < 0 or max_proximity < 0:
                print(f"Negative proximity detected! min_proximity: {min_proximity}, max_proximity: {max_proximity}")
                min_proximity = max_proximity = 0
                sys.exit(1)
        else:
            min_proximity = max_proximity = 0 # aprope total de min si de max
        #min_proximity + max_proximity = 1 
        #daca min_proximity -> 0 inseamna ca pretul este mai aprope de min
        #daca max_proximity -> 0 inseamna ca pretul este mai aprope de max

        return min_proximity, max_proximity

    def calculate_positions(self):
        min_index = self.min_deque[0][1] if self.min_deque else 0
        max_index = self.max_deque[0][1] if self.max_deque else 0
        min_position = (min_index % self.window_size) / self.window_size
        max_position = (max_index % self.window_size) / self.window_size
        return min_position, max_position

        
    def evaluate_buy_sell_opportunity(self, current_price, threshold_percent=1, decrease_percent=3.7):
        slope = self.calculate_slope()
     
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

        if price_change_percent < threshold_percent and not utils.are_values_very_close(price_change_percent, threshold_percent):
            action = 'HOLD'
            return action, current_price, price_change_percent, slope
            
        min_position, max_position = self.calculate_positions()
        if slope > 0: #slope > slope_normalized=0.0000833
            print("Market trending upwards")
            if max_position > 0.8 or utils.are_values_very_close(max_position, 0.8, target_tolerance_percent=1.0):
                action = 'BUY'
                print(f"Near recent high. Action: {action}")
                proposed_price = current_price * 0.995
                print(f"Proposed price updated to {proposed_price} to be close to current price {current_price}")
                return action, proposed_price, price_change_percent, slope 
        else:
            print("Market trending downwards")
            if min_position < 0.2 or utils.are_values_very_close(min_position, 0.2, target_tolerance_percent=1.0):
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
        #oldest_price = self.prices[0]
        newest_price = self.prices[-1]
        #price_diff = max_price - min_price
        price_diff_min = u.calculate_difference_percent(min_price, newest_price)
        price_diff_max = u.calculate_difference_percent(max_price, newest_price)
        grow = price_diff_max < price_diff_min # pret curent inspre max
        price_diff = max(price_diff_min, price_diff_max) ## check if abs(price_diff_min,price_diff_max) > treshold
        if abs(price_diff) >= threshold or utils.are_values_very_close(price_diff, threshold) :
            min_position, max_position = self.calculate_positions()
            position = min(min_position, max_position)
            position = min_position if grow else max_position
            if(position < 0.5 or utils.are_values_very_close( position, 0.5)):                
                return u.slope(min_price, min_index, newest_price, self.current_index), position #2.76 pt ungi de 70
            else:
                print("OUTLAIER!! but can indicate something will came !!!!")
        else:
            return 0, 1
            
        return 0, 1
            
            
    def current_window_size(self):
        return len(self.prices)

    def get_trend(self):
        analyzer = PriceTrendAnalyzer(self.prices)
         # Regresie liniară
        trend_line, slope, r_value = analyzer.linear_regression_trend()
        if not slope is None:
            lin_trend = "creștere" if slope > 0 else "descreștere"
            print(f"Regresie Liniară: Slope = {slope:.2f}, R = {r_value:.2f} -> Tendință estimată de {lin_trend}")
        else :
            lin_trend = "nedefinit"
            print(f"Regresie Liniară:  -> Tendință estimată de {lin_trend}")

        # Regresie polinomială
        poly_degree=2; ema_span=5
        trend_poly, poly_coef = analyzer.polynomial_regression_trend(degree=poly_degree)
        poly_trend = "creștere" if poly_coef[-1] > 0 else "descreștere"
        print(f"Regresie Polinomială (grad {poly_degree}): Coeficient final = {poly_coef[-1]:.2f} -> Tendință de {poly_trend}")

        # Media Mobilă Exponențială
        ema = analyzer.exponential_moving_average(span=ema_span)
        print(f"Media Mobilă Exponențială: Ultima valoare EMA = {ema[-1]:.2f}")

        # Gradientul
        gradient, avg_gradient = analyzer.calculate_gradient()
        grad_trend = "1" if avg_gradient > 0 else "-1"
        print(f"Gradient Mediu: {avg_gradient:.2f} -> Tendință locală de {grad_trend}")

        return grad_trend
        
    

TIME_SLEEP_GET_PRICE = 2  # seconds to sleep for price collection
EXP_TIME_BUY_ORDER = (2.6 * 60) * 60 # dupa 1.6 ore
EXP_TIME_SELL_ORDER = EXP_TIME_BUY_ORDER
TIME_SLEEP_EVALUATE = TIME_SLEEP_GET_PRICE + 60  # seconds to sleep for buy/sell evaluation
# am voie 6 ordere per perioada de expirare care este 2.6 ore. deaceea am impartit la 6
TIME_SLEEP_PLACE_ORDER = TIME_SLEEP_EVALUATE + EXP_TIME_SELL_ORDER/ 6 + 4*79  # seconds to sleep for order placement
WINDOWS_SIZE_MIN = TIME_SLEEP_GET_PRICE + 7.7 * 60  # minutes
window_size = WINDOWS_SIZE_MIN / TIME_SLEEP_GET_PRICE

window_size2 = 2 * 60 * 60 / TIME_SLEEP_GET_PRICE
SELL_BUY_THRESHOLD = 5  # Threshold for the number of consecutive signals

def track_and_place_order(action, count, proposed_price, current_price, quantity=0.017/2, order_ids=None):
    
    print(f"Iteration {count} generated price {proposed_price} versus {current_price}")
                    
    if order_ids is None:
        order_ids = []

    if action == 'HOLD':
        return order_ids

    # Cancel any existing orders
    if order_ids:
        for order_id in order_ids:
            if not api.cancel_order(api.symbol, order_id):
                alert.check_alert(True, f"Order executed! be Happy :-){order_id:.2f}")
        order_ids.clear()
        
    api.cancel_expired_orders(action, api.symbol, EXP_TIME_BUY_ORDER if action == 'BUY' else EXP_TIME_SELL_ORDER)
        
    num_orders, price_step = (2, 0.2) if action == "BUY" else (3, 0.08)

    # Price is rising, place fewer, larger orders. # Increase the spacing between orders as percents
    # Price is falling, place more, smaller orders # Reduce the spacing between orders as percents
   
    if action == 'BUY':
        api.cancel_expired_orders(action, api.symbol, EXP_TIME_BUY_ORDER)

        buy_price = min(proposed_price, current_price * 0.998)
        print(f"BUY price: {buy_price:.2f} USDT")

        alert.check_alert(True, f"BUY order {buy_price:.2f}")
       
        # Place the custom buy orders
        for i in range(num_orders):
            adjusted_buy_price = buy_price * (1 - i * price_step / 100)
            order_quantity = quantity / num_orders  # Divide quantity among orders
            print(f"Placing buy order at price: {adjusted_buy_price:.2f} USDT for {order_quantity:.6f} BTC")
            order = api.place_order_smart("buy", api.symbol, adjusted_buy_price, order_quantity, cancelorders=True, hours=0.3, pair=True)
            if order:
                print(f"Buy order placed successfully with ID: {order['orderId']}")
                order_ids.append(order['orderId']) 

    elif action == 'SELL':
        api.cancel_expired_orders(action, api.symbol, EXP_TIME_SELL_ORDER)

        sell_price = max(proposed_price, current_price * 1.002)
        print(f"SELL price: {sell_price:.2f} USDT")
        
        alert.check_alert(True, f"SELL order {sell_price:.2f}")

        # Place the custom sell orders
        for i in range(num_orders):
            adjusted_sell_price = sell_price * (1 + i * price_step / 100)
            order_quantity = quantity / num_orders  # Divide quantity among orders
            print(f"Placing sell order at price: {adjusted_sell_price:.2f} USDT for {order_quantity:.6f} BTC")
            order = api.place_order_smart("sell", api.symbol, adjusted_sell_price, order_quantity, cancelorders=True, hours=0.3, pair=True)
            if order:
                print(f"Sell order placed successfully with ID: {order['orderId']}")
                order_ids.append(order['orderId']) 

    return order_ids



class TrendState:
    def __init__(self, max_duration_seconds, expiration_threshold):
        self.state = 'HOLD'  # Inițial, starea este 'HOLD'
        self.old_state = self.state 
        self.expired = False
        self.start_time = None  # Timpul de Inceput al trendului
        self.end_time = None  # Timpul de sfârsit al trendului
        self.last_confirmation_time = None  # Ultimul timp de confirmare al trendului
        self.max_duration_seconds = max_duration_seconds  # Durata maxima permisa pentru un trend
        self.confirm_count = 0  # Contorul de confirmari pentru trend
        self.expiration_threshold = expiration_threshold  # Pragul de timp Intre confirmari (In secunde)

    def start_trend(self, new_state):
        
        #self.end_trend()  # Marcheaza sfârsitul trendului anterior
        
        self.state = new_state
        self.start_time = time.time()
        self.last_confirmation_time = self.start_time
        self.confirm_count = 1  # Prima confirmare
        self.end_time = None  # Resetam timpul de sfârsit
        self.expired = False
        print(f"Start of {self.state} trend at {u.timeToHMS(self.start_time)}")
        return self.old_state

    def confirm_trend(self):
        self.last_confirmation_time = time.time()
        self.confirm_count += 1
        print(f"{self.confirm_count} times trend confirmed: {self.state} at {u.timeToHMS(self.last_confirmation_time)}")
        return self.confirm_count

    def check_trend_expiration(self):
        if self.expired :
            return True
        if self.last_confirmation_time:
            time_since_last_confirmation = time.time() - self.last_confirmation_time
            if time_since_last_confirmation > self.expiration_threshold:
                print(f"Trend expired: {self.state}. Time since last confirmation: {time_since_last_confirmation} seconds")
                self.end_trend()
                self.expired = True
                return True
        return False

    def end_trend(self):
        self.old_state = self.state
        self.end_time = self.last_confirmation_time  # Timpul de sfârsit al trendului este ultimul timp de confirmare
        print(f"Trend ended: {self.state} at {u.timeToHMS(self.end_time)} after {self.confirm_count} confirmations.")
        self.old_confirm_count = self.confirm_count
        self.confirm_count = 0
  
    def is_trend_up(self):
        if not self.confirm_count : 
            return 0
        if not  self.check_trend_expiration() and self.state == 'UP':
            return self.confirm_count
        return 0

    def is_trend_down(self):
        if not self.confirm_count : 
            return 0
        if not self.check_trend_expiration() and self.state == 'DOWN':
            return self.confirm_count
        return 0

    def is_hold(self):
        if self.check_trend_expiration() or self.state == 'HOLD':
            return self.confirm_count
        return 0
        
trend_state1 = TrendState(max_duration_seconds= 2 * 60 * 60, expiration_threshold=10 * 60)  # Expira In 10 minute
trend_state2 = TrendState(max_duration_seconds= 2 * 60 * 60, expiration_threshold=10 * 60)  # Expira In 10 minute

#
#       MAIN 
#

#alert.check_alert(True, f"SELL order ")
  

price_window = PriceWindow(window_size)
prediction = pp.PricePrediction(10)  

order_ids = []
last_order_time = time.time()
last_evaluate_time = time.time()




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
    

PRICE_CHANGE_THRESHOLD_EUR = u.calculate_difference_percent(60000, 60000 - 290)

count = 0
    
while True:
    #try:
        time.sleep(TIME_SLEEP_GET_PRICE)

        current_time = time.time()
        current_price = api.get_current_price(api.symbol)
        if current_price is None:
            time.sleep(TIME_SLEEP_GET_PRICE)
            continue

        price_window.process_price(current_price)
        #prediction.process_price(current_price)
        #ppredict = prediction.predict_next_price()
        #print(f"predicted price : {ppredict}")
           
        action, proposed_price, price_change_percent, slope = price_window.evaluate_buy_sell_opportunity(
            current_price, threshold_percent=0.8, decrease_percent=7
        )

        # expired_trend = 'HOLD'
        # if action == 'BUY':
            # if trend_state1.is_trend_up():
                # trend_state1.confirm_trend()
            # else:
                # expired_trend = trend_state1.start_trend('UP')
        # if action == 'SELL':
            # if trend_state1.is_trend_down():
                # trend_state1.confirm_trend()  
            # else:
                # expired_trend = trend_state1.start_trend('DOWN')
        # if action == "HOLD":
           # if trend_state1.is_hold():
              # trend_state1.confirm_trend()
        
        # if trend_state1.is_trend_up() == 3:
            # track_and_place_order('BUY', proposed_price, current_price, order_ids=order_ids)   
        # if trend_state1.is_trend_down() == 3:
            # track_and_place_order('SELL', proposed_price, current_price, order_ids=order_ids)   

        #
        # Verificam schimbarile de preț si gestionam trendurile
        #
        proposed_price = current_price
       
        slope, pos = price_window.check_price_change(PRICE_CHANGE_THRESHOLD_EUR)
        gradient = price_window.get_trend()
        if slope == 0:
            count = count + 1
        else :
            count = 0
        
        if slope > 0:
            # Confirmam un trend de crestere
            initial_difference = 247 * (pos + 0.5)/abs(slope)
            print(f"DIFERENTA MARE UP! DIFF start {initial_difference}")
            if trend_state2.is_trend_up():
                count = trend_state2.confirm_trend() # Confirmam ca trendul de crestere continua
                diff, _ = u.decrese_value_by_increment_exp(initial_difference, count)
                proposed_price = current_price - diff
                track_and_place_order('BUY', count, proposed_price, current_price, order_ids=order_ids)
            else:
                expired_trend = trend_state2.start_trend('UP')  # Incepem un trend nou de crestere
                proposed_price = current_price - initial_difference
                track_and_place_order('BUY',1, proposed_price, current_price, order_ids=order_ids)          
        elif slope < 0:
            # Confirmam un trend de scadere
            initial_difference = 447  * (pos + 0.5) /abs(slope)
            print(f"DIFERENTA MARE DOWN! DIFF start {initial_difference}")
            if trend_state2.is_trend_down():
                count = trend_state2.confirm_trend() # Confirmam ca trendul de scadere continua
                diff, _ = u.decrese_value_by_increment_exp(initial_difference, count)
                proposed_price = proposed_price + diff
                track_and_place_order('SELL', count, proposed_price, current_price, order_ids=order_ids)
            else:
                expired_trend = trend_state2.start_trend('DOWN')  # Incepem un trend nou de scadere
                proposed_price = current_price + initial_difference
                #track_and_place_order('SELL',1, proposed_price, current_price, order_ids=order_ids)

        update_csv_file(filename, api.symbol, slope, count, 0, 0, pos, gradient)
        update_csv_file(filename, 'TAOUSDT', slope, count, 0, 0, pos, gradient)
            
        

    #except BinanceAPIException as e:
        #print(f"Binance API Error: {e}")
        #time.sleep(TIME_SLEEP_GET_PRICE)
    #except Exception as e:
        #print(f"Error: {e}")
        #time.sleep(TIME_SLEEP_GET_PRICE)

