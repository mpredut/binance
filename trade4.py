
import time
import datetime
import math
from binance.client import Client
from binance.exceptions import BinanceAPIException
from collections import deque

from apikeys import api_key, api_secret

# my imports

import binanceapi as api
import log
import alert
import utils as u
#import priceprediction as pp

import os

# Intervalul de timp între încercările de anulare și recreere a ordinului (în secunde)
CHECK_INTERVAL = 10

DEFAULT_ADJUSTMENT_PERCENT = u.calculate_difference_percent(60000, 60000 - 310)/100

def repetitive_buy(current_price, symbol, quantity, filled_sell_price):
    
    adjustment_percent = DEFAULT_ADJUSTMENT_PERCENT
    
    while True:
        target_buy_price = current_price * (1 - adjustment_percent)
        print(f"Ordin de BUY la prețul: {target_buy_price}")
        buy_order = api.place_BUY_order(symbol, target_buy_price, quantity)
        if buy_order is None:
            print("Eroare la plasarea ordinului de cumpărare. Încerc din nou.")
            time.sleep(CHECK_INTERVAL)
            continue

        time.sleep(CHECK_INTERVAL)
        order_id = buy_order['orderId']
        filled_buy_price = float(buy_order['price'])
        
        if api.check_order_filled(order_id):
            print(f"Ordin de cumpărare executat la prețul: {filled_buy_price}")
            return filled_buy_price  # Returnează prețul de cumpărare executat

        current_price = api.get_current_price(symbol)
        # Dacă prețul curent a crescut peste prețul țintă, cumpără la prețul curent
        if current_price > filled_sell_price:
            adjustment_percent = 0
        else:
            adjustment_percent = DEFAULT_ADJUSTMENT_PERCENT

        current_price = api.get_current_price(symbol)
        if not api.cancel_order(symbol, order_id):
            return filled_buy_price
        


def repetitive_sell(current_price, symbol, quantity, filled_buy_price):
    
    adjustment_percent = DEFAULT_ADJUSTMENT_PERCENT
    
    while True:
        target_sell_price = current_price * (1 + adjustment_percent)
        print(f"Ordin de SELL la prețul: {target_sell_price}")
        sell_order = api.place_SELL_order(symbol, target_sell_price, quantity)
        if sell_order is None:
            print("Eroare la plasarea ordinului de vânzare. Încerc din nou.")
            time.sleep(CHECK_INTERVAL)
            continue

        time.sleep(CHECK_INTERVAL)
        order_id = sell_order['orderId']
        filled_sell_price = float(sell_order['price'])
       
        if api.check_order_filled(order_id):
            print(f"Ordin de vânzare executat la prețul: {filled_sell_price}")
            return filled_sell_price  # Returnează prețul de vânzare executat

        # Dacă prețul curent scade sub prețul de cumpărare, vinde imediat la prețul curent
        current_price = api.get_current_price(symbol)
        if current_price < filled_buy_price:
            adjustment_percent = 0
        else:
            adjustment_percent = DEFAULT_ADJUSTMENT_PERCENT

        current_price = api.get_current_price(symbol)
        if not api.cancel_order(symbol, order_id):
            return filled_sell_price
        


print(f"DEFAULT_ADJUSTMENT_PERCENT = {DEFAULT_ADJUSTMENT_PERCENT}")
symbol = api.symbol
filled_sell_price = api.get_current_price(symbol)
filled_buy_price = filled_sell_price * (1 - 0.1)
filled_sell_price = filled_sell_price * (1 + 0.1)

while True:
    try:
        current_price = api.get_current_price(symbol)
        filled_sell_price = repetitive_sell(current_price, "BTCUSDT", 0.017, filled_buy_price)
        current_price = api.get_current_price(symbol)
        filled_buy_price = repetitive_buy(current_price, "BTCUSDT", 0.017, filled_sell_price)

        print(f"Tranzacția completă: Cumpărat la {filled_buy_price}, vândut la {filled_sell_price}")
        if(filled_buy_price < filled_sell_price):
            print(f"Profit {filled_sell_price / filled_buy_price}")
        else:
            print(f"Deficit {filled_sell_price / filled_buy_price}")
        time.sleep(1)
    except Exception as e:
        print(f"Eroare neprevăzută: {e}")
        time.sleep(1) 
    
