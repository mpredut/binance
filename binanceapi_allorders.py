
import time
import datetime
import math
import sys

####Binance
from binance.client import Client
from binance.exceptions import BinanceAPIException

#my imports
from binanceapi import client
 
  
#######
#######      get_all_orders     #######
#######

#TODO: need to review!
#start_time = int((datetime.datetime.now() - datetime.timedelta(days=backdays)).timestamp() * 1000)
def get_filled_orders(order_type, symbol, backdays=3):
    try:
        order_type = order_type.lower()
        end_time = int(time.time() * 1000)  # milisecunde
        
        interval_hours = 1
        interval_ms = interval_hours * 60 * 60 * 1000  # interval_hours de ore în milisecunde
        start_time = end_time - backdays * 24 * 60 * 60 * 1000
       
        all_filtered_orders = []

        # Parcurgem intervale de 1 ora și colectăm ordinele
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
            
            # Actualizăm start_time pentru următorul interval
            start_time = current_end_time
        
        print(f"Filtered filled orders of type '{order_type}': {len(all_filtered_orders)}")
        #print("First few filled orders for inspection:")
        #for filled_order in all_filtered_orders[:5]:  # Afișează primele 5 ordine complet executate
            #print(filled_order)
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
    
    print(f"have len(all_filled_orders) orders. ignore oldest.")
    for order in all_filled_orders:
        if current_time - order['timestamp'] <= max_age_seconds:
            recent_filled_orders.append(order)

    # Sort the recent_filled_orders by price in ascending order
    recent_filled_orders.sort(key=lambda x: x['price'])

    return recent_filled_orders


