import json
import os
import time
from threading import Thread
from datetime import datetime
import threading

####Binance
#from binance.client import Client
#from binance.exceptions import BinanceAPIException

#my imports
import binanceapi as api
import binanceapi_trades as apitrades
import binanceapi_allorders as apiorders

import utils

# Funcția principală care rulează periodic actualizările și cache-ul
def monitor_trades(order_type, symbol, filename, interval=3600, limit=1000, years_to_keep=2):
    while True:
        # Actualizăm fișierul de tranzacții
        apitrades.save_trades_to_file(order_type, symbol, filename, limit=limit, years_to_keep=years_to_keep)
        
        # Reîncărcăm tranzacțiile în cache
        apitrades.load_trades_from_file(filename)
        
        # Așteptăm intervalul configurat înainte de a repeta procesul
        time.sleep(interval)

# Funcția pentru a porni monitorizarea periodică într-un thread separat
def start_monitoring(order_type, symbol, filename, interval=3600, limit=1000, years_to_keep=2):
    monitoring_thread = Thread(target=monitor_trades, args=(order_type, symbol, filename, interval, limit, years_to_keep), daemon=True)
    monitoring_thread.start()



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
    filled_buy_orders = apiorders.get_recent_filled_orders('buy', symbol, max_age_seconds)

    for order in filled_buy_orders:
        current_time = time.time()
        end_time = current_time + 2 * 3600  # Procesul durează două ore
        print("marius")
        print(order)
        # Pornim un fir nou pentru fiecare ordin de cumpărare executat recent
        #thread = threading.Thread(target=sell_order_gradually, args=(order, current_time, end_time))
        #thread = threading.Thread(target=sell_order_gradually, args=(order, current_time, end_time, filled_price, current_price, procent_defined))      
        #thread.start()


def get_close_buy_orders_without_sell(api, max_age_seconds, profit_percentage):
    close_buy_orders = apiorders.get_trade_orders('buy', symbol, max_age_seconds)
    close_sell_orders = apiorders.get_trade_orders('sell', symbol, max_age_seconds)
    
    # Lista de ordere 'buy' care nu au un 'sell' asociat cu profitul dorit
    buy_orders_without_sell = []

    for buy_order in close_buy_orders:
        filled_price = buy_order['filled_price']
        symbol = buy_order['symbol']
        buy_quantity = buy_order['quantity']  # Cantitatea cumpărată
        
        # Filtrează orderele de tip 'sell' asociate cu acest 'buy' (același simbol și cu prețul dorit)
        related_sell_orders = [
            order for order in close_sell_orders 
            if order['symbol'] == symbol and order['filled_price'] >= filled_price * (1 + profit_percentage / 100)
        ]
        
        # Calculează suma cantității vândute pentru orderele 'sell' găsite
        total_sell_quantity = sum(order['quantity'] for order in related_sell_orders)
        
        # Dacă cantitatea totală vândută este mai mică decât cantitatea cumpărată
        if total_sell_quantity < buy_quantity:
            # Adaugă buy_order la lista de ordere care încă nu au sell complet
            buy_orders_without_sell.append(buy_order)

    return buy_orders_without_sell
    

max_age_seconds =  3 * 24 * 3600  # Timpul maxim în care ordinele executate/filled sunt considerate recente (3 zile)

def monitor_close_orders_by_age(max_age_seconds):
    if threading.active_count() > 2:  # Dacă sunt deja fire active (în afară de firul principal)
        print("Fire active detectate, ieșim din funcție pentru a nu porni fire noi.")
        return
 
    close_buy_orders = apiorders.get_trade_orders('buy',  symbol, max_age_seconds)

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
# Exemplu de apel pentru a porni monitorizarea periodică
if __name__ == "__main__":
    symbol = "BTCUSDT"
    filename = "trades_BTCUSDT.json"
    order_type = "buy"
    interval = 3600  # 1 oră

    # Pornim monitorizarea periodică a tranzacțiilor
    start_monitoring(order_type, symbol, filename, interval=interval, limit=1000, years_to_keep=2)

    # Simulare: extragem ordinele recente de tip 'buy'
    while True:
        time.sleep(10)  # Periodic, verificăm ordinele în cache
        max_age_seconds = 86400 *8
        close_orders = apitrades.get_trade_orders('buy', symbol, max_age_seconds)  # Extragere ordine de 'buy' în ultimele 24 de ore
        print(f"get_trade_orders:           Found {len(close_orders)} close 'buy' orders in the last 24 hours.")
        #close_orders_all = apiorders.get_recent_filled_orders('buy', symbol, max_age_seconds)  # Extragere ordine de 'buy' în ultimele 24 de ore
        #print(f"get_recent_filled_orders:   Found {len(close_orders_all)} close 'buy' orders in the last 24 hours.")
        #print(close_orders)
        #print(close_orders_all)
        monitor_close_orders_by_age(max_age_seconds)
