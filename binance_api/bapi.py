
import time
import datetime
import math
import sys
from datetime import datetime, timedelta

import signal

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

from .bapi_client import client

import binance
print(binance.__version__)

from . import bapi_ws

# Function to handle Ctrl+C and shut down the WebSocket properly
def signal_handler(sig, frame):
    global websocket_thread, stop
    print("Shutting down...")
    bapi_ws.bapi_ws_manager.stop()
    
    # Apelare handler implicit pentru SIGINT
    #signal.default_int_handler(sig, frame)
    sys.exit(0)

    
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

def update_price(symbol):
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        cprice[symbol] = float(ticker['price'])
    except Exception as e:
        print(f"update_price: A aparut o eroare neasteptata: {e}")

    cprice_time[symbol] = time.time()
    cprice_refresh_int[symbol] = 11
    return cprice[symbol]

for symbol in sym.symbols:
    update_price(symbol)

def get_current_price(symbol):
    global cprice
    global cprice_refresh_int
    try:     
        if (cprice_time[symbol] + cprice_refresh_int[symbol] <= time.time()) :
            update_price(symbol)
        _cprice  = cprice.get(symbol, None)
        if _cprice is None:
            print(f"get_current_price: Pretul pentru {symbol} nu este disponibil. Returning None.")
        return  _cprice
    
    except BinanceAPIException as e:
        print(f"Eroare la obtinerea pretului curent de la Binance API: {e}")
        print(f"Folosesc pretul obtinut prin websocket, {symbol}: {cprice.get(symbol, 'N/A')}")
        _cprice = cprice.get(symbol, None)  # Returnam None daca simbolul nu exista
        if _cprice is None:
            print(f"get_current_price: Pretul pentru {symbol} nu este disponibil prin WebSocket. Returning None.")
        return _cprice
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


def get_account_assets_balances():
    try:
        account = client.get_account()
        balances = account.get("balances", [])
        result = []
        for balance in balances:
            free_qty = float(balance.get("free", 0.0))
            locked_qty = float(balance.get("locked", 0.0))
            total_qty = free_qty + locked_qty
            if total_qty <= 0:
                if balance.get('asset') in sym.symbols:
                    print(f"get_account_assets_balances: Skip {balance.get('asset')} because total_qty is 0")
                    continue
            result.append(
                {
                    "asset": balance.get("asset"),
                    "free": free_qty,
                    "locked": locked_qty,
                    "total": total_qty,
                }
            )
        return result
    except Exception as e:
        print(f"get_account_assets_balances: Eroare la citirea balantelor: {e}")
        return []


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
            count +=1
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
    #import bapi_trades as apitrades
    from . import bapi_allorders as apiorders

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


# ---------------- Portfolio value query API ----------------
import threading

ASSET_VALUE_CACHE_TTL_SECONDS = 120

_asset_value_cache = {"value": None, "timestamp": 0.0}
_asset_value_cache_lock = threading.Lock()


def _get_symbol_price_safe(symbol):
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    except Exception:
        return None


def _convert_to_usdt(asset, amount):
    if amount <= 0:
        return 0.0
    if asset == "USDT":
        return amount
    if asset == "USDC":
        usdcusdt = _get_symbol_price_safe("USDCUSDT")
        return amount * usdcusdt if usdcusdt else amount

    direct_pairs = [f"{asset}USDT", f"{asset}USDC", f"{asset}BUSD"]
    for pair in direct_pairs:
        price = _get_symbol_price_safe(pair)
        if price:
            if pair.endswith("USDT"):
                return amount * price
            if pair.endswith("USDC"):
                usdcusdt = _get_symbol_price_safe("USDCUSDT") or 1.0
                return amount * price * usdcusdt
            if pair.endswith("BUSD"):
                busdusdt = _get_symbol_price_safe("BUSDUSDT") or 1.0
                return amount * price * busdusdt

    return 0.0


def get_total_assets_value_usdt(use_cache=True, cache_ttl_seconds=ASSET_VALUE_CACHE_TTL_SECONDS):
    now = time.time()
    if use_cache:
        with _asset_value_cache_lock:
            if (
                _asset_value_cache["value"] is not None
                and (now - _asset_value_cache["timestamp"]) < cache_ttl_seconds
            ):
                return _asset_value_cache["value"]

    total_value = 0.0
    try:
        for balance in get_account_assets_balances():
            total_value += _convert_to_usdt(balance["asset"], balance["total"])
    except Exception as e:
        print(f"Error: get_total_assets_value_usdt: Error calculating portfolio value: {e}")
        return None

    with _asset_value_cache_lock:
        if total_value > 0:
            _asset_value_cache["value"] = total_value
            _asset_value_cache["timestamp"] = now
        else:
            print(f"Error: get_total_assets_value_usdt: Total value can't be calculated")
            return None

    return _asset_value_cache["value"]
