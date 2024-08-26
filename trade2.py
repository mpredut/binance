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
from binanceapi import client, symbol, precision, get_quantity_precision, get_current_price, check_order_filled, cancel_order, get_open_sell_orders
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
        print(f"Current index in window: {self.current_index}")

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
            return None

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
        print(f"Slope calculated: {slope:.2f}")

        min_price, min_index = self.get_min_and_index()
        print(f"Min price: {min_price} at index: {min_index}")

        max_price, max_index = self.get_max_and_index()
        print(f"Max price: {max_price} at index: {max_index}")

        min_position, max_position = self.calculate_positions()
        print(f"Min position: {min_position}, Max position: {max_position}")

        min_proximity, max_proximity = self.calculate_proximities(current_price)
        print(f"Min proximity: {min_proximity}, Max proximity: {max_proximity}")

        price_change_percent = (max_price - min_price) / min_price * 100 if min_price and max_price else 0
        print(f"Price change percent: {price_change_percent:.2f}")

        if price_change_percent < threshold_percent and not utils.are_values_very_close(price_change_percent, threshold_percent):
            action = 'HOLD'
            print(f"Action: {action}")
            return action, current_price, price_change_percent, slope
            
        alert.check_alert(True, f"price_change {price_change_percent:.2f}")
        action = 'BUY'
        remaining_decrease_percent = max(0, decrease_percent - price_change_percent)
        print(f"Remaining decrease percent: {remaining_decrease_percent}")
        proposed_price = current_price * (1 - remaining_decrease_percent / 100)
        print(f"Proposed price: {proposed_price}")
        

        if slope is not None and slope > 0:
            print("Market trending upwards")
            if min_proximity < 0.2 or utils.are_values_very_close(min_proximity, 0.2, target_tolerance_percent=1.0):
                if min_position > 0.8 or utils.are_values_very_close(min_position, 0.8, target_tolerance_percent=1.0):
                    action = 'BUY'
                    print(f"Near recent low. Action: {action}")
                    proposed_price = current_price * 0.995
                    print(f"Proposed price updated  to {proposed_price} to be close to current price {current_price}")
                else:
                    #action = 'HOLD'
                    print(f"Not near recent low. Action: {action}")
            else:
                #action = 'HOLD'
                print(f"Not near recent low. Action: {action}")
        else:
            print("Market trending downwards")
            if max_proximity < 0.2 or utils.are_values_very_close(max_proximity, 0.2, target_tolerance_percent=1.0):
                if max_position > 0.8 or utils.are_values_very_close(max_position, 0.8, target_tolerance_percent=1.0):
                    action = 'SELL'
                    print(f"Near recent high. Action: {action}")
                    proposed_price = current_price * 1.005
                    print(f"Proposed price updated  to {proposed_price} to be close to current price {current_price}")
                else:
                    #action = 'HOLD'
                    print(f"Not near recent high. Action: {action}")
            else:
                #action = 'HOLD'
                print(f"Not near recent high. Action: {action}")


        return action, proposed_price, price_change_percent, slope

        
    def current_window_size(self):
        return len(self.prices)


def track_and_place_order(action, proposed_price, current_price, slope, quantity=0.0017*3, order_placed=False, order_id=None):
    
    if action == 'HOLD':
        return order_placed, order_id
        
    # Determine the number of orders and their spacing based on price trend
    if slope is not None and slope > 0:
        # Price is rising, place fewer, larger orders
        num_orders = 3
        price_step = 1.0  # Increase the spacing between orders
        print(f"Placing fewer, larger orders due to rising price.")
    else:
        # Price is falling, place more, smaller orders
        num_orders = 7
        price_step = 0.5  # Reduce the spacing between orders
        print(f"Placing more, smaller orders due to falling price.")

    if action == 'BUY':
        # Cancel existing buy orders
        open_buy_orders = get_open_buy_orders(symbol)
        for order_id in open_buy_orders.keys():
            cancel_order(order_id)
            print(f"Cancelled buy order with ID: {order_id}")

        # Adjust the buy price based on market conditions
        buy_price = min(proposed_price, current_price * 0.998)
        print(f"Adjusted buy price: {buy_price:.2f} USDT")

        alert.check_alert(True, f"BUY order {buy_price:.2f}")
       
        # Place the custom buy orders
        for i in range(num_orders):
            adjusted_buy_price = buy_price * (1 - i * price_step / 100)
            order_quantity = quantity / num_orders  # Divide quantity among orders
            print(f"Placing buy order at price: {adjusted_buy_price:.2f} USDT for {order_quantity:.6f} BTC")
            order = place_order("buy", adjusted_buy_price, order_quantity)
            if order:
                print(f"Buy order placed successfully with ID: {order['orderId']}")
                order_placed = True
                order_id = order['orderId']

    elif action == 'SELL':
        # Cancel existing sell orders
        open_sell_orders = get_open_sell_orders(symbol)
        for order_id in open_sell_orders.keys():
            cancel_order(order_id)
            print(f"Cancelled sell order with ID: {order_id}")

        # Adjust the sell price based on market conditions
        sell_price = max(proposed_price, current_price * 1.002)
        print(f"Adjusted sell price: {sell_price:.2f} USDT")
        
        alert.check_alert(True, f"SELL order {sell_price:.2f}")

        # Place the custom sell orders
        for i in range(num_orders):
            adjusted_sell_price = sell_price * (1 + i * price_step / 100)
            order_quantity = quantity / num_orders  # Divide quantity among orders
            print(f"Placing sell order at price: {adjusted_sell_price:.2f} USDT for {order_quantity:.6f} BTC")
            order = place_order("sell", adjusted_sell_price, order_quantity)
            if order:
                print(f"Sell order placed successfully with ID: {order['orderId']}")
                order_placed = True
                order_id = order['orderId']

    return order_placed, order_id  # Return the updated order state

TIME_SLEEP_GET_PRICE = 4  # seconds to sleep for price collection
TIME_SLEEP_ORDER = 4*79  # seconds to sleep for order placement
TIME_SLEEP_EVALUATE = 60  # seconds to sleep for buy/sell evaluation
WINDOWS_SIZE_MIN = 48  # minutes
window_size = WINDOWS_SIZE_MIN * 60 / TIME_SLEEP_GET_PRICE

SELL_BUY_THRESHOLD = 5  # Threshold for the number of consecutive signals

price_window = PriceWindow(window_size)

order_placed = False
order_id = None
last_order_time = time.time()
last_evaluate_time = time.time()

# Counters for BUY and SELL evaluations
buy_count = 0
sell_count = 0

while True:
    try:
        current_price = get_current_price()
        if current_price is None:
            time.sleep(TIME_SLEEP_GET_PRICE)
            continue
        print(f"BTC: {current_price}")

        price_window.process_price(current_price)

        current_time = time.time()

        # Evaluate buy/sell opportunity more frequently
        if current_time - last_evaluate_time >= TIME_SLEEP_EVALUATE:
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
            
            

        # Place orders based on the threshold
        if current_time - last_order_time >= TIME_SLEEP_ORDER and utils.are_values_very_close(max(buy_count, sell_count), SELL_BUY_THRESHOLD, 1) :
            if buy_count >= sell_count :
                order_placed, order_id = track_and_place_order('BUY', proposed_price, current_price, slope, order_placed=order_placed, order_id=order_id)
                last_order_time = current_time
                #buy_count = 0  # Reset buy count after placing the order
            else:
                order_placed, order_id = track_and_place_order('SELL', proposed_price, current_price, slope, order_placed=order_placed, order_id=order_id)
                last_order_time = current_time
                #sell_count = 0  # Reset sell count after placing the order

        time.sleep(TIME_SLEEP_GET_PRICE)

    except BinanceAPIException as e:
        print(f"Binance API Error: {e}")
        time.sleep(TIME_SLEEP_GET_PRICE)
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(TIME_SLEEP_GET_PRICE)

