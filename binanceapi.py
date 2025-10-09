
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
from binance.exceptions import BinanceAPIException
#from binance.streams import BinanceSocketManager
#from binance.streams import BinanceSocketManager
#print(dir(BinanceSocketManager))


####MYLIB
import utils as u
import symbols as sym
import config as cfg
import generateweb as web
import priceAnalysis as pa

from binanceclient import client

stop = False

import binance
print(binance.__version__)

def listen_to_binance(symbol):
    socket = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@ticker"
    
    # Functie asincrona pentru WebSocket
    async def connect():
        async with websockets.connect(socket) as websocket:
            while not stop:
                message = await websocket.recv()
                message = json.loads(message)
                process_message(symbol, message)

    # Rulam WebSocket-ul intr-un event loop propriu in acest thread
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(connect())

# Functie de gestionare a mesajului primit de la WebSocket
def process_message(symbol, message):
    global cprice
    symbol = message['s']  # Simbolul criptomonedei
    price = float(message['c'])  # Asigura-te ca price este un float
    cprice[symbol] = price
    #print(f"ASYNC {symbol} is {price:.2f}")

def start_websocket_thread(symbol):
    websocket_thread = threading.Thread(target=listen_to_binance, args=(symbol,))
    websocket_thread.daemon = True
    websocket_thread.start()
    return websocket_thread


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

cprice = {}
cprice_time = {}
cprice_refresh_int = {}
quantities = {}
      
def update_price(symbol):
    #global quantities
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        cprice[symbol] = float(ticker['price'])
        quantities[symbol] = 10000 / cprice[symbol]
    except Exception as e:
        print(f"update_price: A aparut o eroare neasteptata: {e}")
        
    cprice_time[symbol] = time.time()
    cprice_refresh_int[symbol] = 1
    return cprice[symbol]
    
    
for symbol in sym.symbols:
    update_price(symbol)
    # Start the WebSocket thread
    websocket_thread = start_websocket_thread(symbol)
    
quantities = {symbol: 1000 / cprice[symbol] for symbol in sym.symbols}

def get_current_price(symbol):
    global cprice
    global cprice_refresh_int
    try:     
        if (cprice_time[symbol] + cprice_refresh_int[symbol] <= time.time()) :
            update_price(symbol)
        return cprice[symbol]
    
    except BinanceAPIException as e:
        print(f"Eroare la obtinerea pretului curent de la Binance API: {e}")
        print(f"Folosesc pretul obtinut prin websocket, {symbol}: {cprice.get(symbol, 'N/A')}")
        return cprice.get(symbol, None)  # Returnam None daca simbolul nu exista
    
#    except Exception as e:
#        print(f"get_current_price: A aparut o eroare neasteptata: {e}")
#        print(f"Folosesc pretul obtinut prin websocket, {symbol}: {cprice.get(symbol, 'N/A')}")
#        return cprice.get(symbol, None)  # Returnam None daca simbolul nu exista

currenttime = time.time()       
def get_current_time():
        global currenttime
        currenttime = time.time()
        return currenttime


def split_symbol(symbol: str):
    # Split symbol in base and quote/cotare. TAOUSDC -> (TAO, USDC) Work for sym end in USDT/USDC.   
   if symbol.endswith("USDT"):
        return symbol[:-4], "USDT"
   elif symbol.endswith("USDC"):
        return symbol[:-4], "USDC"
   else:
        raise ValueError(f"Simbol necunoscut: {symbol}")


def get_free_balance(asset: str) -> float:
    try:
        #  Returneaza balanta libera pentru un asset din Binance.
        asset_info = client.get_asset_balance(asset=asset)
        return float(asset_info.get("free", 0))
    except Exception as e:
        print(f"get_free_balance: Eroare pentru {asset}: {e}")
        return 0.0


def get_asset_info(order_type, symbol, price):
    """
    Returnează cantitatea disponibilă exprimată mereu în asset-ul de bază (qty).
    - SELL: cantitatea de bază disponibilă (ex: BTC).
    - BUY: cât din baza se poate cumpăra cu balanța de cotare (ex: USDC / preț curent).
    """
    try:
        base, quote = split_symbol(symbol)

        if order_type.upper() == "SELL":
            return get_free_balance(base)

        elif order_type.upper() == "BUY":
            if not price:
                print(f"get_asset_info: price is invalid ({price}), returning 0 qty for {symbol}")
                return 0.0            
            free_quote = get_free_balance(quote)
            return free_quote / price

        return 0.0

    except Exception as e:
        print(f"get_asset_info: Error: {e}, order_type {order_type} and {symbol}")
        return 0.0


def apply_weight_limit(symbol, order_type, price, required_qty, available_qty):
    import binanceapi_allorders as apiorders
    try:
        # weight din permisiuni
        weight = pa.get_weight_for_cash_permission_at_quant_time(symbol, order_type)
        if weight is None:
            print("Weight is None, set it at default 0.03")
            weight = 0.03

        # 2. Obține cât s-a tranzacționat deja în ultimele 24h (în quote)
        stats = apiorders.get_total_traded_stats(symbol)
        traded_value = stats.get(order_type.upper(), {}).get('total_value', 0)

        # 3. Calculează valoarea totală tranzacționabilă (tranzacționată + disponibilă)
        total_value_reference = traded_value + available_qty * price
        # 4. Calculează plafonul maxim permis (în quote) pe baza weight
        max_trade_value = total_value_reference * weight
        #max_trade_value = available_qty * price * weight

        # 5. Cât mai pot tranzacționa în quote asset (USDC, USDT etc.)
        remaining_trade_value = max(0, max_trade_value - traded_value)

        # qty maxim în în cantitate/baza (BTC, TAO etc.)
        remaining_trade_qty = remaining_trade_value / price if price else 0

        # alegem cantitatea cea mai mică între ce vreau și cât am voie
        adjusted_qty = min(required_qty, remaining_trade_qty)

        print(f"apply_weight_limit → {order_type} {symbol}, "
              f"Available qty {available_qty:.8f}, "
              f"Weight {weight}, "
              f"Traded in 24h {traded_value:.2f} USDC, "
              f"Max trade allowed (24h): {max_trade_value:.2f} USDC, "
              f"Remaining: {remaining_trade_value:.2f} USDC, "
              f"Required qty: {required_qty:.8f}, "
              f"Final qty: {adjusted_qty:.8f}")


        return adjusted_qty

    except Exception as e:
        print(f"apply_weight_limit: Error: {e}, order_type {order_type} and {symbol}")
        return required_qty

def manage_quantity(order_type, symbol, required_qty, price_to_be_traded, cancelorders=False, hours=5):

    current_price = get_current_price(symbol)
                
    # 1. cat am efectiv disponibil
    available_qty = get_asset_info(order_type, symbol, current_price)

    # 2. aplicam limita de cash/weight
    required_qty = apply_weight_limit(symbol, order_type, current_price, required_qty, available_qty)


    if available_qty < required_qty:
        print(f"Not enough available {symbol}. Available: {available_qty:.8f}, Required: {required_qty:.8f}")

        freed_quantity = 0
        if cancelorders:
            freed_quantity = cancel_orders_old_or_outlier(
                order_type, symbol, required_qty, hours=hours, price_difference_percentage=0.15
            ) or 0

        available_qty += freed_quantity

        if available_qty < required_qty:
            print(f"Still not enough quantity. Adjusting order quantity to {available_qty:.8f}")

    return required_qty, available_qty

    

def cancel_orders_old_or_outlier(order_type, symbol, required_quantity, hours=5, price_difference_percentage=0.1):

    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, 1, required_quantity)
    
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
                price_diff_percentage = abs(float(order_info['price']) - current_price) / current_price * 100
                if price_diff_percentage >= price_difference_percentage * 100:  # Convertim 0.1 in 10%
                    cancel = True

            if cancel:
                cancel_order(symbol, order_id)
                available_qty += float(order_info['quantity'])
                print(f"New available quantity: {available_qty:.8f}")

            if available_qty >= required_quantity:
                break

    return available_qty




def get_open_orders(order_type, symbol):

    order_type = order_type.upper()
    sym.validate_params(order_type, symbol)
        
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
        
def place_BUY_order(symbol, price, qty):
    try:
        if not cfg.is_trade_enabled() :
            print(f"Trade is desabled!")
            return None
            
        price = round(min(price, get_current_price(symbol)), 2)
        qty = round(qty, 4)    
        BUY_order = client.order_limit_buy(
            symbol=symbol,
            quantity=qty,
            price=str(price)
        )
        
        if BUY_order:
            print(f"BUY order placed successfully: {BUY_order['orderId']}")
        else :
            print(f"Eroare la plasarea ordinului de BUY")
        
        return BUY_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de cumparare: {e}")
        return None

def place_SELL_order(symbol, price, qty):
    try:
        if not cfg.is_trade_enabled() :
            print(f"Trade is disabled!")
            return None
            
        price = round(max(price, get_current_price(symbol)), 2)
        qty = round(qty, 4)    
        SELL_order = client.order_limit_sell(
            symbol=symbol,
            quantity=qty,
            price=str(price)
        )
        
        if SELL_order:
            print(f"SELL order placed successfully: {SELL_order['orderId']}")
        else :
            print(f"Eroare la plasarea ordinului de SELL")
        
        return SELL_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de vanzare: {e}")
        return None


def place_SELL_BUY_order(order_type, symbol, price, qty) :
 
    order = None
    if order_type == "BUY":
        order = client.order_limit_buy(
            symbol=symbol,
            quantity=qty,
            price=str(price)
        )
    elif order_type == "SELL":
        order = client.order_limit_sell(
            symbol=symbol,
            quantity=qty,
            price=str(price)
        )
    
    if order:
        print(f"{order_type} order placed successfully: {order['orderId']}")
    else :
        print(f"Eroare la plasarea ordinului de {order_type}, pret {price:.2f}")
    return order

def place_BUY_order_at_market(symbol, qty):
    try:
        if not cfg.is_trade_enabled():
            print(f"Trade este dezactivat!")
            return None
            
        qty = round(qty, 4)  # Rotunjim cantitatea la 4 zecimale
        BUY_order = client.order_market_buy(
            symbol=symbol,
            quantity=qty
        )
        
        if BUY_order:
            print(f"BUY order de market executat cu succes: {BUY_order['orderId']}")
        else:
            print(f"Eroare la plasarea ordinului de BUY de market")
        
        return BUY_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de market de cumparare: {e}")
        return None


def place_SELL_order_at_market(symbol, qty):
    try:
        if not cfg.is_trade_enabled():
            print(f"Trade este dezactivat!")
            return None

        qty = round(qty, 4)  # Rotunjim cantitatea la 4 zecimale
        SELL_order = client.order_market_sell(
            symbol=symbol,
            quantity=qty
        )
        
        if SELL_order:
            print(f"SELL order de market executat cu succes: {SELL_order['orderId']}")
        else:
            print(f"Eroare la plasarea ordinului de SELL de market")
        
        return SELL_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de market de vanzare: {e}")
        return None


def if_place_safe_order(order_type, symbol, price, qty, time_back_in_seconds, max_daily_trades=10, profit_percentage=0.01):
    #import binanceapi_trades as apitrades
    import binanceapi_allorders as apiorders
    

    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, price, qty)
    minutes_ago = time.time() - 3 * 60  # With 3 min in urma , time.time() => seconds
    #apitrades.compare_trade_sources(symbol, order_type=order_type, max_age_seconds=time_back_in_seconds, limit=1000)
        
    try:
        
        current_price = get_current_price(symbol)
        
        if order_type == "BUY":
            price = round(min(price, current_price), 0)
        else:  # pentru "SELL"
            price = round(max(price, current_price), 0)

        qty = round(qty, 4)

        opposite_order_type = "SELL" if order_type == "BUY" else "BUY"
        backdays = math.ceil(time_back_in_seconds / 86400)
        #all_trades = apitrades.get_my_trades(order_type, symbol, backdays=backdays, limit=1000)
        #all_trades = apitrades.get_trade_orders(order_type, symbol, max_age_seconds=time_back_in_seconds)
        all_trades = apiorders.get_trade_orders(order_type, symbol, max_age_seconds=time_back_in_seconds)
        
        #all_trades = apitrades.get_trade_orders_24(order_type, symbol, days_back=backdays)
        #oposite_trades = apitrades.get_my_trades(opposite_order_type, symbol, backdays=backdays, limit=1000) ## curent date
        #oposite_trades = apitrades.get_trade_orders(opposite_order_type, symbol, max_age_seconds=time_back_in_seconds) ## curent date
        oposite_trades = apiorders.get_trade_orders(opposite_order_type, symbol, max_age_seconds=time_back_in_seconds) ## curent date
        if len(all_trades)/backdays > max_daily_trades:
            print(f"Am {len(oposite_trades)} trades. Limita zilnica este de {max_daily_trades} pentru '{order_type}'.")
            return False
        for trade in all_trades:
            trade_time = trade['timestamp'] / 1000  # 'time' este in milisecunde
            if trade_time > minutes_ago:
                print(f"Are recent transactions in last 3 minutes")
                return False
                
        #print("Tranzactii anterioare:")
        #for trade in oposite_trades:
            #print(apitrades.format_trade(trade, time_limit))
            
        print(f"Am {len(oposite_trades)} trades de tip {opposite_order_type} pentru {backdays} zile. ")
        
        time_limit = float(time.time() * 1000) - (time_back_in_seconds * 1000)  # in milisecunde
        # Filtram tranzactiile opuse care au avut loc in intervalul specificat
        recent_opposite_trades = [trade for trade in oposite_trades if float(trade['timestamp']) >= float(time_limit)]
        print(f"Ma raportrez doar la cele care sunt cu {time_back_in_seconds} sec. back , in numar de '{len(recent_opposite_trades)}'")
        for trade in recent_opposite_trades:
            readable = datetime.fromtimestamp(trade['timestamp'] / 1000)
            print(f"[CHECK] {readable} - price: {trade['price']} - included: {float(trade['timestamp']) >= time_limit}")
        
        #max_SELL_price = max(float(trade['quoteQty']) / float(trade['qty']) for trade in recent_opposite_trades)
        if recent_opposite_trades:
            if order_type == "BUY":
                last_sell_price = min(float(trade['price']) for trade in recent_opposite_trades)
                diff_percent = u.value_diff_to_percent(last_sell_price, current_price)
                print(f"[DEBUG] Last SELL Price: {last_sell_price}")
            else:  # pentru `sell`
                last_buy_price = max(float(trade['price']) for trade in recent_opposite_trades)
                diff_percent = u.value_diff_to_percent(current_price, last_buy_price)           
                print(f"[DEBUG] Last Buy Price: {last_buy_price}")                
                
            print(f"[DEBUG] Difference Percent: {diff_percent:.2f}%")
            print(f"[DEBUG] Required Percentage Diff: {profit_percentage}%")   
            
            if diff_percent < profit_percentage:
                    print(f"Diferenta procentuala ({diff_percent:.2f}%) este sub pragul necesar"
                        f" de {profit_percentage}%. Ordinul de {order_type} nu a fost plasat.")
                    return False
        return True

    except BinanceAPIException as e:
        print(f"Eroare la verificare if place safe order {order_type}: {e}")
        return False


def place_order(order_type, symbol, price, qty, force=False, cancelorders=False, hours=5, fee_percentage=0.001):
    order = __place_order(order_type, symbol, price, qty, force, cancelorders, hours, fee_percentage)
    
    if order is None:
        if force and order_type == 'BUY':
            print("ULTRA DUBIOS!!!!")
            #order = place_SELL_order_at_market(symbol.forcesellsymbol[symbol], symbol.quantities[symbol]) 
            #time.sleep(0.2)
            #order = __place_order(order_type, symbol, price, qty, force, cancelorders, hours, fee_percentage)
            
    return order
         

from decimal import Decimal, ROUND_DOWN
def __place_order(order_type, symbol, price, qty, force=False, cancelorders=False, hours=5, fee_percentage=0.001):
    
    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, price, qty)  
        
    try:
        print(f"Order Request {order_type} {symbol} qty {qty}, Price {price}")
        qty, available_qty = manage_quantity(order_type, symbol, qty, price_to_be_traded=price, cancelorders=cancelorders, hours=hours)

        if available_qty <= 0:
            print(f"No sufficient quantity available to place the {order_type} order.")
            return None
                
        if order_type == 'SELL':      
            print(f"available_qty {available_qty:.8f} versus requested {qty:.8f}")
            
            adjusted_qty = qty * (1 + fee_percentage)

            if available_qty < adjusted_qty:
                print(f"Adjusting {order_type} order quantity from {qty:.8f} to {available_qty / (1 + fee_percentage):.8f} to cover fees")
                qty = available_qty / (1 + fee_percentage)

        elif order_type == 'BUY':
            # in cazul unei comenzi de BUY, trebuie sa calculezi cantitatea necesara de USDT pentru achizitionare
            total_usdt_needed = qty * price * (1 + fee_percentage)

            if available_qty * price < total_usdt_needed:
                print(f"Not enough {symbol} available for {order_type}. You need {total_usdt_needed:.8f}, but you only have {available_qty:.8f} {symbol}.")
                # Ajusteaza cantitatea pe care o poti cumpara cu USDT disponibili
                qty = available_qty / (price * (1 + fee_percentage))
                print(f"Adjusting {order_type} order quantity to {qty:.8f} based on available {symbol}.")

        # Rotunjim cantitatea la 5 zecimale in jos
        #qty = math.floor(qty * 10**5) / 10**5  # Rotunjire in jos la 5 zecimale
        qty = round(qty, 4)
        qty = float(Decimal(qty).quantize(Decimal('0.0001'), rounding=ROUND_DOWN))  # Rotunjit la 5 zecimale

        current_price = get_current_price(symbol)
        if qty * current_price < 100:
            print(f"Value {qty * current_price} of {symbol} is too small to make sense to be traded :-) .by by!")
            return None
        
        print(f"Trying to place {order_type} order of {symbol} for quantity {qty:.8f} at {'market price' if force else f'price {price}'}")

        if order_type == 'SELL':
            price = round(max(price, current_price), 0)
            if force:
                 order = place_SELL_order_at_market(symbol, qty);
            else:
                order = place_SELL_order(symbol, price, qty);
        elif order_type == 'BUY':
            price = round(min(price, current_price), 0)
            if force:
                 order = place_BUY_order_at_market(symbol, qty);
            else:
                order = place_BUY_order(symbol, price, qty);
        else:
            print(f"Invalid order type: {order_type}")
            return None

        return order

    except BinanceAPIException as e:
        print(f"Error placing {order_type.upper()} order: {e}")
        return None
    #except Exception as e:
    #    print(f"place_order: A aparut o eroare: {e}")
    #    return None


def place_safe_order(order_type, symbol, price, qty, safeback_seconds=48*3600+60, force=False, cancelorders=False, hours=5, fee_percentage=0.001):
    
    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, price, qty)
    
    if not if_place_safe_order(order_type, symbol, price, qty, time_back_in_seconds=safeback_seconds, max_daily_trades=15, profit_percentage = 0.15) :
        return None
      
    return place_order(order_type, symbol, price, qty, force=force, cancelorders=cancelorders, hours=hours, fee_percentage=fee_percentage)    
    

def place_order_smart(order_type, symbol, price, qty, safeback_seconds=48*3600+60, force=False, cancelorders=True, hours=5, pair=True):
    
    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, price, qty) 
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
            order = place_safe_order("BUY", symbol, price=price, qty=qty, 
                safeback_seconds=safeback_seconds, force=force, cancelorders=cancelorders, hours=hours)
            # appy pair
            if order and pair :            
                price = max(price * 1.11, current_price)
                price = round(price * 1.001, 0)
                place_safe_order("SELL", symbol, price=price, qty=qty,
                    safeback_seconds=safeback_seconds, force=force, cancelorders=cancelorders, hours=hours)
                
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
            order = place_safe_order("SELL", symbol, price=price, qty=qty,
                safeback_seconds=safeback_seconds, force=force, cancelorders=cancelorders, hours=hours)
            # appy pair
            if order and pair :
                price = min(price * (1 - 0.11), current_price)
                price = round(price * 0.999, 0)
                place_safe_order("BUY", symbol, price=price, qty=qty,
                    safeback_seconds=safeback_seconds, force=force, cancelorders=cancelorders, hours=hours)
        else:
            print("Tipul ordinului este invalid. Trebuie sa fie 'BUY' sau 'SELL'.")
            return None
        
        return order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de {order_type}: {e}")
        return None
        #return place_order(order_type, symbol, price, qty)
    #except Exception as e:
    #    print(f"place_order_smart: A aparut o eroare: {e}")
    #    return None
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
    sym.validate_params(order_type, symbol) 
    
    try:
        open_orders = get_open_orders(order_type, symbol)
        for order_id, order_details in open_orders.items():
            print(f"Cancelling order {order_id} for {symbol}")
            cancel_order(symbol, order_id)
    except Exception as e:
        print(f"Error cancelling orders for {symbol}: {e}")
        

def cancel_expired_orders(order_type, symbol, expire_time):
    
    order_type = order_type.upper()
    sym.validate_params(order_type, symbol)
    
    open_orders = get_open_orders(order_type, symbol)

    #current_time = int(time.time() * 1000)  # Convert current time to milliseconds
    current_time = int(time.time())
  
    if len(open_orders) < 1:
        return
    print(f"Available open orders {len(open_orders)}. Try cancel {order_type} orders type ... ")
      
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
        

def cancel_recent_orders(order_type, symbol, max_age_seconds):

    order_type = order_type.upper()
    sym.validate_params(order_type, symbol)
    
    open_orders = get_open_orders(order_type, symbol)
    current_time = int(time.time())  # Current time in seconds

    if len(open_orders) < 1:
        return
    print(f"Available open orders {len(open_orders)}. Checking for recent {order_type} orders to cancel... ")
   
    count = 0
    for order_id, order_details in open_orders.items():
        order_time = order_details.get('timestamp')  # Assuming timestamp is in seconds
        if current_time - order_time <= max_age_seconds:  # Order is recent
            cancel = cancel_order(symbol, order_id)
            if cancel:
                print(f"Cancelled {order_type} order with ID: {order_id} (recent order).")
                count += 1
            else:
                print(f"Failed to cancel {order_type} order with ID: {order_id}. Needs cancel because recent order.")
    
    print(f"Cancelled {count} recent orders.")


def check_order_filled(order_id, symbol):
    try:
        if not order_id:
            return False
        order = client.get_order(symbol=symbol, orderId=order_id)
        return order['status'] == 'FILLED'
    except Exception as e:
        print(f"Eroare la verificarea starii ordinului: {e}")
        return False



def check_order_filled_by_time(order_type, symbol, time_back_in_seconds, pret_min=None, pret_max=None):
    #import binanceapi_trades as apitrades
    import binanceapi_allorders as apiorders

    backdays = math.ceil(time_back_in_seconds / 86400)
    #trades = apitrades.get_my_trades(order_type, symbol, backdays=backdays, limit=1000)
    #trades = apitrades.get_trade_orders(order_type, symbol, max_age_seconds=time_back_in_seconds)
    trades = apiorders.get_trade_orders(order_type, symbol, max_age_seconds=time_back_in_seconds)
    time_limit = int(time.time() * 1000) - (time_back_in_seconds * 1000)  # in milisecunde

                
    # Filtram tranzactiile in functie de timp si optional in functie de pret total
    tranzactii_recente = [
        trade for trade in trades
        if trade['timestamp'] >= time_limit and
           (pret_min is None or float(trade['price']) * float(trade['qty']) >= pret_min) and
           (pret_max is None or float(trade['price']) * float(trade['qty']) <= pret_max)
    ]

    if tranzactii_recente:
        # Gasim cea mai recenta tranzactie (dupa timp)
        tranzactia_recenta = max(tranzactii_recente, key=lambda trade: trade['timestamp'])
        return float(tranzactia_recenta['price'])

    print(f"[DEBUG] Nicio tranzactie recenta pentru simbolul {symbol}. in ultimele {time_back_in_seconds} secunde ")
    return None
