
import time
import datetime
import math
import sys

#my imports
import utils as u
from binanceapi import client

import cacheManager as cm

order_cache_manager = cm.get_order_cache_manager() 
 
  
#######
#######      get_all_orders     #######
#######

def get_filled_orders(order_type, symbol, backdays=3):
    try:
        order_type = order_type.lower()
        end_time = int(time.time() * 1000)  # convert to milisecunde by * 1000
        
        interval_hours = 2 # number of h. for which I request orders - to be less than limit variable
        interval_ms = interval_hours * 60 * 60 * 1000  # interval_hours de ore în milisecunde
        start_time = end_time - backdays * 24 * 60 * 60 * 1000 # backdays * 24 h
       
        all_filtered_orders = []

        # Parcurgem intervale de interval_hours ora și colectăm ordinele 
        # Asta ca sa ma asigur ca am mai putin decat limit pe interval
        while start_time < end_time:
            current_end_time = min(start_time + interval_ms, end_time)
            orders = client.get_all_orders(symbol=symbol, startTime=start_time, endTime=current_end_time, limit=1000)
            print(f"{len(orders)} orders get for interval")
            
            filtered_orders = [
                {
                    'orderId': order['orderId'],
                    'price': float(order['price']),
                    'quantity': float(order['origQty']),
                    'timestamp': order['time'] / 1000,  # Timpul în secunde
                    'side': order['side'].lower()
                }
                for order in orders if order['status'] == 'FILLED' and order['side'].lower() == order_type.lower()
            ]
            
            all_filtered_orders.extend(filtered_orders)
            
            start_time = current_end_time
        
        print(f"Filtered filled orders of type '{order_type}': {len(all_filtered_orders)}")
        print("First few filled orders for inspection:")
        for filled_order in all_filtered_orders[:5]:  # Afișează primele 5 ordine complet executate
            print(filled_order)
        return all_filtered_orders

    except Exception as e:
        print(f"An error occurred: {e}")
        return []


        
def get_recent_filled_orders(order_type, symbol, max_age_seconds):

    backdays = math.ceil(max_age_seconds / (24 * 60 * 60))  # Aproximăm numărul de zile

    all_filled_orders = get_filled_orders(order_type, symbol, backdays)    
    recent_filled_orders = []
    current_time = time.time()
    if(len(all_filled_orders) < 1) :
        return []
    
    print(f"have {len(all_filled_orders)} orders. ignore oldest.")
    for order in all_filled_orders:
        if current_time - order['timestamp'] <= max_age_seconds:
            recent_filled_orders.append(order)

    # Sort the recent_filled_orders by price in ascending order
    recent_filled_orders.sort(key=lambda x: x['price'])

    return recent_filled_orders



# Functia care returneaza tranzactiile de tip "BUY" sau "SELL" din cache pentru un anumit simbol
def get_trade_orders(order_type, symbol, max_age_seconds):
    
    sym.validate_ordertype(order_type)
    sym.validate_symbols(symbol)
    
    current_time_ms = int(time.time() * 1000)
    max_age_ms = max_age_seconds * 1000 #convert to ms
    
    filtered_orders = [
        {
            'orderId': order['orderId'],
            'price': float(order['price']),
            'quantity': float(order['origQty']),
            'timestamp': order['time'] / 1000,  # Timpul în secunde
            'side': order['side'].lower()
        }        
        for order in order_cache_manager.cache
        if order['symbol'] == symbol 
        and (order_type is None or order['side'].upper() == order_type))  # Verifica doar daca order_type nu este None
        and (current_time_ms - trade['time']) <= max_age_ms
    ]

    #  filtered_orders.sort(key=lambda x: x['price'])
    
    return filtered_orders