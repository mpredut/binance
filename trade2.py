import time
import datetime
import math
from binance.client import Client
from binance.exceptions import BinanceAPIException
from collections import deque


from apikeys import api_key, api_secret

#my imports
import binanceapi as api
import utils as u
from binanceapi import client, symbol, precision, get_quantity_precision, get_current_price, place_buy_order, place_sell_order, check_order_filled, cancel_order, get_open_sell_orders
from utils import beep, get_interval_time, are_difference_equal_with_aprox_proc, are_values_very_close, budget, order_cost_btc, price_change_threshold, max_threshold

class PriceWindow:
    def __init__(self, window_size, max_index=1000000, epsilon=1e-5):
        self.window_size = window_size
        self.prices = deque()  # Păstrează toate prețurile din fereastră
        self.min_deque = deque()  # Gestionarea minimului
        self.max_deque = deque()  # Gestionarea maximului
        self.current_index = 0  # Contor intern pentru a urmări indexul
        self.max_index = max_index  # Pragul la care se face normalizarea
        self.epsilon = epsilon  # Toleranță pentru minimurile aproximativ egale

    def process_price(self, price):
        # Adăugăm noul preț la lista de prețuri
        self.prices.append(price)
        print(f"Preț adăugat la fereastră: {price}")

        # Eliminăm prețurile care ies din fereastră
        if len(self.prices) > self.window_size:
            removed_price = self.prices.popleft()
            print(f"Preț eliminat din fereastră: {removed_price}")

        # Gestionarea minimului și maximului curent
        self._manage_minimum(price)
        self._manage_maximum(price)

        # Incrementăm indexul intern
        self.current_index += 1
        print(f"Index curent în fereastră: {self.current_index}")

    def _manage_minimum(self, price):
        """Gestionarea minimului curent din fereastră."""
        # Eliminăm elementele care sunt în afara ferestrei (prea vechi)
        if self.min_deque and self.min_deque[0][0] <= self.current_index - self.window_size:
            removed_min = self.min_deque.popleft()
            print(f"Minim eliminat: {removed_min}")

        # Verificăm dacă prețul curent este aproximativ egal cu oricare preț existent în `min_deque`
        for index, existing_price in self.min_deque:
            if abs(existing_price - price) <= self.epsilon:
                print(f"Prețul {price} este aproape egal cu un minim existent: {existing_price}")
                return  # Nu adăugăm prețul curent dacă există deja un echivalent
        
        # Eliminăm elementele din spate mai mari decât prețul curent
        while self.min_deque and self.min_deque[-1][1] > price:
            removed_min = self.min_deque.pop()
            print(f"Minim eliminat din spate: {removed_min}")

        # Adăugăm prețul curent
        self.min_deque.append((self.current_index, price))
        print(f"Minim adăugat: {price}")

    def _manage_maximum(self, price):
        """Gestionarea maximului curent din fereastră."""
        # Eliminăm elementele care sunt în afara ferestrei (prea vechi)
        if self.max_deque and self.max_deque[0][0] <= self.current_index - self.window_size:
            removed_max = self.max_deque.popleft()
            print(f"Maxim eliminat: {removed_max}")

        # Eliminăm elementele din spate mai mici sau egale decât prețul curent
        while self.max_deque and self.max_deque[-1][1] <= price:
            removed_max = self.max_deque.pop()
            print(f"Maxim eliminat din spate: {removed_max}")

        # Adăugăm prețul curent ca nou maxim
        self.max_deque.append((self.current_index, price))
        print(f"Maxim adăugat: {price}")

    def get_min(self):
        """Returnează minimul curent din fereastră."""
        if not self.min_deque:
            return None
        min_price = self.min_deque[0][1]
        print(f"Minimul curent din fereastră: {min_price}")
        return min_price

    def get_max(self):
        """Returnează maximul curent din fereastră."""
        if not self.max_deque:
            return None
        max_price = self.max_deque[0][1]
        print(f"Maximul curent din fereastră: {max_price}")
        return max_price

    def calculate_slope(self):
        """Calculează panta dintre minim și maxim în fereastră."""
        min_price = self.get_min()
        max_price = self.get_max()
        if min_price is None or max_price is None:
            return None
        slope = (max_price - min_price) / self.window_size
        print(f"Panta calculată: {slope}")
        return slope

def track_price_and_place_order(window_size=184, threshold_percent=2, decrease_percent=5, quantity=0.001):
    price_window = PriceWindow(window_size)
    order_placed = False
    order_id = None

    while True:
        current_price = get_current_price()
        if current_price is None:
            time.sleep(15)
            continue

        print(f"Preț curent obținut: {current_price}")
        price_window.process_price(current_price)
        min_price = price_window.get_min()
        max_price = price_window.get_max()

        if min_price is not None and max_price is not None:
            # Calculăm procentul de schimbare
            price_change_percent = (max_price - min_price) / min_price * 100
            print(f"Procentul de schimbare între minim și maxim: {price_change_percent:.2f}%")
            
            if price_change_percent > threshold_percent:
                buy_price = current_price * (1 - decrease_percent / 100)

                if not order_placed:
                    # Plasează ordinul de cumpărare
                    print(f"Plasarea ordinului de cumpărare la prețul: {buy_price:.2f} USDT")
                    order = place_buy_order(buy_price, quantity)
                    if order:
                        order_placed = True
                        order_id = order['orderId']
                else:
                    # Verificăm panta și anulăm ordinul dacă panta este pozitivă (prețul continuă să crească)
                    slope = price_window.calculate_slope()
                    if slope is not None and slope > 0:
                        print("Prețul continuă să crească, anulăm ordinul și plasăm unul nou.")
                        if cancel_order(order_id):
                            order = place_buy_order(buy_price, quantity)
                            if order:
                                order_id = order['orderId']

        # Așteptăm 15 secunde înainte de următoarea verificare
        time.sleep(15)

# Începem monitorizarea și plasarea ordinului dacă condițiile sunt îndeplinite
track_price_and_place_order()










