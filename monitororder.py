import time
import datetime
import random

####Binance
from binance.client import Client
from binance.exceptions import BinanceAPIException

#my imports
import binanceapi as api
import utils
# 
MAX_PROC = 0.77
monitor_interval = 3.7
initial_prices = {}  # Dictionar pentru a retine preturile initiale ale ordinelor
initial_sell_prices = {}  # Dictionar pentru a retine preturile initiale ale ordinelor de vanzare
initial_buy_prices = {}  # Dictionar pentru a retine preturile initiale ale ordinelor de cumparare

import time


TIME_SLEEP_ERROR = 10



def monitor_open_orders_by_type(order_type):
    orders = api.get_open_orders(order_type, api.symbol)  # Obtine ordinele de vanzare sau cumparare în functie de tip
    if not orders:
        print(f"Nu exista ordine de {order_type} deschise initial.")
        return
    
    current_price = api.get_current_price(api.symbol)
    if current_price is None:
        print("Eroare la obtinerea pretului...")
        return
    
    print(f"Pretul curent BTC: {current_price:.2f}")
    
    initial_prices = initial_sell_prices if order_type == 'sell' else initial_buy_prices

    for order_id in list(orders.keys()):
        order = orders[order_id]
        price = order['price']
        
        if order_id not in initial_prices:
            initial_prices[order_id] = price
        
        difference_percent = abs(current_price - price) / price * 100
        print(f"{order_type.capitalize()} price {price}, current price {current_price} difference: {difference_percent:.2f}%, Order ID {order_id}")
        
        are_close = utils.are_values_very_close(current_price, price, MAX_PROC)
        if are_close:
            print(f"Current price {current_price} and {order_type} price {price} are close!")
            
            difference_percent = abs(current_price - initial_prices[order_id]) / initial_prices[order_id] * 100
            
            are_close = utils.are_values_very_close(current_price, initial_prices[order_id], MAX_PROC)
            if not are_close:
                print(f"Totusi pretul s-a modificat prea mult({difference_percent}%) fata de pretul initial ({initial_prices[order_id]}). Nu se mai modifica ordinul.")
                continue
            else:
                print(f"Current price {current_price} and initial {order_type} price {initial_prices[order_id]} are close!")
            
            if not api.cancel_order(order_id):
                initial_prices.pop(order_id)
                continue
            
            if order_type == 'sell':
                new_price = current_price * 1.001 + 20
            else:
                new_price = current_price * 0.999 - 20
            
            quantity = order['quantity']
            
            new_order = api.place_order(order_type,  api.symbol, new_price, quantity)
            
            if new_order:    
                orders[new_order['orderId']] = {
                    'price': new_price,
                    'quantity': quantity
                }
                initial_prices[new_order['orderId']] = initial_prices.pop(order_id)  # Pastram pretul initial
                print(f"Update order from {price} to {new_price}. New ID: {new_order['orderId']}")
            else:
                print(f"Eroare la plasarea noului ordin de {order_type}.")
    



MONITOR_OPEN_ORDER_INTERVAL = 28
MONITOR_CLOSE_ORDER_INTERVAL = 98
max_age_seconds =  3 * 24 * 3600  # Timpul maxim în care ordinele executate/filled sunt considerate recente (3 zile)

def monitor_orders():
    #monitor_filled_buy_orders()
    #return
    
    monitor_open_orders_lasttime = time.time() - MONITOR_OPEN_ORDER_INTERVAL - TIME_SLEEP_ERROR
    monitor_close_orders_by_age_lasttime = time.time() - MONITOR_CLOSE_ORDER_INTERVAL - TIME_SLEEP_ERROR

    while not api.stop:
        try:
            currenttime = time.time()
            if(currenttime - monitor_open_orders_lasttime > MONITOR_OPEN_ORDER_INTERVAL) :
                monitor_open_orders_by_type('sell')
                monitor_open_orders_by_type('buy')
                monitor_open_orders_lasttime = currenttime
            if(currenttime - monitor_close_orders_by_age_lasttime > MONITOR_CLOSE_ORDER_INTERVAL) :
                #monitor_close_orders_by_age(max_age_seconds)
                monitor_close_orders_by_age_lasttime = currenttime   
                
            time.sleep(min(MONITOR_OPEN_ORDER_INTERVAL, MONITOR_CLOSE_ORDER_INTERVAL))
            
        except BinanceAPIException as e:
            print(f"Eroare API Binance: {e}")
            time.sleep(TIME_SLEEP_ERROR)
        except Exception as e:
            print(f"Eroare: {e}")
            time.sleep(TIME_SLEEP_ERROR)

monitor_orders()
