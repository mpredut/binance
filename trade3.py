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


class PriceWindow:
    def __init__(self, window_size, max_index=10000, epsilon=1e-2):
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
       
        if self.current_index > self.max_index:
            print(f"Start normalize indexes")
            self._normalize_indices()

    def _normalize_indices(self):
        old_index = self.current_index
        self.current_index = 0  # Reset current index

        self.min_deque = deque([(i - old_index, price) for price, i in self.min_deque if i >= old_index])
        self.max_deque = deque([(i - old_index, price) for price, i in self.max_deque if i >= old_index])

        print(f"Indices normalized. Old index: {old_index}, New index: {self.current_index}")

    def _manage_minimum(self, price):
        if self.min_deque and self.min_deque[0][1] <= self.current_index - self.window_size:
            removed_min = self.min_deque.popleft()
            print(f"Minimum removed as it is outside the window: {removed_min}")

        for existing_price, index in self.min_deque:
            if abs(existing_price - price) <= self.epsilon: 
                return  # Don't add the current price if an equivalent exists

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

            # Verificare pentru valori negative și declanșare excepție
            if min_proximity < 0 or max_proximity < 0:
                printf(f"Negative proximity detected! min_proximity: {min_proximity}, max_proximity: {max_proximity}")
                sys.exit(1)
        else:
            min_proximity = max_proximity = 0

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
            f"slope: {slope:.2f} "
            f"Market trending: {'upwards' if slope > 0 else 'downwards'}"
        )

        remaining_decrease_percent = max(0, decrease_percent - price_change_percent)
        print(f"Remaining decrease percent: {remaining_decrease_percent:.2f}")
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
                print(f"Proposed price updated  to {proposed_price} to be close to current price {current_price}")
        else:
            print("Market trending downwards")
            if min_position < 0.2 or utils.are_values_very_close(min_position, 0.2, target_tolerance_percent=1.0):
                action = 'SELL'
                print(f"Near recent low. Action: {action}")
                proposed_price = current_price * 1.005
                print(f"Proposed price updated  to {proposed_price} to be close to current price {current_price}")

        action = 'BUY'
        proposed_price = current_price * (1 - remaining_decrease_percent / 100)
        print(f"Trending not well defined propose price: {proposed_price:.2f} Action: {action}")
        
        return action, proposed_price, price_change_percent, slope

    def check_price_change(self, threshold):
        if len(self.prices) < 2:
            return None
        oldest_price = self.prices[0]
        newest_price = self.prices[-1]
        price_diff = newest_price - oldest_price
        if abs(price_diff) >= threshold or utils.are_values_very_close(price_diff, threshold) :
            return price_diff
        else:
            return None
            
    def current_window_size(self):
        return len(self.prices)


TIME_SLEEP_GET_PRICE = 2  # seconds to sleep for price collection
EXP_TIME_BUY_ORDER = (2.6 * 60) * 60 # dupa 1.6 ore
EXP_TIME_SELL_ORDER = EXP_TIME_BUY_ORDER
TIME_SLEEP_EVALUATE = TIME_SLEEP_GET_PRICE + 60  # seconds to sleep for buy/sell evaluation
# am voie 6 ordere per perioada de expirare care este 2.6 ore. deaceea am impartit la 6
TIME_SLEEP_PLACE_ORDER = TIME_SLEEP_EVALUATE + EXP_TIME_SELL_ORDER/ 6 + 4*79  # seconds to sleep for order placement
WINDOWS_SIZE_MIN = TIME_SLEEP_GET_PRICE + 5  # minutes
window_size = WINDOWS_SIZE_MIN * 60 / TIME_SLEEP_GET_PRICE

window_size2 = 2 * 60 * 60 / TIME_SLEEP_GET_PRICE
SELL_BUY_THRESHOLD = 5  # Threshold for the number of consecutive signals


def track_and_place_order(action, proposed_price, current_price, slope, quantity=0.017/2, order_placed=False, order_id=None):
    
    if action == 'HOLD':
        return order_placed, order_id
        
    # Determine the number of orders and their spacing based on price trend
    if slope is not None and slope > 0:
        # Price is rising, place fewer, larger orders
        num_orders = 3
        price_step = 0.2  # Increase the spacing between orders as procents
        print(f"Placing fewer, {num_orders} larger orders due to rising price.")
    else:
        # Price is falling, place more, smaller orders
        num_orders = 3
        price_step = 0.08  # Reduce the spacing between orders as procents
        print(f"Placing more, {num_orders} smaller orders due to falling price.")

    if action == 'BUY':
        api.cancel_expired_orders("buy", api.symbol, EXP_TIME_BUY_ORDER)

        buy_price = min(proposed_price, current_price * 0.998)
        print(f"BUY price: {buy_price:.2f} USDT")

        alert.check_alert(True, f"BUY order {buy_price:.2f}")
       
        # Place the custom buy orders
        for i in range(num_orders):
            adjusted_buy_price = buy_price * (1 - i * price_step / 100)
            order_quantity = quantity / num_orders  # Divide quantity among orders
            print(f"Placing buy order at price: {adjusted_buy_price:.2f} USDT for {order_quantity:.6f} BTC")
            order = api.place_order_smart("buy", api.symbol, adjusted_buy_price, order_quantity)
            if order:
                print(f"Buy order placed successfully with ID: {order['orderId']}")
                order_placed = True
                order_id = order['orderId']

    elif action == 'SELL':
        api.cancel_expired_orders("sell", api.symbol, EXP_TIME_SELL_ORDER)

        sell_price = max(proposed_price, current_price * 1.002)
        print(f"SELL price: {sell_price:.2f} USDT")
        
        alert.check_alert(True, f"SELL order {sell_price:.2f}")

        # Place the custom sell orders
        for i in range(num_orders):
            adjusted_sell_price = sell_price * (1 + i * price_step / 100)
            order_quantity = quantity / num_orders  # Divide quantity among orders
            print(f"Placing sell order at price: {adjusted_sell_price:.2f} USDT for {order_quantity:.6f} BTC")
            order = api.place_order_smart("sell", api.symbol, adjusted_sell_price, order_quantity)
            if order:
                print(f"Sell order placed successfully with ID: {order['orderId']}")
                order_placed = True
                order_id = order['orderId']

    return order_placed, order_id  # Return the updated order state


import time

class TrendState:
    def __init__(self, max_duration_seconds, expiration_threshold):
        self.state = 'HOLD'  # Inițial, starea este 'HOLD'
        self.old_state = elf.state 
        self.start_time = None  # Timpul de început al trendului
        self.end_time = None  # Timpul de sfârșit al trendului
        self.last_confirmation_time = None  # Ultimul timp de confirmare al trendului
        self.max_duration_seconds = max_duration_seconds  # Durata maximă permisă pentru un trend
        self.confirm_count = 0  # Contorul de confirmări pentru trend
        self.expiration_threshold = expiration_threshold  # Pragul de timp între confirmări (în secunde)

    def start_trend(self, new_state):
        
        self.end_trend()  # Marchează sfârșitul trendului anterior
        
        self.state = new_state
        self.start_time = time.time()
        self.last_confirmation_time = self.start_time
        self.confirm_count = 1  # Prima confirmare
        self.end_time = None  # Resetăm timpul de sfârșit
        print(f"Trend started: {self.state} at {time.ctime(self.start_time)}")
        return self.old_state

    def confirm_trend(self):
        self.last_confirmation_time = time.time()
        self.confirm_count += 1
        print(f"Trend confirmed: {self.state} at {time.ctime(self.last_confirmation_time)}")

    def check_trend_expiration(self):
        """Verifică dacă trendul a expirat din cauza lipsei confirmărilor în intervalul permis."""
        if self.last_confirmation_time:
            time_since_last_confirmation = time.time() - self.last_confirmation_time
            if time_since_last_confirmation > self.expiration_threshold:
                print(f"Trend expired: {self.state}. Time since last confirmation: {time_since_last_confirmation} seconds")
                end_trend()
                return True
        return False

    def end_trend(self):
        self.old_state = self.state
        self.end_time = self.last_confirmation_time  # Timpul de sfârșit al trendului este ultimul timp de confirmare
        self.confirm_count = 0
        print(f"Trend ended: {self.state} at {time.ctime(self.end_time)} after {self.confirm_count} confirmations.")
  
    def is_trend_up(self):
        if not check_trend_expiration(self) and self.state == 'UP':
            return confirm_count
        return 0

    def is_trend_down(self):
         if not check_trend_expiration(self) and self.state == 'DOWN':
            return confirm_count
        return 0

    def is_hold(self):
       if check_trend_expiration(self) or self.state == 'HOLD':
            return confirm_count
        return 0
        
trend_state1 = TrendState(max_duration_seconds= 2 * 60 * 60, expiration_threshold=10 * 60)  # Expiră în 10 minute
trend_state2 = TrendState(max_duration_seconds= 2 * 60 * 60, expiration_threshold=10 * 60)  # Expiră în 10 minute

#
#       MAIN 
#

alert.check_alert(True, f"SELL order ")
  

price_window = PriceWindow(window_size)

order_placed = False
order_id = None
last_order_time = time.time()
last_evaluate_time = time.time()


PRICE_CHANGE_THRESHOLD_EUR = 260

while True:
    try:
        time.sleep(TIME_SLEEP_GET_PRICE)

        current_time = time.time()
        current_price = api.get_current_price(api.symbol)
        if current_price is None:
            time.sleep(TIME_SLEEP_GET_PRICE)
            continue

        price_window.process_price(current_price)
           
        action, proposed_price, price_change_percent, slope = price_window.evaluate_buy_sell_opportunity(
            current_price, threshold_percent=0.8, decrease_percent=7
        )

        expired_trend = 'HOLD'
        if action == 'BUY':
            if trend_state1.is_trend_up():
                trend_state1.confirm_trend()
            else:
                expired_trend = trend_state1.start_trend('UP')
        if action == 'SELL':
            if trend_state1.is_trend_down():
                trend_state1.confirm_trend()  
            else:
                expired_trend = trend_state1.start_trend('DOWN')
        if action == "HOLD":
           if trend_state1.is_hold():
              trend_state1.confirm_trend()
        
        if trend_state1.is_trend_up() > 3:
            order_placed, order_id = track_and_place_order('BUY', proposed_price, current_price, slope=None, order_placed=order_placed, order_id=order_id)   
        if trend_state1.is_trend_down() > 3:
            order_placed, order_id = track_and_place_order('SELL', proposed_price, current_price, slope=None, order_placed=order_placed, order_id=order_id)   


        # Verificăm schimbările de preț și gestionăm trendurile
        price_change = price_window.check_price_change(PRICE_CHANGE_THRESHOLD_EUR)
        
        if price_change is not None and price_change > 0:
            # Confirmăm un trend de creștere
            print("DIFERENTA MARE UP!")
            if trend_state2.is_trend_up():
                trend_state2.confirm_trend()  # Confirmăm că trendul de creștere continuă
            else:
                expired_trend = trend_state2.start_trend('UP')  # Începem un trend nou de creștere
                order_placed, order_id = track_and_place_order('BUY', proposed_price, current_price, slope=None, order_placed=order_placed, order_id=order_id)
                # Dacă trendul anterior a fost DOWN, cumpărăm la începutul trendului de UP
                if expired_trend == 'DOWN':
                    proposed_price = proposed_price - 142
                    print(f"Start of UP trend. BUY order at {proposed_price:.2f} EUR")
                    order_placed, order_id = track_and_place_order('BUY', proposed_price, current_price, slope=None, order_placed=order_placed, order_id=order_id)
                    #last_order_time = current_time

        elif price_change is not None and price_change < 0:
            # Confirmăm un trend de scădere
            print("DIFERENTA MARE DOWN!")
            if trend_state2.is_trend_down():
                trend_state2.confirm_trend()  # Confirmăm că trendul de scădere continuă
            else:
                expired_trend = trend_state2.start_trend('DOWN')  # Începem un trend nou de scădere

                # Dacă trendul anterior a fost UP, vindem la începutul trendului de DOWN
                if expired_trend == 'UP':
                    proposed_price = proposed_price + 142
                    print(f"Start of DOWN trend. SELL order at {proposed_price:.2f} EUR")
                    order_placed, order_id = track_and_place_order('SELL', proposed_price, current_price, slope=None, order_placed=order_placed, order_id=order_id)
                    #last_order_time = current_time


   
            
        

    except BinanceAPIException as e:
        print(f"Binance API Error: {e}")
        time.sleep(TIME_SLEEP_GET_PRICE)
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(TIME_SLEEP_GET_PRICE)

