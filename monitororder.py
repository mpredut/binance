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
initial_prices = {}  # Dicționar pentru a reține prețurile inițiale ale ordinelor
initial_sell_prices = {}  # Dicționar pentru a reține prețurile inițiale ale ordinelor de vânzare
initial_buy_prices = {}  # Dicționar pentru a reține prețurile inițiale ale ordinelor de cumpărare
max_adjustments = 1000  # Număr maxim de ajustări pentru un ordin


def monitor_orders_by_type(order_type):
    orders = api.get_open_orders(order_type, api.symbol)  # Obține ordinele de vânzare sau cumpărare în funcție de tip
    if not orders:
        print(f"Nu există ordine de {order_type} deschise inițial.")
        return
    
    current_price = api.get_current_price(api.symbol)
    if current_price is None:
        print("Eroare la obținerea prețului. Încerc din nou în câteva secunde.")
        time.sleep(2)
        return
    
    print(f"Prețul curent BTC: {current_price:.2f}")
    
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
                print(f"Totusi prețul s-a modificat prea mult față de prețul inițial ({initial_prices[order_id]}). Nu se mai modifică ordinul.")
                continue
            else:
                print(f"Current price {current_price} and initial {order_type} price {initial_prices[order_id]} are close!")
            
            if not api.cancel_order(order_id):
                initial_prices.pop(order_id)
                continue
            
            if order_type == 'sell':
                new_price = current_price * 1.001 + 200
            else:
                new_price = current_price * 0.999 - 200
            
            quantity = order['quantity']
            
            new_order = api.place_order(order_type,  api.symbol, new_price, quantity)
            
            if new_order:    
                orders[new_order['orderId']] = {
                    'price': new_price,
                    'quantity': quantity
                }
                initial_prices[new_order['orderId']] = initial_prices.pop(order_id)  # Păstrăm prețul inițial
                print(f"Update order from {price} to {new_price}. New ID: {new_order['orderId']}")
            else:
                print(f"Eroare la plasarea noului ordin de {order_type}.")
    
    time.sleep(monitor_interval)

def monitor_orders():
    while True:
        try:
            monitor_orders_by_type('sell')
            monitor_orders_by_type('buy')
            
        except BinanceAPIException as e:
            print(f"Eroare API Binance: {e}")
            time.sleep(1)
        except Exception as e:
            print(f"Eroare: {e}")
            time.sleep(1)

monitor_orders()
