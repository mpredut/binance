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
import log
import alert

class PriceWindow:
    def __init__(self, window_size, max_index=1000000, epsilon=1e-2):  # Modificat epsilon la 1e-2
        self.window_size = window_size
        self.prices = deque()  # Păstrează toate prețurile din fereastră
        self.min_deque = deque()  # Gestionarea minimului
        self.max_deque = deque()  # Gestionarea maximului
        self.current_index = 0  # Contor intern pentru a urmări indexul
        self.max_index = max_index  # Pragul la care se face normalizarea
        self.epsilon = 10#epsilon  # Toleranță pentru minimurile aproximativ egale

    def process_price(self, price):
        self.prices.append(price)
   
        # Eliminăm prețurile care ies din fereastră
        if len(self.prices) > self.window_size:
            removed_price = self.prices.popleft()
            print(f"Preț eliminat din fereastră: {removed_price}")

        self._manage_minimum(price)
        self._manage_maximum(price)

        # Incrementăm indexul intern
        self.current_index += 1
        print(f"Index curent în fereastră: {self.current_index}")

    def _manage_minimum(self, price):
        if self.min_deque and self.min_deque[0][0] <= self.current_index - self.window_size:
            removed_min = self.min_deque.popleft()
            print(f"Minim eliminat deoarece este in afara ferestrei: {removed_min}")

        # Verificăm dacă prețul curent este aproximativ egal cu oricare preț existent în `min_deque`
        for index, existing_price in self.min_deque:
            if abs(existing_price - price) <= self.epsilon:  #are_values_very_close
                print(f"Prețul {price} este aproape egal cu un minim existent: {existing_price}")
                return  # Nu adăugăm prețul curent dacă există deja un echivalent
        
        # Eliminăm elementele din spate mai mari decât prețul curent
        while self.min_deque and self.min_deque[-1][1] > price:
            removed_min = self.min_deque.pop()
            print(f"Minim-uri eliminate din spate: {removed_min}")

        # Adăugăm prețul curent ca un nou potential minim
        self.min_deque.append((self.current_index, price))

    def _manage_maximum(self, price):
        if self.max_deque and self.max_deque[0][0] <= self.current_index - self.window_size:
            removed_max = self.max_deque.popleft()
            print(f"Maxim eliminat deoarece este in afara ferestrei: {removed_max}")

        # Eliminăm elementele din spate mai mici sau egale decât prețul curent
        while self.max_deque and self.max_deque[-1][1] <= price:
            removed_max = self.max_deque.pop()
            print(f"Maxim-uri eliminate din spate: {removed_max}")

        # Adăugăm prețul curent ca nou potential maxim
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
        
        # Extragem indicii pentru minim și maxim
        min_index = self.min_deque[0][0]
        max_index = self.max_deque[0][0]

        # Asigurăm că nu împărțim la zero
        if max_index == min_index:
            return 0
        
        slope = (max_price - min_price) / (max_index - min_index)
        print(f"Panta calculată: {slope}")
        
        return slope


def track_and_place_order(price_window, current_price, threshold_percent=2, decrease_percent=4, quantity=0.001, order_placed=False, order_id=None):
    min_price = price_window.get_min()
    max_price = price_window.get_max()
    min_price_index = price_window.get_min_and_index()
    max_price_index = price_window.get_max_and_index()
    print(f"Minimul curent din fereastră: {min_price} la index {min_price_index}")
    print(f"Maximul curent din fereastră: {max_price} la index {max_price_index}")

    slope = price_window.calculate_slope()
    if slope is None:
        print("Slope este null !!!")
     
    if slope is not None and slope > 0:
        print("Prețul continuă să crească")
    else:
        print("Prețul continuă să scada")
                    
    if min_price is not None and max_price is not None:
        # Calculăm procentul de schimbare
        price_change_percent = (max_price - min_price) / min_price * 100
        print(f"Procentul de schimbare între minim și maxim: {price_change_percent:.2f}%")
        
        if price_change_percent > threshold_percent:
            alert.check_alert(True, f"price_change {price_change_percent:.2f}")
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
                            order_placed = True
                        else:
                            order_placed = False
                    else:
                        order_placed = False

    return order_placed, order_id  # Returnăm starea actualizată a ordinului


window_size = 220  # window_size = 46 minute * 60 / 15 secunde sleep = 184
price_window = PriceWindow(window_size)
 
order_placed = False
order_id = None

while True:
    try:
        current_price = get_current_price()
        if current_price is None:
            time.sleep(4)
            continue
        print(f"BTC: {current_price}")
        
        price_window.process_price(current_price)
        
        order_placed, order_id = track_and_place_order(price_window, current_price, order_placed=order_placed, order_id=order_id)
        
        # Așteptăm x secunde înainte de următoarea verificare
        time.sleep(4)

    except BinanceAPIException as e:
        print(f"Eroare API Binance: {e}")
        time.sleep(1)  # Așteaptă 1 secundă înainte de a reporni încercările
    except Exception as e:
        print(f"Eroare: {e}")
        time.sleep(1)  # Așteaptă 1 secundă înainte de a reporni încercările
