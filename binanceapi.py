
import time
import datetime
import math
import sys
from datetime import datetime, timedelta

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
taosymbol = 'TAOUSDT'
symbols = [symbol, taosymbol]

import binance
print(binance.__version__)
currentprice = {}
#currentprice[symbol] = 0
#currentprice['TAOUSDT'] = 0

currenttime = time.time()
client = Client(api_key, api_secret)


def validate_params(order_type, symbol, price = 1, quantity = 1):
    if order_type not in ['BUY', 'SELL']:
        raise ValueError(f"Invalid order_type '{order_type}'. It must be either 'BUY' or 'SELL'.")
    
    if not isinstance(price, (int, float)) or price <= 0:
        raise ValueError(f"Invalid price '{price}'. Price must be a positive number.")
    
    if not isinstance(quantity, (int, float)) or quantity <= 0:
        raise ValueError(f"Invalid quantity '{quantity}'. Quantity must be a positive number.")
    
    if symbol.upper() not in symbols:
        raise ValueError(f"Invalid symbol '{symbol}'. Symbol must be one of {valid_symbols}.")



def get_binance_symbols(keysearch):
    try:
        exchange_info = client.get_exchange_info()
        print(f"Number of symbols on Binance: {len(exchange_info['symbols'])}")

        symbols = [s['symbol'] for s in exchange_info['symbols']]  # Extragem doar simbolul
        if keysearch:
            matching_symbols = [symbol for symbol in symbols if keysearch.upper() in symbol]
            print(f"Symbols containing '{keysearch}': {matching_symbols}")
        else:
            print(f"All symbols: {symbols}")
    
    except Exception as e:
        print(f"An error occurred: {e}")


def listen_to_binance(symbol):
    socket = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@ticker"
    
    # Functie asincrona pentru WebSocket
    async def connect():
        async with websockets.connect(socket) as websocket:
            while not stop:
                message = await websocket.recv()
                message = json.loads(message)
                process_message(symbol, message)

    # Rulam WebSocket-ul într-un event loop propriu în acest thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(connect())

# Functie de gestionare a mesajului primit de la WebSocket
def process_message(symbol, message):
    global currentprice
    symbol = message['s']  # Simbolul criptomonedei
    price = float(message['c'])  # Asigura-te ca price este un float
    currentprice[symbol] = price
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
        print(f"Eroare la obtinerea preciziei cantitatii: {e}")
    return 8  # Valoare implicita

try:
    # Cerere pentru a obtine informatii despre cont
    account_info = client.get_account()
    print("Cheile API sunt valide!")
except Exception as e:
    print(f"Eroare la verificarea cheilor API: {e}")
    sys.exit()


precision = get_quantity_precision(symbol)
precision = 8
print(f"Precision is {precision}")

def normalize_quantity(symbol, quantity):
    min_qty, max_qty, step_size = get_symbol_limits(symbol)
    if quantity < min_qty:
        print(f"Quantity {quantity} is below the minimum limit. Setting to minimum: {min_qty}")
        quantity = min_qty
    elif quantity > max_qty:
        print(f"Quantity {quantity} is above the maximum limit. Setting to maximum: {max_qty}")
        quantity = max_qty
    
    adjusted_quantity = round(quantity // step_size * step_size, 5)
    
    if adjusted_quantity < min_qty:
        adjusted_quantity = min_qty
    elif adjusted_quantity > max_qty:
        adjusted_quantity = max_qty
    
    return adjusted_quantity


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

refresh_interval = 0 # Intervalul în care sa se faca actualizarea (în secunde)
def get_current_price(symbol):
    global currenttime
    global currentprice
    global refresh_interval
    #refresh_interval = 0 # Intervalul în care sa se faca actualizarea (în secunde)
    try:
        if symbol not in currentprice or (currenttime + refresh_interval <= time.time()):
            refresh_interval = 2
            ticker = client.get_symbol_ticker(symbol=symbol)  # Obtineti pretul curent de la Binance API
            currentprice[symbol] = float(ticker['price'])
            currenttime = time.time()

        return currentprice[symbol]
    
    except BinanceAPIException as e:
        print(f"Eroare la obtinerea pretului curent de la Binance API: {e}")
        print(f"Folosesc pretul obtinut prin websocket, {symbol}: {currentprice.get(symbol, 'N/A')}")
        return currentprice.get(symbol, None)  # Returnam None daca simbolul nu exista
    
    except Exception as e:
        print(f"get_current_price: A aparut o eroare neasteptata: {e}")
        print(f"Folosesc pretul obtinut prin websocket, {symbol}: {currentprice.get(symbol, 'N/A')}")
        return currentprice.get(symbol, None)  # Returnam None daca simbolul nu exista

        
def get_current_time():
        global currenttime
        currenttime = time.time()
        return currenttime

def get_asset_info(order_type, symbol):
    try:
        if order_type.upper() == 'SELL':
            symbol = symbol[:-4] #BTC
        if order_type.upper() == 'BUY':
            symbol = symbol[3:] #USDT
        #print(f"asset_info for {order_type} {symbol}")
        asset_info = client.get_asset_balance(asset=symbol)
        #print(f"asset_info: {asset_info}")
        if asset_info['free'] is None:
            return 0
        return float(asset_info['free']) # info ['locked']
    except Exception as e:
        return 0
        print(f"get_asset_info: A aparut o eroare: {e}")


def manage_quantity(order_type, symbol, required_qty, cancelorders=False, hours=5):

    available_qty = get_asset_info(order_type, symbol)
    
    if available_qty < required_qty:
        print(f"Not enough available {symbol}. Available: {available_qty:.8f}, Required: {required_qty:.8f}")
     
        freed_quantity = 0
        if cancelorders:
            freed_quantity = cancel_orders_old_or_outlier(
                order_type, symbol, required_qty, hours=hours, price_difference_percentage=0.1
            )
        
        available_qty += freed_quantity

        if available_qty < required_qty:
            print(f"Still not enough quantity. Adjusting order quantity to {available_qty:.8f}")
            return available_qty
    else:
        return available_qty
    
    return available_qty


def cancel_orders_old_or_outlier(order_type, symbol, required_quantity, hours=5, price_difference_percentage=0.1):

    order_type = order_type.upper()
    validate_params(order_type, symbol, 1, required_quantity)
    
    open_orders = get_open_orders(order_type, symbol)
    available_qty = 0  # Initial nu ai nicio cantitate disponibila
    current_price = get_current_price(symbol)
    if open_orders:
        # Sorteaza ordinele descrescator pentru SELL sau crescator pentru BUY
        sorted_orders = sorted(
            open_orders.items(),
            key=lambda x: (x[1]['price'] if order_type == 'BUY' else -x[1]['price'])
        )

        # Timpul limita (cutoff) pentru ordinele recente
        cutoff_time = datetime.now().timestamp() - timedelta(hours=hours).total_seconds()

        for order_id, order_info in sorted_orders:
            cancel = False
            if order_info['timestamp'] <= cutoff_time:
                cancel = True
            else:
                price_diff_percentage = abs(order_info['price'] - current_price) / current_price * 100
                if price_diff_percentage >= price_difference_percentage * 100:  # Convertim 0.1 în 10%
                    cancel = True

            if cancel:
                cancel_order(symbol, order_id)
                available_qty += order_info['quantity']
                print(f"New available quantity: {available_qty:.8f}")

            if available_qty >= required_quantity:
                break

    return available_qty


    


def get_open_orders(order_type, symbol):

    order_type = order_type.upper()
    validate_params(order_type, symbol)
        
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
    except Exception as e:
        print(f"Error getting open {order_type} orders: {e}")
        return {}
        
def place_BUY_order(symbol, price, quantity):
    try:
        price = round(min(price, get_current_price(symbol)), 0)
        quantity = round(quantity, 5)    
        BUY_order = client.order_limit_buy(
            symbol=symbol,
            quantity=quantity,
            price=str(price)
        )
        return BUY_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de cumparare: {e}")
        return None

def place_SELL_order(symbol, price, quantity):
    try:
        price = round(max(price, get_current_price(symbol)), 0)
        quantity = round(quantity, 5)    
        SELL_order = client.order_limit_sell(
            symbol=symbol,
            quantity=quantity,
            price=str(price)
        )
        return SELL_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de vanzare: {e}")
        return None


def place_safe_order(order_type, symbol, price, quantity, time_back_in_seconds=3600*2, max_daily_trades=20, profit_percentage = 0.4):
    import binanceapi_trades as apitrades

    order_type = order_type.upper()
    validate_params(order_type, symbol, price, quantity)    
    try:
        
        current_price = get_current_price(symbol)
        
        if order_type == "BUY":
            price = round(min(price, current_price), 0)
        else:  # pentru "SELL"
            price = round(max(price, current_price), 0)

        quantity = round(quantity, 4)

        opposite_order_type = "SELL" if order_type == "BUY" else "BUY"
        previous_trades = apitrades.get_my_trades(opposite_order_type, symbol, backdays=0, limit=1000) ## curent date
        if len(previous_trades) >= max_daily_trades:
            print(f"Am {len(previous_trades)} trades. Limita zilnica este de {max_daily_trades} pentru'{order_type}'.")
            return None
        print(f"Am {len(previous_trades)} trades");
        
        time_limit = int(time.time() * 1000) - (time_back_in_seconds * 1000)  # în milisecunde
        
        # Filtram tranzactiile opuse care au avut loc în intervalul specificat
        recent_opposite_trades = [trade for trade in previous_trades if trade['time'] >= time_limit]
        
        #max_SELL_price = max(float(trade['quoteQty']) / float(trade['qty']) for trade in recent_opposite_trades)
        if recent_opposite_trades:
            if order_type == "BUY":
                last_sell_price = min(float(trade['price']) for trade in recent_opposite_trades)
                diff_percent = value_diff_to_percent(last_sell_price, current_price)
            else:  # pentru `sell`
                last_buy_price = max(float(trade['price']) for trade in recent_opposite_trades)
                diff_percent = value_diff_to_percent(current_price, last_buy_price)
                
            if diff_percent < required_percentage_diff:
                    print(f"Diferenta procentuala ({diff_percent:.2f}%) este sub pragul necesar de {required_percentage_diff}%. Ordinul de {order_type} nu a fost plasat.")
                    return None
            
        order = None
        if order_type == "BUY":
            order = client.order_limit_buy(
                symbol=symbol,
                quantity=quantity,
                price=str(price)
            )
        elif order_type == "SELL":
            order = client.order_limit_sell(
                symbol=symbol,
                quantity=quantity,
                price=str(price)
            )
        
        if order:
            print(f"{order_type} order placed successfully: {order['orderId']}")
        else :
            print(f"Eroare la plasarea ordinului de {order_type}")
        return order

    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de {order_type}: {e}")
        return None



from decimal import Decimal, ROUND_DOWN
def place_order(order_type, symbol, price, qty, cancelorders=False, hours=5, fee_percentage=0.001):
    
    order_type = order_type.upper()
    validate_params(order_type, symbol, price, qty)  
    
    try:
        print(f"Order Request {order_type.upper()} {symbol} qty {qty}, Price {price}")
        available_qty = manage_quantity(order_type, symbol, qty, cancelorders=cancelorders, hours=hours)
        
        if order_type.upper() == 'SELL':
            # Verifica daca ai destula criptomoneda pentru a vinde
            if available_qty <= 0:
                print(f"No sufficient quantity available to place the {order_type.upper()} order.")
                return None
            
            print(f"available_qty {available_qty:.8f} versus requested {qty:.8f}")
            
            adjusted_qty = qty * (1 + fee_percentage)

            if available_qty < adjusted_qty:
                print(f"Adjusting {order_type.upper()} order quantity from {qty:.8f} to {available_qty / (1 + fee_percentage):.8f} to cover fees")
                qty = available_qty / (1 + fee_percentage)

        elif order_type.upper() == 'BUY':
            # În cazul unei comenzi de BUY, trebuie sa calculezi cantitatea necesara de USDT pentru achizitionare
            total_usdt_needed = qty * price * (1 + fee_percentage)

            if available_qty < total_usdt_needed:
                print(f"Not enough USDT available. You need {total_usdt_needed:.8f} USDT, but you only have {available_qty:.8f} USDT.")
                # Ajusteaza cantitatea pe care o poti cumpara cu USDT disponibili
                qty = available_qty / (price * (1 + fee_percentage))
                print(f"Adjusting {order_type.upper()} order quantity to {qty:.8f} based on available USDT.")

        current_price = get_current_price(symbol)

        # Rotunjim cantitatea la 5 zecimale în jos
        #qty = math.floor(qty * 10**5) / 10**5  # Rotunjire în jos la 5 zecimale
        qty = round(qty, 4)
        qty = float(Decimal(qty).quantize(Decimal('0.0001'), rounding=ROUND_DOWN))  # Rotunjit la 5 zecimale
        if qty <= 0:
            print("Adjusted quantity is too small after rounding.")
            return None          
        if qty * current_price < 100:
            print(f"Value {qty * current_price} of {symbol} is too small to make sense to be traded :-) .by by!")
            return None
        if order_type.upper() == 'SELL':
            price = round(max(price, current_price), 0)
            print(f"Trying to place SELL order of {symbol} for quantity {qty:.8f} at price {price}")
            order = place_safe_order(order_type, symbol, price, qty);
        elif order_type.upper() == 'BUY':
            price = round(min(price, current_price), 0)
            print(f"Trying to place BUY order of {symbol} for quantity {qty:.8f} at price {price}")
            order = place_safe_order(order_type, symbol, price, qty);
        else:
            print(f"Invalid order type: {order_type}")
            return None

        return order

    except BinanceAPIException as e:
        print(f"Error placing {order_type.upper()} order: {e}")
        return None
    except Exception as e:
        print(f"place_order: A aparut o eroare: {e}")
        return None


def place_order_smart(order_type, symbol, price, qty, cancelorders=True, hours=5, pair=True):
    
    order_type = order_type.upper()
    validate_params(order_type, symbol, price, qty) 
    pair = False
    try:
        qty = round(qty, 5)
        cancel = False
        current_price = get_current_price(symbol)
        
        if order_type.upper() == 'BUY':
            open_SELL_orders = get_open_orders("SELL", symbol)
            # Anuleaza ordinele de vanzare existente la un pret mai mic decat pretul de cumparare dorit
            for order_id, order_details in open_SELL_orders.items():
                if order_details['price'] < price:
                    cancel = cancel_order(symbol, order_id)
                    if not cancel:
                        print(f"Fail cancel order {order_id} prep. for BUY order. We wanted becuse low price for SELL.")
            
            price = min(price, current_price)
            price = round(price * 0.999, 0)
            order = place_order("BUY", symbol, price=price, qty=qty, cancelorders=cancelorders, hours=hours)
            # appy pair
            if order and pair :            
                price = max(price * 1.11, current_price)
                price = round(price * 1.001, 0)
                place_order("SELL", symbol, price=price, qty=qty, cancelorders=cancelorders, hours=hours)
                
        elif order_type.upper() == 'SELL':
            open_BUY_orders = get_open_orders("BUY", symbol)
            # Anuleaza ordinele de cumparare existente la un pret mai mare decat pretul de vanzare dorit
            for order_id, order_details in open_BUY_orders.items():
                if order_details['price'] > price:
                    cancel = cancel_order(symbol, order_id)
                    if not cancel:
                        print(f"Fail cancel order {order_id} prep. for SELL order. We wanted becuse high price for BUY")
                   
            price = max(price, current_price)
            price = round(price * (1 + 0.001), 0)
            order = place_order("SELL", symbol, price=price, qty=qty, cancelorders=cancelorders, hours=hours)
            # appy pair
            if order and pair :
                price = min(price * (1 - 0.11), current_price)
                price = round(price * 0.999, 0)
                place_order("BUY", symbol, price=price, qty=qty, cancelorders=cancelorders, hours=hours)
        else:
            print("Tipul ordinului este invalid. Trebuie sa fie 'BUY' sau 'SELL'.")
            return None
        
        return order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de {order_type}: {e}")
        return None
        #return place_order(order_type, symbol, price, qty)
    except Exception as e:
        print(f"place_order_smart: A aparut o eroare: {e}")
        return None
        #return place_order(order_type, symbol, price, qty)
          
def cancel_order(symbol, order_id):
    try:
        if not order_id:
            return False
        client.cancel_order(symbol=symbol, orderId=order_id)
        print(f"Ordinul cu ID {order_id} a fost anulat.")
        return True
    except Exception as e:
        print(f"Eroare la anularea ordinului: {order_id} {e}")
        return False

def cancel_open_orders(order_type, symbol):
    
    order_type = order_type.upper()
    validate_params(order_type, symbol) 
    
    try:
        open_orders = get_open_orders(order_type, symbol)
        for order_id, order_details in open_orders.items():
            print(f"Cancelling order {order_id} for {symbol}")
            cancel_order(symbol, order_id)
    except Exception as e:
        print(f"Error cancelling orders for {symbol}: {e}")
        
def cancel_expired_orders(order_type, symbol, expire_time):
    
    order_type = order_type.upper()
    validate_params(order_type, symbol)
    
    open_orders = get_open_orders(order_type, symbol)

    #current_time = int(time.time() * 1000)  # Convert current time to milliseconds
    current_time = int(time.time())

    print(f"Available open orders {len(open_orders)}. Try cancel {order_type} orders type ... ")
    if len(open_orders) < 1:
        return
    count = 0   
    for order_id, order_details in open_orders.items():
        order_time = order_details.get('timestamp')

        if current_time - order_time > expire_time:
            cancel = cancel_order(symbol, order_id)
            if cancel:
                print(f"Cancelled {order_type} order with ID: {order_id} due to expiration.")
            else:
                 print(f"Needs cancel because expiration!")
            cancel +=1
    print(f"Cancelled {count} orders")
        

def check_order_filled(order_id):
    try:
        if not order_id:
            return False
        order = client.get_order(symbol=symbol, orderId=order_id)
        return order['status'] == 'FILLED'
    except Exception as e:
        print(f"Eroare la verificarea starii ordinului: {e}")
        return False


