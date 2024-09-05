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

import threading
import time


TIME_SLEEP_ERROR = 10


def adjust_monitor_interval(initial_interval, min_interval, total_duration, elapsed_time):
    if elapsed_time >= total_duration:
        return min_interval
    
    interval_range = initial_interval - min_interval
    time_fraction = elapsed_time / total_duration
    current_interval = initial_interval - (interval_range * time_fraction)
    
    return max(current_interval, min_interval)
    
def calculate_target_price(filled_price, current_price, procent_defined, time_fraction):
    # Calculul procentului ajustat inițial
    procent_adjusted = (procent_defined * (1 - time_fraction)) - (1 - current_price / filled_price)

    # Calculul prețului țintă inițial
    target_price = filled_price * (1 + procent_adjusted)
    
    # Dacă target_price a ajuns sub current_price, îl ajustăm
    if target_price < current_price:
        # Definim un dinamic_procent care scade treptat în timp
        dinamic_procent = 0.01 * (1 - time_fraction) + 1  # Începe de la 1.01 și scade către 1
        target_price = current_price * dinamic_procent
    
    return target_price
 
procent_defined = 0.10  # Procentul inițial (10%)
def sell_order_gradually(order, start_time, end_time):

    filled_quantity = order['quantity']
    filled_price = order['price']
    close_order_id = order.get('orderId')
    order_id = None

    initial_interval = 20  # Interval inițial de monitorizare (în secunde)
    min_interval = 5       # Interval minim de monitorizare (în secunde)
    total_duration = end_time - start_time  # Durata totală a procesului
    current_time = start_time
    
    #while time.time() < end_time:
    while current_time < end_time:
        #elapsed_time = time.time() - start_time
        elapsed_time = current_time - start_time
        monitor_interval = adjust_monitor_interval(initial_interval, min_interval, total_duration, elapsed_time)
         
        print(f"Monitor interval: {monitor_interval:.2f} seconds")

        current_price = get_current_price(api.symbol)

        if current_price is None:
            print("Eroare la obținerea prețului. Încerc din nou în câteva secunde.")
            time.sleep(monitor_interval)
            continue

        
        time_fraction = elapsed_time / total_duration
        target_price = calculate_target_price(filled_price, current_price, procent_defined, time_fraction)

        # Calculăm prețul propus
        #if current_price > filled_price:
        #    target_price = max(filled_price * 1.01, current_price * 1.01)  # Preț mai mare cu 1%
        #else:
        #    target_price = filled_price * (1 + time_fraction * (current_price / filled_price - 1))

        print(f"Vânzare graduală: target_price={target_price:.2f}, current_price={current_price:.2f}")
        print(f"Elapsed Time: {elapsed_time:.2f} seconds, Target Price: {target_price:.2f} USD")

        # Anulăm ordinul anterior înainte de a plasa unul nou
        if order_id:
            if check_order_filled(order_id) :
                return; #order filled!
            cancel_order(order_id)
            print(f"Anulat ordinul anterior cu ID: {order_id}")

        # Plasăm ordinul de vânzare
        new_order = api.place_sell_order(symbol, target_price, filled_quantity)
        if new_order:
            order_id = new_order['orderId']
            print(f"Plasat ordin de vânzare la prețul {target_price:.2f}. New Order ID: {order_id}")
        else:
            print("Eroare la plasarea ordinului de vânzare.")
            order_id = None  # Resetează ID-ul ordinului dacă plasarea eșuează
        
        # Așteptăm un interval ajustat înainte de următoarea ajustare
        time.sleep(monitor_interval)
        current_time += monitor_interval



def monitor_filled_buy_orders_old():
    if threading.active_count() > 1:  # Dacă sunt deja fire active (în afară de firul principal)
        print("Fire active detectate, ieșim din funcție pentru a nu porni fire noi.")
        return
 
    max_age_seconds =  3 * 24 * 3600  # Timpul maxim în care ordinele executate sunt considerate recente (2 ore)
    filled_buy_orders = api.get_recent_filled_orders('buy', max_age_seconds)

    for order in filled_buy_orders:
        current_time = time.time()
        end_time = current_time + 2 * 3600  # Procesul durează două ore
        print("marius")
        print(order)
        # Pornim un fir nou pentru fiecare ordin de cumpărare executat recent
        #thread = threading.Thread(target=sell_order_gradually, args=(order, current_time, end_time))
        #thread = threading.Thread(target=sell_order_gradually, args=(order, current_time, end_time, filled_price, current_price, procent_defined))      
        #thread.start()



def monitor_close_orders_by_age(max_age_seconds):
    if threading.active_count() > 2:  # Dacă sunt deja fire active (în afară de firul principal)
        print("Fire active detectate, ieșim din funcție pentru a nu porni fire noi.")
        return
 
    close_buy_orders = api.get_recent_filled_orders('buy', max_age_seconds)

    for order in close_buy_orders:
        current_time = time.time()
        end_time = current_time + 2 * 3600  # Procesul durează două ore
        filled_price = order['price']
        quantity = order['quantity']

        current_price = api.get_current_price(api.symbol) + 200

        if current_price >= filled_price * 1.07:  # Dacă prețul curent este cu 7% mai mare
            print(f"Prețul curent ({current_price}) este cu 7% mai mare decât prețul de cumpărare ({filled_price}). Inițiem vânzarea.")
            
            # Pornim un fir nou pentru a vinde BTC-ul
            thread = threading.Thread(target=place_sell_order, args=(symbol, current_price, quantity))
            #sell_order_gradually, args=(order, current_time, end_time))
            thread.start()
        else:
            print(f"Prețul curent ({current_price}) nu a atins încă pragul de 7% față de prețul de cumpărare ({filled_price}).")
            return



def monitor_open_orders_by_type(order_type):
    orders = api.get_open_orders(order_type, api.symbol)  # Obține ordinele de vânzare sau cumpărare în funcție de tip
    if not orders:
        print(f"Nu există ordine de {order_type} deschise inițial.")
        return
    
    current_price = api.get_current_price(api.symbol)
    if current_price is None:
        print("Eroare la obținerea prețului...")
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
                print(f"Totusi prețul s-a modificat prea mult({difference_percent}%) față de prețul inițial ({initial_prices[order_id]}). Nu se mai modifică ordinul.")
                continue
            else:
                print(f"Current price {current_price} and initial {order_type} price {initial_prices[order_id]} are close!")
            
            if not api.cancel_order(order_id):
                initial_prices.pop(order_id)
                continue
            
            if order_type == 'sell':
                new_price = current_price * 1.001 + 100
            else:
                new_price = current_price * 0.999 - 100
            
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
    



MONITOR_OPEN_ORDER_INTERVAL = 18
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
                monitor_close_orders_by_age(max_age_seconds)
                monitor_close_orders_by_age_lasttime = currenttime   
                
            time.sleep(min(MONITOR_OPEN_ORDER_INTERVAL, MONITOR_CLOSE_ORDER_INTERVAL))
            
        except BinanceAPIException as e:
            print(f"Eroare API Binance: {e}")
            time.sleep(TIME_SLEEP_ERROR)
        except Exception as e:
            print(f"Eroare: {e}")
            time.sleep(TIME_SLEEP_ERROR)

monitor_orders()
