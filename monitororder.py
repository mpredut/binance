import time
import datetime
import random
from collections import OrderedDict

####Binance
from binance.client import Client
from binance.exceptions import BinanceAPIException

#my imports
import utils as u
import symbols as sym
from binance_api import bapi as api
from binance_api import bapi_placeorder as po
from providers.market_api import api as mkt   # single guarded proxy (Instrument.place)



# 
MAX_PROC = 0.77
monitor_interval = 3.7
MAX_TRACKED_ORDER_IDS = 10_000
initial_prices = OrderedDict()
initial_sell_prices = OrderedDict()
initial_buy_prices = OrderedDict()


def _remember_initial_price(cache, order_id, price):
    cache[order_id] = price
    cache.move_to_end(order_id)
    while len(cache) > MAX_TRACKED_ORDER_IDS:
        cache.popitem(last=False)


TIME_SLEEP_ERROR = 10


def monitor_open_orders_by_type(symbol, order_type, failed_orders=None):
    """Reprice eligible open orders without owning a second retry loop.

    ``mkt.place`` persists every replacement intent in the shared outbox before
    submit.  ``failed_orders`` remains as a compatibility-only argument for old
    callers; this function neither writes nor drains it.
    """
    orders = api.get_open_orders(order_type, symbol)  # Fetch open orders for the requested side.
    if not orders:
        print(f"Pentru {symbol} Nu exista ordine de {order_type} deschise initial.")
        return
    
    current_price = api.get_current_price(symbol)
    if current_price is None:
        print("Eroare la obtinerea pretului...")
        return
    
    print(f"Pretul curent : {current_price:.2f}")
    
    initial_prices = initial_sell_prices if order_type == "SELL" else initial_buy_prices
    
    for order_id in list(orders.keys()):
        order = orders[order_id]
        price = order['price']

        if not price or price <= 0:          # Guard against division by zero for invalid prices.
            print(f"Preț invalid ({price}) pentru ordinul {order_id}, sar peste.")
            continue

        if order_id not in initial_prices:
            _remember_initial_price(initial_prices, order_id, price)

        difference_percent = abs(current_price - price) / price * 100
        print(f"{order_type.capitalize()} price {price}, current price {current_price} difference: {difference_percent:.2f}%, Order ID {order_id}")
        
        are_close = u.are_close(current_price, price, MAX_PROC)
        if are_close:
            print(f"Current price {current_price} and {order_type} price {price} are close!")
            
            difference_percent = abs(current_price - initial_prices[order_id]) / initial_prices[order_id] * 100
            are_close = u.are_close(current_price, initial_prices[order_id], MAX_PROC)
            if not are_close:
                print(f"Totusi pretul s-a modificat prea mult ({difference_percent}%) fata de pretul initial ({initial_prices[order_id]}). Nu se mai modifica ordinul.")
                continue
            else:
                print(f"Current price {current_price} and initial {order_type} price {initial_prices[order_id]} are close!")
            
            if not api.cancel_order(symbol, order_id):
                initial_prices.pop(order_id)
                continue
            
            # Calculate the new price according to the order side.
            if order_type == "SELL":
                new_price = current_price * 1.001 + 1
            else:
                new_price = current_price * 0.999 - 1
            
            quantity = order['quantity']
            
            # Attempt placement through the single guarded proxy.
            new_order = mkt.place(symbol, order_type, new_price, quantity, smart=False)
            
            if new_order:
                orders[new_order['orderId']] = {
                    'price': new_price,
                    'quantity': quantity
                }
                _remember_initial_price(
                    initial_prices, new_order['orderId'], initial_prices.pop(order_id))
                print(f"Updated order from {price} to {new_price}. New ID: {new_order['orderId']}")
            else:
                print(
                    f"Inlocuirea {order_type} nu a fost acceptata imediat; "
                    "intentia persistata ramane in outboxul comun pentru retry."
                )
    


MONITOR_BETWEEN_ORDERS_INTERVAL = 2
MONITOR_OPEN_ORDER_INTERVAL = 8
MONITOR_CLOSE_ORDER_INTERVAL = 8
max_age_seconds =  3 * 24 * 3600  # Maximum age for treating filled orders as recent (three days).

def monitor_orders():
    #monitor_filled_buy_orders()
    #return
    
    monitor_open_orders_lasttime = time.time() - MONITOR_OPEN_ORDER_INTERVAL - TIME_SLEEP_ERROR
    monitor_close_orders_by_age_lasttime = time.time() - MONITOR_CLOSE_ORDER_INTERVAL - TIME_SLEEP_ERROR

    while not api.stop:
        try:
            currenttime = time.time()
            if(currenttime - monitor_open_orders_lasttime > MONITOR_OPEN_ORDER_INTERVAL) :
                for symbol in sym.symbols:
                    monitor_open_orders_by_type(symbol, "SELL")
                    monitor_open_orders_by_type(symbol, "BUY")
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


if __name__ == "__main__":
    monitor_orders()
