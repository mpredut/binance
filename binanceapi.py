
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
        print(f"folosesc pretul obtinut prin websocket, BTC:{binancecurrentprice}")
        return binancecurrentprice

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
        
        if order_type.lower() == 'buy':
            open_sell_orders = get_open_orders("sell", symbol)
            # Anulează ordinele de vânzare existente la un preț mai mic decât prețul de cumpărare dorit
            for order_id, order_details in open_sell_orders.items():
                if order_details['price'] < price:
                    cancel = cancel_order(order_id)
                    if not cancel:
                        print(f"Fail cancel order {order_id} prep. for buy order")
            
            price = round(min(price, get_current_price(symbol)), 0)
            order = client.order_limit_buy(
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
                   
            price = round(max(price, get_current_price(symbol)), 0)
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


################
def get_my_trades_24(symbol, days_ago, order_type=None, limit=1000):
    all_trades = []
    try:
        current_time = int(time.time() * 1000)
        
        # Calculăm start_time și end_time pentru ziua specificată în urmă
        end_time = current_time - days_ago * 24 * 60 * 60 * 1000
        start_time = end_time - 24 * 60 * 60 * 1000  # Cu 24 de ore în urmă de la end_time

        # Apelăm API-ul pentru tranzacții în intervalul specificat
        while start_time < end_time:
            trades = client.get_my_trades(symbol=symbol, limit=limit, startTime=start_time, endTime=end_time)

            if not trades:
                break

            print("GASIT")
            
            # Dacă order_type este specificat, filtrăm tranzacțiile
            if order_type == "buy":
                filtered_trades = [trade for trade in trades if trade['isBuyer']]
            elif order_type == "sell":
                filtered_trades = [trade for trade in trades if not trade['isBuyer']]
            else:
                # Dacă nu e specificat order_type, nu aplicăm niciun filtru
                filtered_trades = trades

            all_trades.extend(filtered_trades)
            
            if len(trades) < limit:
                break

            # Ajustăm `start_time` la timpul celei mai noi tranzacții pentru a continua
            start_time = trades[-1]['time'] + 1  # Ne mutăm înainte cu 1 ms pentru a evita duplicatele
            
        return all_trades

    except Exception as e:
        print(f"An error occurred: {e}")
        return []


# symbol = 'BTCUSDT'
# limit = 2

# for days_ago in range (0,20):
    # print(f"Testing get_my_trades_24 for {symbol} on day {days_ago}...")
    # trades = get_my_trades_24(symbol, days_ago, limit)
    # if trades:
        # print(f"Found {len(trades)} trades for day {days_ago}.")
        # for trade in trades[:5]:  # Afișează primele 5 tranzacții
            # print(trade)
    # else:
        # print(f"No trades found for day {days_ago}.")


def get_my_trades(order_type, symbol, backdays=3, limit=1000):
    all_trades = []
    
    try:
        for days_ago in range(backdays):
            print(f"Fetching trades for day {days_ago}...")
            trades = get_my_trades_24(symbol, days_ago, limit)
            
            if not trades:
                print(f"No trades found for day {days_ago}.")
                continue
            
            #filtered_trades = [trade for trade in trades if trade['isBuyer'] == (order_type == "buy")]
            if order_type == "buy":
                filtered_trades = [trade for trade in trades if trade['isBuyer']]
            elif order_type == "sell":
                filtered_trades = [trade for trade in trades if not trade['isBuyer']]
            else:
                filtered_trades = trades
                
            all_trades.extend(filtered_trades)

        return all_trades

    except Exception as e:
        print(f"An error occurred: {e}")
        return []
        
        
def get_my_trades_simple(order_type, symbol, backdays=3, limit=1000):
    all_trades = []
    try:
        current_time = int(time.time() * 1000) 

        max_interval = 24 * 60 * 60 * 1000

        end_time = current_time

        for day in range(backdays):
            # Calculăm start_time pentru ziua curentă în intervalul de 24 de ore
            start_time = end_time - max_interval
            
            trades = client.get_my_trades(symbol=symbol, limit=limit, startTime=start_time, endTime=end_time)

            if trades:
                #filtered_trades = [trade for trade in trades if trade['isBuyer'] == (order_type == "buy")]
                if order_type == "buy":
                    filtered_trades = [trade for trade in trades if trade['isBuyer']]
                elif order_type == "sell":
                    filtered_trades = [trade for trade in trades if not trade['isBuyer']]
                else:
                    filtered_trades = trades
                
                all_trades.extend(filtered_trades)
            
            # Actualizăm end_time pentru ziua anterioară (înainte de această perioadă de 24 de ore)
            end_time = start_time

        return all_trades

    except Exception as e:
        print(f"An error occurred: {e}")
        return []



def test_get_my_trades():
    symbol = 'BTCUSDT'
    backdays = 30
    limit = 1000

    # Testare fără filtrare (fără 'buy' sau 'sell')
    print("Testing get_my_trades with pagination (no order_type)...")
    trades_pagination = get_my_trades(None, symbol, backdays=backdays, limit=limit)

    print("Testing get_my_trades_simple without pagination (no order_type)...")
    trades_simple = get_my_trades_simple(None, symbol, backdays=backdays, limit=limit)

    # Testare pentru 'buy'
    print("Testing get_my_trades with pagination (buy orders)...")
    trades_pagination_buy = get_my_trades("buy", symbol, backdays=backdays, limit=limit)

    print("Testing get_my_trades_simple without pagination (buy orders)...")
    trades_simple_buy = get_my_trades_simple("buy", symbol, backdays=backdays, limit=limit)

    # Testare pentru 'sell'
    print("Testing get_my_trades with pagination (sell orders)...")
    trades_pagination_sell = get_my_trades("sell", symbol, backdays=backdays, limit=limit)

    print("Testing get_my_trades_simple without pagination (sell orders)...")
    trades_simple_sell = get_my_trades_simple("sell", symbol, backdays=backdays, limit=limit)

    # Comparăm rezultatele pentru tranzacțiile nefiltrate
    print("\nComparing unfiltered results...")
    if trades_pagination == trades_simple:
        print("Both functions returned the same results for unfiltered trades.")
    else:
        print("The functions returned different results for unfiltered trades.")
        print(f"Trades with pagination: {len(trades_pagination)}")
        print(f"Trades without pagination: {len(trades_simple)}")
        print("Differences found in content for unfiltered trades.")
        for i, (trade_p, trade_s) in enumerate(zip(trades_pagination, trades_simple)):
            if trade_p != trade_s:
                print(f"Difference at trade {i}:")
                print(f"Pagination trade: {trade_p}")
                print(f"Simple trade: {trade_s}")

    # Comparăm rezultatele pentru tranzacțiile de tip 'buy'
    print("\nComparing buy order results...")
    if trades_pagination_buy == trades_simple_buy:
        print("Both functions returned the same results for buy orders.")
    else:
        print("The functions returned different results for buy orders.")
        print(f"Buy trades with pagination: {len(trades_pagination_buy)}")
        print(f"Buy trades without pagination: {len(trades_simple_buy)}")
        print("Differences found in content for buy trades.")
        for i, (trade_p, trade_s) in enumerate(zip(trades_pagination_buy, trades_simple_buy)):
            if trade_p != trade_s:
                print(f"Difference at trade {i}:")
                print(f"Pagination trade: {trade_p}")
                print(f"Simple trade: {trade_s}")

    # Comparăm rezultatele pentru tranzacțiile de tip 'sell'
    print("\nComparing sell order results...")
    if trades_pagination_sell == trades_simple_sell:
        print("Both functions returned the same results for sell orders.")
    else:
        print("The functions returned different results for sell orders.")
        print(f"Sell trades with pagination: {len(trades_pagination_sell)}")
        print(f"Sell trades without pagination: {len(trades_simple_sell)}")
        print("Differences found in content for sell trades.")
        for i, (trade_p, trade_s) in enumerate(zip(trades_pagination_sell, trades_simple_sell)):
            if trade_p != trade_s:
                print(f"Difference at trade {i}:")
                print(f"Pagination trade: {trade_p}")
                print(f"Simple trade: {trade_s}")

    # Afișăm câteva exemple pentru fiecare caz
    print("\nFirst few trades for unfiltered pagination:")
    for trade in trades_pagination[:5]:
        print(trade)

    print("\nFirst few buy trades with pagination:")
    for trade in trades_pagination_buy[:5]:
        print(trade)

    print("\nFirst few sell trades with pagination:")
    for trade in trades_pagination_sell[:5]:
        print(trade)

# Apelăm funcția de testare
#test_get_my_trades()

import os
# Funcția care salvează tranzacțiile noi în fișier (completare dacă există deja)
def save_trades_to_file(order_type, symbol, filename, limit=1000):
    all_trades = []

    # Verificăm dacă fișierul există deja
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            try:
                existing_trades = json.load(f)
                print(f"Loaded {len(existing_trades)} existing trades from {filename}.")
            except json.JSONDecodeError:
                existing_trades = []
    else:
        existing_trades = []

    # Dacă există deja tranzacții, găsim cea mai recentă tranzacție salvată
    if existing_trades:
        most_recent_trade_time = max(trade['time'] for trade in existing_trades)
        print(f"Most recent trade time from file: {most_recent_trade_time}")

        # Calculăm câte zile au trecut de la most_recent_trade_time până la acum
        current_time = int(time.time() * 1000)
        time_diff_ms = current_time - most_recent_trade_time
        backdays = time_diff_ms // (24 * 60 * 60 * 1000) + 1  # Câte zile au trecut de la ultima tranzacție
    else:
        most_recent_trade_time = 0  # Dacă nu există tranzacții, începem de la 0
        backdays = 60  # Adăugăm tranzacții pentru ultimele 60 de zile dacă fișierul e gol

    print(f"Fetching trades from the last {backdays} days.")

    # Apelăm funcția pentru a obține tranzacțiile recente doar din perioada lipsă
    new_trades = get_my_trades_simple(order_type, symbol, backdays=backdays, limit=limit)

    # Filtrăm doar tranzacțiile care sunt mai recente decât cea mai recentă tranzacție din fișier
    new_trades = [trade for trade in new_trades if trade['time'] > most_recent_trade_time]

    if new_trades:
        print(f"Found {len(new_trades)} new trades.")
        
        # Adăugăm doar tranzacțiile noi la cele existente
        all_trades = existing_trades + new_trades
        all_trades = sorted(all_trades, key=lambda x: x['time'])  # Sortăm după timp

        # Salvăm doar tranzacțiile noi la fișier
        with open(filename, 'w') as f:
            json.dump(all_trades, f)

        print(f"Updated file with {len(all_trades)} total trades.")
    else:
        print("No new trades found to save.")

#save_trades_to_file(None, "BTCUSDT", "trades_BTCUSDT.json", limit=1000)

# Exemplu de utilizare pentru a obține tranzacțiile de tip buy
#buy_trades = get_filled_trades('buy', 'BTCUSDT', backdays=7*2, limit=2)
#sell_trades = get_filled_trades('sell', 'BTCUSDT', backdays=7*2, limit=4)

  
  
  
  #######
#start_time = int((datetime.datetime.now() - datetime.timedelta(days=backdays)).timestamp() * 1000)
def get_filled_orders(order_type, symbol, backdays=3):
    try:
        end_time = int(time.time() * 1000)  # milisecunde
        
        interval_hours = 1
        interval_ms = interval_hours * 60 * 60 * 1000  # interval_hours de ore în milisecunde
        start_time = end_time - backdays * 24 * 60 * 60 * 1000
       
        all_filtered_orders = []

        # Parcurgem intervale de 24 de ore și colectăm ordinele
        while start_time < end_time:
            current_end_time = min(start_time + interval_ms, end_time)
            orders = client.get_all_orders(symbol=symbol, startTime=start_time, endTime=current_end_time, limit=1000)
            print(f"orders : {len(orders)}")
            
            # Filtrăm ordinele complet executate și pe cele care corespund tipului de ordin specificat
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



start_time = int(time.time() * 1000) - 1 * 24 * 60 * 60 * 1000  # Cu 3 zile în urmă
end_time = int(time.time() * 1000)  # Momentul curent
order_type = "buy"  # sau "sell", sau None pentru ambele tipuri

#all_filtered_orders = get_all_orders_in_time_range(order_type, symbol, start_time, end_time)
#all_filtered_orders = get_filled_orders(order_type, symbol)
#print(f"Filtered filled orders of type '{order_type}': {len(all_filtered_orders)}")
#print("First few filled orders for inspection:")
#for filled_order in all_filtered_orders[:5]:  # Afișează primele 5 ordine complet executate
    #print(filled_order)
        

def get_recent_filled_orders(order_type, max_age_seconds):

    all_filled_orders = get_filled_orders(order_type, symbol)
    recent_filled_orders = []
    current_time = time.time()
    if(len(all_filled_orders) < 1) :
        return []

    print(len(all_filled_orders))
    order_time = current_time
    for order in all_filled_orders:
        order_time = order['timestamp']
        if current_time - order_time <= max_age_seconds:
            recent_filled_orders.append(order)

    # Sort the recent_filled_orders by price in ascending order
    recent_filled_orders.sort(key=lambda x: x['price'])

    return recent_filled_orders


def get_close_buy_orders_without_sell(api, max_age_seconds, profit_percentage):
    close_buy_orders = api.get_recent_filled_orders('buy', max_age_seconds)
    close_sell_orders = api.get_recent_filled_orders('sell', max_age_seconds)
    
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

