
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


def get_current_price(symbol):
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        return float(ticker['price'])
    except BinanceAPIException as e:
        print(f"Eroare la obținerea prețului curent: {e}")
        return None
    
def place_buy_order(symbol, price, quantity):
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

def place_sell_order(symbol, price, quantity):
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

def place_order(order_type, symbol, price, quantity):
    try:
        price = round(price, 0)
        quantity = round(quantity, 5)
        
        if order_type.lower() == 'buy':
            open_sell_orders = get_open_sell_orders(symbol)
            # Anulează ordinele de vânzare existente la un preț mai mic decât prețul de cumpărare dorit
            for order_id, order_details in open_sell_orders.items():
                if order_details['price'] < price:
                    cancel_order(order_id)
            
            # Plasează ordinul de cumpărare
            order = client.order_limit_buy(
                symbol=symbol,
                quantity=quantity,
                price=str(price)
            )
        
        elif order_type.lower() == 'sell':
            open_buy_orders = get_open_buy_orders(symbol)
            # Anulează ordinele de cumpărare existente la un preț mai mare decât prețul de vânzare dorit
            for order_id, order_details in open_buy_orders.items():
                if order_details['price'] > price:
                    cancel_order(order_id)
            
            # Plasează ordinul de vânzare
            order = client.order_limit_sell(
                symbol=symbol,
                quantity=quantity,
                price=str(price)
            )
        else:
            print("Tipul ordinului este invalid. Trebuie să fie 'buy' sau 'sell'.")
            return None
        
        return order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de {order_type}: {e}")
        return None

def place_order_force(order_type, symbol, price, quantity):
    try:
        price = round(price, 0)
        quantity = round(quantity, 5)
        
        # Obține ordinele deschise pentru tipul opus
        if order_type.lower() == 'buy':
            open_orders = get_open_sell_orders(symbol)
        elif order_type.lower() == 'sell':
            open_orders = get_open_buy_orders(symbol)
        else:
            print("Tipul ordinului este invalid. Trebuie să fie 'buy' sau 'sell'.")
            return None
        
        # Anulează ordinele existente dacă e necesar
        for order_id, order_details in open_orders.items():
            if (order_type.lower() == 'buy' and order_details['price'] < price) or \
               (order_type.lower() == 'sell' and order_details['price'] > price):
                cancel_order(order_id)
        
        # Plasează ordinul
        if order_type.lower() == 'buy':
            order = client.order_limit_buy(
                symbol=symbol,
                quantity=quantity,
                price=str(price)
            )
        else:
            order = client.order_limit_sell(
                symbol=symbol,
                quantity=quantity,
                price=str(price)
            )
        
        return order
    
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de {order_type}: {e}")

        if "insufficient funds" in str(e).lower():
            print("Fonduri insuficiente detectate. Anulăm un ordin recent și încercăm din nou.")
            
            for order_id in open_orders:
                if cancel_order(order_id):
                    return place_order(order_type, symbol, price, quantity)
        
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


def get_recent_filled_orders(order_type, max_age_seconds):

    all_filled_orders = api.get_filled_orders(order_type)
    recent_filled_orders = []
    current_time = time.time()

    for order in all_filled_orders:
        order_time = order['timestamp']
        
        if current_time - order_time <= max_age_seconds:
            recent_filled_orders.append(order)
    
    return recent_filled_orders

        
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

def cancel_expired_orders(order_type, symbol, expire_time):
    if order_type == 'buy':
        open_orders = get_open_buy_orders(symbol)
    elif order_type == 'sell':
        open_orders = get_open_sell_orders(symbol)
    else:
        raise ValueError("order_type must be 'buy' or 'sell'")
    
    current_time = time.time()

    for order_id, order_details in open_orders.items():
        order_time = order_details.get('timestamp')

        if current_time - order_time > expire_time:
            cancel_order(order_id)
            print(f"Cancelled {order_type} order with ID: {order_id} due to expiration.")


def get_open_sell_orders(symbol):
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

def get_open_orders(order_type, symbol):
    try:
        open_orders = client.get_open_orders(symbol=symbol)
        
        filtered_orders = {
            order['orderId']: {
                'price': float(order['price']),
                'quantity': float(order['origQty'])
            }
            for order in open_orders if order['side'] == order_type.upper()
        }
        
        return filtered_orders
    except BinanceAPIException as e:
        print(f"Error getting open {order_type} orders: {e}")
        return {}

