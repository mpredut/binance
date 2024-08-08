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
monitor_interval = 7.7
def monitor_sell_orders():
   
    
   while True:
        try:
            sell_orders = api.get_open_sell_orders()  # Inițializăm cu ordinele curente de vânzare
            if not sell_orders:
                print("Nu există ordine de vânzare deschise inițial.")
            current_price = api.get_current_price()
            if current_price is None:
                print("Eroare la obținerea prețului. Încerc din nou în câteva secunde.")
                time.sleep(2)
                continue
            print(f"Prețul curent BTC: {current_price:.2f}")
 
            for order_id in list(sell_orders.keys()):
                sell_order = sell_orders[order_id]
                sell_price = sell_order['price']
                
                difference_percent = abs(current_price - sell_price) / sell_price * 100
                print(f"sell price {sell_price}, current price {current_price} difference: {difference_percent:.2f}%, Order ID {order_id} ")
                
                are_close, iterations, final_tolerance = utils.are_values_very_close(current_price, sell_price, 0.77)
                if are_close:
                    print(f"Current price {current_price} and sell price {sell_price} are close! ")
                    api.cancel_order(order_id)
                    
                    new_sell_price = round(current_price * 1.001 + 500, 2)
                    quantity = sell_order['quantity']
                    
                    new_order = api.place_sell_order(new_sell_price, quantity)
                    if new_order:    
                        sell_orders[new_order['orderId']] = {
                            'price': new_sell_price,
                            'quantity': quantity
                        }
                        del sell_orders[order_id]
                        print(f"Update order from {sell_price} to {new_sell_price}. New ID: {new_order['orderId']}")
                    else:
                        print("Eroare la plasarea noului ordin de vânzare.")
                        
            time.sleep(monitor_interval)
            
        except BinanceAPIException as e:
            print(f"Eroare API Binance: {e}")
            time.sleep(1)
        except Exception as e:
            print(f"Eroare: {e}")
            time.sleep(1)

monitor_sell_orders()
