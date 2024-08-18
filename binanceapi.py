
import time
import datetime
import math
import sys

####Binance
from binance.client import Client
from binance.exceptions import BinanceAPIException

####MYLIB
import utils
from apikeys import api_key, api_secret

symbol = 'BTCUSDT'

client = Client(api_key, api_secret)

try:
    # Cerere pentru a obține informații despre cont
    account_info = client.get_account()
    print("Cheile API sunt valide!")
except Exception as e:
    print(f"Eroare la verificarea cheilor API: {e}")
    sys.exit()
    
def get_quantity_precision(symbol):
    try:
        info = client.get_symbol_info(symbol)
        for filter in info['filters']:
            if filter['filterType'] == 'LOT_SIZE':
                step_size = filter['stepSize']
                precision = -int(round(-math.log10(float(step_size)), 0))
                return precision
    except BinanceAPIException as e:
        print(f"Eroare la obținerea preciziei cantității: {e}")
    return 8  # Valoare implicită

precision = get_quantity_precision(symbol)
precision = 8
print(f"Precision is {precision}")


def get_current_price():
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        return float(ticker['price'])
    except BinanceAPIException as e:
        print(f"Eroare la obținerea prețului curent: {e}")
        return None
    
def place_buy_order(price, quantity):
    try:
        price = round(price, 0)
        quantity = round(quantity, 5)    
        buy_order = client.order_limit_buy(
            symbol=symbol,
            quantity=quantity,
            price=str(price)
        )
        return buy_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de cumpărare: {e}")
        return None

def place_sell_order(price, quantity):
    try:
        price = round(price, 0)
        quantity = round(quantity, 5)    
        sell_order = client.order_limit_sell(
            symbol=symbol,
            quantity=quantity,
            price=str(price)
        )
        return sell_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de vânzare: {e}")
        return None

def check_order_filled(order_id):
    try:
        if not order_id:
            return False
        order = client.get_order(symbol=symbol, orderId=order_id)
        return order['status'] == 'FILLED'
    except BinanceAPIException as e:
        print(f"Eroare la verificarea stării ordinului: {e}")
        return False
        
def cancel_order(order_id):
    try:
        if not order_id:
            return False
        client.cancel_order(symbol=symbol, orderId=order_id)
        print(f"Ordinul cu ID {order_id} a fost anulat.")
        return True
    except BinanceAPIException as e:
        print(f"Eroare la anularea ordinului: {e}")
        return False

def get_open_sell_orders():
    try:
        open_orders = client.get_open_orders(symbol=symbol)
        sell_orders = {
            order['orderId']: {
                'price': float(order['price']),
                'quantity': float(order['origQty'])
            }
            for order in open_orders if order['side'] == 'SELL'
        }
        return sell_orders
    except BinanceAPIException as e:
        print(f"Error getting open sell orders: {e}")
        return {}

def get_open_buy_orders(symbol):
    try:
        open_orders = client.get_open_orders(symbol=symbol)
        buy_orders = {
            order['orderId']: {
                'price': float(order['price']),
                'quantity': float(order['origQty'])
            }
            for order in open_orders if order['side'] == 'BUY'
        }
        return buy_orders
    except BinanceAPIException as e:
        print(f"Error getting open buy orders: {e}")
        return {}
