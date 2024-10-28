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
                print(f"Proposed price updated  to {proposed_price} to be close to current price {current_price}")
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
WINDOWS_SIZE_MIN = TIME_SLEEP_GET_PRICE + 7.7 * 60  # minutes
window_size = WINDOWS_SIZE_MIN / TIME_SLEEP_GET_PRICE

window_size2 = 2 * 60 * 60 / TIME_SLEEP_GET_PRICE
SELL_BUY_THRESHOLD = 5  # Threshold for the number of consecutive signals

def track_and_place_order(action, proposed_price, current_price, quantity=0.017/2, order_ids=None):
    # Initialize order_ids as an empty list if it is None
    if order_ids is None:
        order_ids = []

    if action == 'HOLD':
        return order_ids

    # Cancel any existing orders
    if order_ids:
        for order_id in order_ids:
            if not api.cancel_order(order_id):
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
            order = api.place_order_smart("buy", api.symbol, adjusted_buy_price, order_quantity)
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
            order = api.place_order_smart("sell", api.symbol, adjusted_sell_price, order_quantity)
            if order:
                print(f"Sell order placed successfully with ID: {order['orderId']}")
                order_ids.append(order['orderId']) 

    return order_ids



class TrendState:
    def __init__(self, max_duration_seconds, expiration_threshold):
        self.state = 'HOLD'  # Inițial, starea este 'HOLD'
        self.old_state = self.state 
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
        print(f"Start of {self.state} trend at {u.timeToHMS(self.start_time)}")
        return self.old_state

    def confirm_trend(self):
        self.last_confirmation_time = time.time()
        self.confirm_count += 1
        print(f"Trend confirmed: {self.state} at {u.timeToHMS(self.last_confirmation_time)}")
        return self.confirm_count

    def check_trend_expiration(self):
        """Verifica daca trendul a expirat din cauza lipsei confirmarilor In intervalul permis."""
        if self.last_confirmation_time:
            time_since_last_confirmation = time.time() - self.last_confirmation_time
            if time_since_last_confirmation > self.expiration_threshold:
                print(f"Trend expired: {self.state}. Time since last confirmation: {time_since_last_confirmation} seconds")
                self.end_trend()
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

order_ids = []
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
       
        price_change = price_window.check_price_change(PRICE_CHANGE_THRESHOLD_EUR)
        
        if price_change is not None and price_change > 0:
            # Confirmam un trend de crestere
            print("DIFERENTA MARE UP!")
            initial_difference = 247
            if trend_state2.is_trend_up():
                count = trend_state2.confirm_trend() # Confirmam ca trendul de crestere continua
                diff, _ = u.decrese_value_by_increment_exp(initial_difference, count)
                proposed_price = current_price - diff
                track_and_place_order('BUY', proposed_price, current_price, order_ids=order_ids)
            else:
                expired_trend = trend_state2.start_trend('UP')  # Incepem un trend nou de crestere
                proposed_price = current_price - initial_difference
                track_and_place_order('BUY', proposed_price, current_price, order_ids=order_ids)
               

        elif price_change is not None and price_change < 0:
            # Confirmam un trend de scadere
            print("DIFERENTA MARE DOWN!")
            initial_difference = 447
            if trend_state2.is_trend_down():
                count = trend_state2.confirm_trend() # Confirmam ca trendul de scadere continua
                diff, _ = u.decrese_value_by_increment_exp(initial_difference, count)
                proposed_price = proposed_price + diff
                track_and_place_order('SELL', proposed_price, current_price, order_ids=order_ids)
            else:
                expired_trend = trend_state2.start_trend('DOWN')  # Incepem un trend nou de scadere
                proposed_price = current_price + initial_difference
                track_and_place_order('SELL', proposed_price, current_price, order_ids=order_ids)

   
            
        

    except BinanceAPIException as e:
        print(f"Binance API Error: {e}")
        time.sleep(TIME_SLEEP_GET_PRICE)
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(TIME_SLEEP_GET_PRICE)

