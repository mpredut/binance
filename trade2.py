import time
import datetime
import math
from binance.client import Client
from binance.exceptions import BinanceAPIException
from collections import deque

from apikeys import api_key, api_secret

# my imports
import binanceapi as api
import utils as u
from binanceapi import client, symbol, precision, get_quantity_precision, get_current_price, check_order_filled, place_order, cancel_order, cancel_expired_orders
from utils import beep, get_interval_time, are_difference_equal_with_aprox_proc, are_values_very_close, budget, order_cost_btc, price_change_threshold, max_threshold
import log
import alert
import utils


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
        print(f"BTC: {round(current_price, 0):.0f} Current index in window: {self.current_index}")
       
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

        slope = (max_price - min_price) / (max_index - min_index)
        return slope

    def calculate_proximities(self, current_price):
        min_price, _ = self.get_min_and_index()
        max_price, _ = self.get_max_and_index()
        if max_price != min_price:
            min_proximity = (current_price - min_price) / (max_price - min_price)
            max_proximity = (max_price - current_price) / (max_price - min_price)
        else:
            min_proximity = max_proximity = 0
        return min_proximity, max_proximity

    def calculate_positions(self):
        min_index = self.min_deque[0][1] if self.min_deque else 0
        max_index = self.max_deque[0][1] if self.max_deque else 0
        return min_index / self.window_size, max_index / self.window_size

        
    def evaluate_buy_sell_opportunity(self, current_price, threshold_percent=1, decrease_percent=3.7):
        slope = self.calculate_slope()
     
        min_price, min_index = self.get_min_and_index()
        max_price, max_index = self.get_max_and_index()
        
        print(
            f"Min price: {min_price} at index: {min_index} "
            f"Max price: {max_price} at index: {max_index}"
        )

        min_position, max_position = self.calculate_positions()
        min_proximity, max_proximity = self.calculate_proximities(current_price)
 
        print(
            f"Min position: {min_position:.2f}, Max position: {max_position:.2f} "
            f"Min proximity: {min_proximity:.2f}, Max proximity: {max_proximity:.2f}"
        )
        
        price_change_percent = (max_price - min_price) / min_price * 100 if min_price and max_price else 0
        print(
            f"Price change percent: {price_change_percent:.2f} "
            f"slope: {slope:.2f} "
            f"Market trending: {'upwards' if slope > 0 else 'downwards'}"
        )

        if price_change_percent < threshold_percent and not utils.are_values_very_close(price_change_percent, threshold_percent):
            action = 'HOLD'
            print(f"Action: {action}")
            return action, current_price * 0.8, price_change_percent, slope
            
        alert.check_alert(True, f"Price changed {price_change_percent:.2f}%. Current price {current_price}")
        action = 'BUY'
        remaining_decrease_percent = max(0, decrease_percent - price_change_percent)
        print(f"Remaining decrease percent: {remaining_decrease_percent:.2f}")
        proposed_price = current_price * (1 - remaining_decrease_percent / 100)
        print(f"Proposed price: {proposed_price:.2f} Action: {action}")
        
        if slope > 0:
            print("Market trending upwards")
            if min_proximity < 0.2 or utils.are_values_very_close(min_proximity, 0.2, target_tolerance_percent=1.0):
                if min_position > 0.8 or utils.are_values_very_close(min_position, 0.8, target_tolerance_percent=1.0):
                    action = 'BUY'
                    print(f"Near recent low. Action: {action}")
                    proposed_price = current_price * 0.995
                    print(f"Proposed price updated  to {proposed_price} to be close to current price {current_price}")
        else:
            print("Market trending downwards")
            if max_proximity < 0.2 or utils.are_values_very_close(max_proximity, 0.2, target_tolerance_percent=1.0):
                if max_position > 0.8 or utils.are_values_very_close(max_position, 0.8, target_tolerance_percent=1.0):
                    action = 'SELL'
                    print(f"Near recent high. Action: {action}")
                    proposed_price = current_price * 1.005
                    print(f"Proposed price updated  to {proposed_price} to be close to current price {current_price}")

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

SELL_BUY_THRESHOLD = 5  # Threshold for the number of consecutive signals


def track_and_place_order(action, proposed_price, current_price, slope, quantity=0.0017, order_placed=False, order_id=None):
    
    if action == 'HOLD':
        return order_placed, order_id
        
    # Determine the number of orders and their spacing based on price trend
    if slope is not None and slope > 0:
        # Price is rising, place fewer, larger orders
        num_orders = 2
        price_step = 0.2  # Increase the spacing between orders as procents
        print(f"Placing fewer, {num_orders} larger orders due to rising price.")
    else:
        # Price is falling, place more, smaller orders
        num_orders = 2
        price_step = 0.08  # Reduce the spacing between orders as procents
        print(f"Placing more, {num_orders} smaller orders due to falling price.")

    if action == 'BUY':
        cancel_expired_orders("buy", symbol, EXP_TIME_BUY_ORDER)

        buy_price = min(proposed_price, current_price * 0.998)
        print(f"BUY price: {buy_price:.2f} USDT")

        alert.check_alert(True, f"BUY order {buy_price:.2f}")
       
        # Place the custom buy orders
        for i in range(num_orders):
            adjusted_buy_price = buy_price * (1 - i * price_step / 100)
            order_quantity = quantity / num_orders  # Divide quantity among orders
            print(f"Placing buy order at price: {adjusted_buy_price:.2f} USDT for {order_quantity:.6f} BTC")
            order = place_order("buy", symbol, adjusted_buy_price, order_quantity)
            if order:
                print(f"Buy order placed successfully with ID: {order['orderId']}")
                order_placed = True
                order_id = order['orderId']

    elif action == 'SELL':
        cancel_expired_orders("sell", symbol, EXP_TIME_SELL_ORDER)

        sell_price = max(proposed_price, current_price * 1.002)
        print(f"SELL price: {sell_price:.2f} USDT")
        
        alert.check_alert(True, f"SELL order {sell_price:.2f}")

        # Place the custom sell orders
        for i in range(num_orders):
            adjusted_sell_price = sell_price * (1 + i * price_step / 100)
            order_quantity = quantity / num_orders  # Divide quantity among orders
            print(f"Placing sell order at price: {adjusted_sell_price:.2f} USDT for {order_quantity:.6f} BTC")
            order = place_order("sell", symbol, adjusted_sell_price, order_quantity)
            if order:
                print(f"Sell order placed successfully with ID: {order['orderId']}")
                order_placed = True
                order_id = order['orderId']

    return order_placed, order_id  # Return the updated order state


import time

class TrendState:
    def __init__(self, max_duration_seconds, expiration_threshold):
        self.state = 'HOLD'  # Inițial, starea este 'HOLD'
        self.start_time = None  # Timpul de început al trendului
        self.end_time = None  # Timpul de sfârșit al trendului
        self.last_confirmation_time = None  # Ultimul timp de confirmare al trendului
        self.max_duration_seconds = max_duration_seconds  # Durata maximă permisă pentru un trend
        self.confirm_count = 0  # Contorul de confirmări pentru trend
        self.expiration_threshold = expiration_threshold  # Pragul de timp între confirmări (în secunde)

    def start_trend(self, new_state):
        old_state = self.state
        if self.state != 'HOLD':  # Dacă schimbăm trendul, considerăm sfârșitul trendului anterior
            self.end_trend()  # Marchează sfârșitul trendului anterior
        
        self.state = new_state
        self.start_time = time.time()
        self.last_confirmation_time = self.start_time
        self.confirm_count = 1  # Prima confirmare
        self.end_time = None  # Resetăm timpul de sfârșit
        print(f"Trend started: {self.state} at {time.ctime(self.start_time)}")
        return old_state

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
                self.end_time = self.last_confirmation_time  # Sfârșitul trendului este la ultima confirmare
                return True
        return False

    def end_trend(self):
        """Marchează sfârșitul trendului curent și returnează starea trendului care s-a încheiat."""
        self.end_time = self.last_confirmation_time  # Timpul de sfârșit al trendului este ultimul timp de confirmare
        print(f"Trend ended: {self.state} at {time.ctime(self.end_time)} after {self.confirm_count} confirmations.")

    def is_trend_up(self):
        return self.state == 'UP'

    def is_trend_down(self):
        return self.state == 'DOWN'

    def is_hold(self):
        return self.state == 'HOLD'
        
trend_state = TrendState(max_duration_seconds= 2 * 60 * 60, expiration_threshold=10 * 60)  # Expiră în 10 minute


#
#       MAIN 
#

alert.check_alert(True, f"SELL order ")
  

price_window = PriceWindow(window_size)

order_placed = False
order_id = None
last_order_time = time.time()
last_evaluate_time = time.time()

# Counters for BUY and SELL evaluations
buy_count = 0
sell_count = 0

PRICE_CHANGE_THRESHOLD_EUR = 300

while True:
    try:
        current_price = get_current_price(symbol)
        if current_price is None:
            time.sleep(TIME_SLEEP_GET_PRICE)
            continue

        price_window.process_price(current_price)

        # Verificăm periodic dacă trendul curent a expirat
        if trend_state.check_trend_expiration():
            expired_trend = trend_state.state  # Reținem trendul care a expirat
            trend_state.end_trend()  # Marchează sfârșitul trendului

            # Aplicăm ordine la sfârșitul unui trend
            if expired_trend == 'UP':
                proposed_price = current_price + 142  # Preț de vânzare
                print(f"End of UP trend. SELL order at {proposed_price:.2f} EUR")
                order_placed, order_id = track_and_place_order('SELL', proposed_price, current_price, slope=None, order_placed=order_placed, order_id=order_id)
            elif expired_trend == 'DOWN':
                proposed_price = current_price - 142  # Preț de cumpărare
                print(f"End of DOWN trend. BUY order at {proposed_price:.2f} EUR")
                order_placed, order_id = track_and_place_order('BUY', proposed_price, current_price, slope=None, order_placed=order_placed, order_id=order_id)

        # Verificăm schimbările de preț și gestionăm trendurile
        price_change = price_window.check_price_change(PRICE_CHANGE_THRESHOLD_EUR)
        
        if price_change is not None and price_change > 0:
            # Confirmăm un trend de creștere
            if trend_state.is_trend_up():
                trend_state.confirm_trend()  # Confirmăm că trendul de creștere continuă
            else:
                expired_trend = trend_state.start_trend('UP')  # Începem un trend nou de creștere

                # Dacă trendul anterior a fost DOWN, cumpărăm la începutul trendului de UP
                if expired_trend == 'DOWN':
                    proposed_price = current_price - 142
                    print(f"Start of UP trend. BUY order at {proposed_price:.2f} EUR")
                    order_placed, order_id = track_and_place_order('BUY', proposed_price, current_price, slope=None, order_placed=order_placed, order_id=order_id)

        elif price_change is not None and price_change < 0:
            # Confirmăm un trend de scădere
            if trend_state.is_trend_down():
                trend_state.confirm_trend()  # Confirmăm că trendul de scădere continuă
            else:
                expired_trend = trend_state.start_trend('DOWN')  # Începem un trend nou de scădere

                # Dacă trendul anterior a fost UP, vindem la începutul trendului de DOWN
                if expired_trend == 'UP':
                    proposed_price = current_price + 142
                    print(f"Start of DOWN trend. SELL order at {proposed_price:.2f} EUR")
                    order_placed, order_id = track_and_place_order('SELL', proposed_price, current_price, slope=None, order_placed=order_placed, order_id=order_id)


        # Confirmarea trendului folosind `evaluate_buy_sell_opportunity`
        action, proposed_price, price_change_percent, slope = price_window.evaluate_buy_sell_opportunity(
            current_price, threshold_percent=0.8, decrease_percent=4
        )

        if action == 'BUY':
            if trend_state.is_trend_up():
                trend_state.confirm_trend()  # Confirmăm trendul de creștere
               
        elif action == 'SELL':
            if trend_state.is_trend_down():
                trend_state.confirm_trend()  # Confirmăm trendul de scădere
 
        # Resetează fereastra de prețuri după acțiune
        #price_window = PriceWindow(window_size)           


        #########
        current_time = time.time()

        # Evaluate buy/sell opportunity more frequently
        if current_time - last_evaluate_time >= TIME_SLEEP_EVALUATE:
        
            cancel_expired_orders("buy", symbol, EXP_TIME_BUY_ORDER)
            cancel_expired_orders("sell", symbol, EXP_TIME_SELL_ORDER)
            
            action, proposed_price, price_change_percent, slope = price_window.evaluate_buy_sell_opportunity(current_price, threshold_percent=0.8, decrease_percent=4)
            last_evaluate_time = current_time

            # Count consecutive BUY/SELL actions
            if action == 'BUY':
                buy_count += 1
                sell_count -= 1  # Reset sell count if a BUY signal is received
            elif action == 'SELL':
                sell_count += 1
                buy_count -= 1  # Reset buy count if a SELL signal is received
            else:
                buy_count -= 1
                sell_count -= 1
            
            if buy_count < 0:
                buy_count = 0
            if sell_count < 0:
                sell_count = 0
            
            
        diff_count = abs(buy_count - sell_count)
        # Place orders based on the threshold
        if current_time - last_order_time >= TIME_SLEEP_PLACE_ORDER and \
        (utils.are_values_very_close(diff_count, SELL_BUY_THRESHOLD, 1) or diff_count >= SELL_BUY_THRESHOLD)  :
            if abs(last_evaluate_time - time.time()) > 2:
                action, proposed_price, price_change_percent, slope = price_window.evaluate_buy_sell_opportunity(current_price, threshold_percent=0.8, decrease_percent=4)
            if action == 'HOLD':
                continue
            last_evaluate_time = time.time()
            if buy_count >= sell_count :
                #order_placed, order_id = track_and_place_order('BUY', proposed_price, current_price, slope, order_placed=order_placed, order_id=order_id)
                last_order_time = current_time
                #buy_count = 0  # Reset buy count after placing the order
            else:
                #order_placed, order_id = track_and_place_order('SELL', proposed_price, current_price, slope, order_placed=order_placed, order_id=order_id)
                last_order_time = current_time
                #sell_count = 0  # Reset sell count after placing the order

        time.sleep(TIME_SLEEP_GET_PRICE)

    except BinanceAPIException as e:
        print(f"Binance API Error: {e}")
        time.sleep(TIME_SLEEP_GET_PRICE)
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(TIME_SLEEP_GET_PRICE)

