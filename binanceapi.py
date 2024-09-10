
import time
import datetime
import math
import sys

import signal
import asyncio
import threading
import websockets
from threading import Thread
import json
#from twisted.internet import reactor

####Binance
from binance.client import Client
from binance.exceptions import BinanceAPIException
#from binance.streams import BinanceSocketManager
#from binance.streams import BinanceSocketManager
#print(dir(BinanceSocketManager))


####MYLIB
import utils
from apikeys import api_key, api_secret

stop = False
symbol = 'BTCUSDT'

import binance
print(binance.__version__)

client = Client(api_key, api_secret)
binancecurrentprice = 0


def listen_to_binance(symbol):
    socket = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@ticker"
    
    # Funcție asincronă pentru WebSocket
    async def connect():
        async with websockets.connect(socket) as websocket:
            while not stop:
                message = await websocket.recv()
                message = json.loads(message)
                process_message(message)

    # Rulăm WebSocket-ul într-un event loop propriu în acest thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(connect())

# Funcție de gestionare a mesajului primit de la WebSocket
def process_message(message):
    symbol = message['s']  # Simbolul criptomonedei
    price = float(message['c'])  # Asigură-te că price este un float
    binancecurrentprice = price
    print(f"ASYNC {symbol} is {price:.2f}")

def start_websocket_thread(symbol):
    websocket_thread = threading.Thread(target=listen_to_binance, args=(symbol,))
    websocket_thread.daemon = True
    websocket_thread.start()
    return websocket_thread

# Start the WebSocket thread
#websocket_thread = start_websocket_thread(symbol)

# Function to handle Ctrl+C and shut down the WebSocket properly
def signal_handler(sig, frame):
    global websocket_thread, stop
    print("Shutting down...")
    stop = True
    loop = asyncio.get_event_loop()
    loop.stop()  # Stop the asyncio event loop
    #websocket_thread.join()  # This makes the main thread wait for the websocket thread to finish
    #if websocket_thread and websocket_thread.is_alive():
    #    websocket_thread.join()
    # Apelare handler implicit pentru SIGINT
    signal.default_int_handler(sig, frame)
    
signal.signal(signal.SIGINT, signal_handler)


  
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

try:
    # Cerere pentru a obține informații despre cont
    account_info = client.get_account()
    print("Cheile API sunt valide!")
except Exception as e:
    print(f"Eroare la verificarea cheilor API: {e}")
    sys.exit()
    
precision = get_quantity_precision(symbol)
precision = 8
print(f"Precision is {precision}")

def get_symbol_limits(symbol):
    info = client.get_symbol_info(symbol)
    if info:
        filters = info['filters']
        for f in filters:
            if f['filterType'] == 'LOT_SIZE':
                min_qty = float(f['minQty'])
                max_qty = float(f['maxQty'])
                step_size = float(f['stepSize'])
                print(f"Min quantity: {min_qty}, Max quantity: {max_qty}, Step size: {step_size}")
                return min_qty, max_qty, step_size
    return None, None, None

def get_current_price(symbol):
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        return float(ticker['price'])
    except BinanceAPIException as e:
        print(f"Eroare la obținerea prețului curent de la Binance API: {e}")
        print(f"Folosesc prețul obținut prin websocket, BTC: {binancecurrentprice}")
        return binancecurrentprice
    except Exception as e:
        # Handle any other exceptions that might occur
        print(f"A apărut o eroare neașteptată: {e}")
        print(f"Folosesc prețul obținut prin websocket, BTC: {binancecurrentprice}")
        return binancecurrentprice
        

def get_asset_info(symbol):
    try:

        asset_info = client.get_asset_balance(asset=symbol)
        print(f"asset_info: {asset_info}")
        return float(asset_info['free']) # info ['locked']
    except Exception as e:
        # Gestionarea altor erori neprevăzute
        print(f"A apărut o eroare: {e}")


def get_open_orders(order_type, symbol):
    if order_type.upper() != 'BUY' and order_type.upper() != 'SELL':
        raise ValueError(f"getting {order_type}. order_type must be 'buy' or 'sell'")
        
    try:
        open_orders = client.get_open_orders(symbol=symbol)
        #print(open_orders)
        filtered_orders = {
            order['orderId']: {
                'price': float(order['price']),
                'quantity': float(order['origQty']),
                'timestamp': order['time'] / 1000
            }
            for order in open_orders if order['side'] == order_type.upper()
        }
        
        return filtered_orders
    except BinanceAPIException as e:
        print(f"Error getting open {order_type} orders: {e}")
        return {}
        
def place_buy_order(symbol, price, quantity):
    try:
        price = round(min(price, get_current_price(symbol)), 0)
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

def place_sell_order_(symbol, price, quantity):
    try:
        # Verificăm limitele pentru simbolul dat
        min_qty, max_qty, step_size = get_symbol_limits(symbol)

        # Asigură-te că cantitatea respectă limitele
        if min_qty is not None and (quantity < min_qty or quantity > max_qty):
            print(f"Quantity {quantity} is out of bounds (min: {min_qty}, max: {max_qty})")
            return None

        # Rotunjim cantitatea la pasul acceptat
        quantity = round(quantity // step_size * step_size, 5)

        # Rotunjim prețul
        price = round(max(price, get_current_price(symbol)), 0)

        # Plasăm ordinul de vânzare
        sell_order = client.order_limit_sell(
            symbol=symbol,
            quantity=quantity,
            price=str(price)
        )
        return sell_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de vânzare: {e}")
        return None



def place_sell_order(symbol, price, quantity):
    try:
        price = round(max(price, get_current_price(symbol)), 0)
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
        #price = round(price, 0)
        quantity = round(quantity, 5)
        cancel = False
        current_price = get_current_price(symbol)
        
        if order_type.lower() == 'buy':
            open_sell_orders = get_open_orders("sell", symbol)
            # Anulează ordinele de vânzare existente la un preț mai mic decât prețul de cumpărare dorit
            for order_id, order_details in open_sell_orders.items():
                if order_details['price'] < price:
                    cancel = cancel_order(order_id)
                    if not cancel:
                        print(f"Fail cancel order {order_id} prep. for buy order")
            
            price = min(price, current_price)
            price = round(price * 0.999, 0)
            order = client.order_limit_buy(
                symbol=symbol,
                quantity=quantity,
                price=str(price)
            )
            # appy pair
            price = max(price * 1.12, current_price)
            price = round(price * 1.001, 0)
            order = client.order_limit_sell(
                symbol=symbol,
                quantity=quantity,
                price=str(price)
            )
        
        elif order_type.lower() == 'sell':
            open_buy_orders = get_open_orders("buy", symbol)
            # Anulează ordinele de cumpărare existente la un preț mai mare decât prețul de vânzare dorit
            for order_id, order_details in open_buy_orders.items():
                if order_details['price'] > price:
                    cancel = cancel_order(order_id)
                    if not cancel:
                        print(f"Fail cancel order {order_id} prep. for sell order")
                   
            price = max(price, current_price)
            price = round(price * (1 + 0.001), 0)
            order = client.order_limit_sell(
                symbol=symbol,
                quantity=quantity,
                price=str(price)
            )
            # appy pair
            price = min(price * 0.12, current_price)
            price = round(price * 0.999, 0)
            order = client.order_limit_buy(
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
        
 #TODO: review it
def place_order_force(order_type, symbol, price, quantity):
    try:
        price = round(price, 0)
        quantity = round(quantity, 5)
        
        # Obține ordinele deschise pentru tipul opus
        if order_type.lower() == 'buy':
            open_orders = get_open_orders("sell", symbol)
        elif order_type.lower() == 'sell':
            open_orders = get_open_rders("buy", symbol)
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
            price = round(min(price, get_current_price(symbol)), 0)
            order = client.order_limit_buy(
                symbol=symbol,
                quantity=quantity,
                price=str(price)
            )
        else:
            price = round(max(price, get_current_price(symbol)), 0)
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
    
    open_orders = get_open_orders(order_type, symbol)

    #current_time = int(time.time() * 1000)  # Convert current time to milliseconds
    current_time = int(time.time())

    print(f"Available open orders {len(open_orders)}. Try cancel {order_type} orders type ... ")
    if len(open_orders) < 1:
        return
    for order_id, order_details in open_orders.items():
        order_time = order_details.get('timestamp')

        if current_time - order_time > expire_time:
            cancel_order(order_id)
            print(f"Cancelled {order_type} order with ID: {order_id} due to expiration.")
        

def check_order_filled(order_id):
    try:
        if not order_id:
            return False
        order = client.get_order(symbol=symbol, orderId=order_id)
        return order['status'] == 'FILLED'
    except BinanceAPIException as e:
        print(f"Eroare la verificarea stării ordinului: {e}")
        return False


