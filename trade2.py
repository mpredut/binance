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
from binanceapi import client, symbol, precision, get_quantity_precision, get_current_price, place_buy_order, place_sell_order, check_order_filled, cancel_order, get_open_sell_orders
from utils import beep, get_interval_time, are_difference_equal_with_aprox_proc, are_values_very_close, budget, order_cost_btc, price_change_threshold, max_threshold
import log
import alert


class PriceWindow:
    def __init__(self, window_size, max_index=1000000, epsilon=1e-2):
        self.window_size = window_size
        self.prices = deque()  # Store all prices in the window
        self.min_deque = deque()  # Manage the minimums
        self.max_deque = deque()  # Manage the maximums
        self.current_index = 0  # Internal counter to keep track of index
        self.max_index = max_index  # Threshold for normalization
        self.epsilon = 10  # Tolerance for approximately equal minimums

    def process_price(self, price):
        self.prices.append(price)

        # Remove prices that fall out of the window
        if len(self.prices) > self.window_size:
            removed_price = self.prices.popleft()
            print(f"Price removed from window: {removed_price}")

        self._manage_minimum(price)
        self._manage_maximum(price)

        # Increment internal index
        self.current_index += 1
        print(f"Current index in window: {self.current_index}")
        
        # Normalize indices if they exceed max_index
        if self.current_index > self.max_index:
            print(f"Start normalize indexes")
            self._normalize_indices()

    def _normalize_indices(self):
        """Normalize indices in the deques to prevent overflow."""
        old_index = self.current_index
        self.current_index = 0  # Reset current index
        
        # Adjust indices in min_deque and max_deque
        self.min_deque = deque([(i - old_index, price) for i, price in self.min_deque])
        self.max_deque = deque([(i - old_index, price) for i, price in self.max_deque])
        
        print(f"Indices normalized. Old index: {old_index}, New index: {self.current_index}")
        
    def _manage_minimum(self, price):
        if self.min_deque and self.min_deque[0][0] <= self.current_index - self.window_size:
            removed_min = self.min_deque.popleft()
            print(f"Minimum removed as it is outside the window: {removed_min}")

        for index, existing_price in self.min_deque:
            if abs(existing_price - price) <= self.epsilon: # are_values_very_close
                print(f"Price {price} is approximately equal to an existing minimum: {existing_price}")
                return  # Don't add the current price if an equivalent exists

        while self.min_deque and self.min_deque[-1][1] > price:
            removed_min = self.min_deque.pop()
            print(f"Minimums removed from back: {removed_min}")

        self.min_deque.append((self.current_index, price))

    def _manage_maximum(self, price):
        if self.max_deque and self.max_deque[0][0] <= self.current_index - self.window_size:
            removed_max = self.max_deque.popleft()
            print(f"Maximum removed as it is outside the window: {removed_max}")

        while self.max_deque and self.max_deque[-1][1] <= price:
            removed_max = self.max_deque.pop()
            print(f"Maximums removed from back: {removed_max}")

        self.max_deque.append((self.current_index, price))

    def get_min(self):
        if not self.min_deque:
            return None
        return self.min_deque[0][1]

    def get_max(self):
        if not self.max_deque:
            return None
        return self.max_deque[0][1]

    def get_min_and_index(self):
        if not self.min_deque:
            return None
        return self.min_deque[0]

    def get_max_and_index(self):
        if not self.max_deque:
            return None
        return self.max_deque[0]

    def calculate_slope(self):
        min_price = self.get_min()
        max_price = self.get_max()

        if min_price is None or max_price is None:
            return None

        min_index = self.min_deque[0][0]
        max_index = self.max_deque[0][0]

        if max_index == min_index:
            return 0

        slope = (max_price - min_price) / (max_index - min_index)
        print(f"Slope calculated: {slope}")

        return slope

    def current_window_size(self):
        return len(self.prices)


def track_and_place_order(price_window, current_price, threshold_percent=2, decrease_percent=4, quantity=0.0017, order_placed=False, order_id=None):
    min_price = price_window.get_min()
    max_price = price_window.get_max()
    min_price_index = price_window.get_min_and_index()
    max_price_index = price_window.get_max_and_index()
    print(f"Current minimum in window: {min_price} at index {min_price_index}")
    print(f"Current maximum in window: {max_price} at index {max_price_index}")

    slope = price_window.calculate_slope()
    if slope is None:
        print("Slope is null!")

    if slope is not None and slope > 0:
        print("Price continues to rise")
    else:
        print("Price continues to fall")

    if min_price is not None and max_price is not None:
        price_change_percent = (max_price - min_price) / min_price * 100
        print(f"Percentage change between minimum and maximum: {price_change_percent:.2f}%")

        if price_change_percent > threshold_percent:
            alert.check_alert(True, f"price_change {price_change_percent:.2f}")

            # Cancel existing sell orders
            open_sell_orders = get_open_sell_orders(symbol)
            for order_id in open_sell_orders.keys():
                cancel_order(order_id)

            # Calculate the base buy price
            buy_price = current_price * (1 - decrease_percent / 100)
            buy_price = min(buy_price, current_price * 0.998)
            # Decide on the number of orders and their spacing based on price trend
            num_orders = 5  # Default number of orders
            price_step = 0.5  # Default step percentage between orders

            if slope is not None and slope > 0:
                # Price is rising, place fewer, larger orders
                num_orders = 3
                price_step = 1.0  # Increase the spacing between orders
                print("Placing fewer, larger buy orders due to rising price.")
            else:
                # Price is falling, place more, smaller orders
                num_orders = 7
                price_step = 0.5  # Reduce the spacing between orders
                print("Placing more, smaller buy orders due to falling price.")

            # Place the custom buy orders
            for i in range(num_orders):
                adjusted_buy_price = buy_price * (1 - i * price_step / 100)
                order_quantity = quantity / num_orders  # Divide quantity among orders
                print(f"Placing buy order at price: {adjusted_buy_price:.2f} USDT for {order_quantity:.6f} BTC")
                order = place_buy_order(adjusted_buy_price, order_quantity)
                if order:
                    print(f"Order placed successfully with ID: {order['orderId']}")

    return order_placed, order_id  # Return the updated order state



TIME_SLEEP_PRICE = 4  # seconds to sleep for price collection
TIME_SLEEP_ORDER = 30  # seconds to sleep for order placement
WINDOWS_SIZE_MIN = 48  # minutes
window_size = WINDOWS_SIZE_MIN * 60 / TIME_SLEEP_PRICE

price_window = PriceWindow(window_size)

order_placed = False
order_id = None
last_order_time = time.time()

while True:
    try:
        current_price = get_current_price()
        if current_price is None:
            time.sleep(TIME_SLEEP_PRICE)
            continue
        print(f"BTC: {current_price}")

        price_window.process_price(current_price)

        if time.time() - last_order_time >= TIME_SLEEP_ORDER:
            order_placed, order_id = track_and_place_order(price_window, current_price, threshold_percent=1.5, order_placed=order_placed, order_id=order_id)
            last_order_time = time.time()

        time.sleep(TIME_SLEEP_PRICE)

    except BinanceAPIException as e:
        print(f"Binance API Error: {e}")
        time.sleep(TIME_SLEEP_PRICE)
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(TIME_SLEEP_PRICE)
