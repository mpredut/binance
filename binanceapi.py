
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

def get_filled_orders(order_type, symbol):

    all_orders = client.get_all_orders(symbol=symbol)
    # Filtrăm ordinele complet executate și pe cele care corespund tipului de ordin specificat
    filled_orders = [
        {
            'orderId': order['orderId'],
            'price': float(order['price']),
            'quantity': float(order['origQty']),
            'timestamp': order['time'] / 1000,  # Timpul în secunde
            'side': order['side'].lower()
        }
        for order in all_orders if order['status'] == 'FILLED' and order['side'].lower() == order_type.lower()
    ]
    
    return filled_orders
    
def debug_get_filled_orders(order_type, symbol):
    print(f"Fetching all orders for symbol: {symbol}")
    all_orders = client.get_all_orders(symbol=symbol)
    
    print(f"Total orders fetched: {len(all_orders)}")
    
    filtered_orders = []
    for order in all_orders:
        # Afișăm fiecare ordin pentru a verifica structura și valorile cheilor relevante
        print(f"Order ID: {order['orderId']}, Status: {order['status']}, Side: {order['side']}, Type: {order['type']}, Time: {order['time']}")
        
        # Verifică dacă ordinul este FILLED și are tipul corect (buy sau sell)
        if order['status'] == 'FILLED' and order['side'].lower() == order_type.lower():
            filtered_order = {
                'orderId': order['orderId'],
                'price': float(order['price']),
                'quantity': float(order['origQty']),
                'timestamp': order['time'] / 1000,  # Timpul în secunde
                'side': order['side'].lower()
            }
            filtered_orders.append(filtered_order)
    
    print(f"Filtered filled orders of type '{order_type}': {len(filtered_orders)}")
    print("First few filled orders for inspection:")
    for filled_order in filtered_orders[:5]:  # Afișează primele 5 ordine complet executate
        print(filled_order)
    
    return filtered_orders

def get_old_orders(symbol, limit=1000):
    start_date = datetime.datetime(2023, 6, 1)  # 1 iunie 2023
    end_date = datetime.datetime(2023, 6, 30, 23, 59, 59)  # 30 iunie 2023, ora 23:59:59

    # Obținem timestamp-urile în milisecunde
    start_time = int(start_date.timestamp() * 1000)
    end_time = int(end_date.timestamp() * 1000)
    
    delta = datetime.timedelta(days=1)
    end_time = int((start_date + delta).timestamp() * 1000)

    all_orders = []
    
    while True:
        orders = client.get_all_orders(symbol=symbol, startTime=start_time, endTime=end_time, limit=limit)
        if not orders:
            break
        
        all_orders.extend(orders)
        end_time = orders[-1]['time']  # Ajustează end_time pentru a continua de la ultimul ordin
        
        # Verifică dacă am ajuns la limitele ordinelor vechi
        if len(orders) < limit:
            break
    
    # Filtrează doar ordinele FILLED și sortează-le după timp
    filled_orders = [order for order in all_orders if order['status'] == 'FILLED']
    filled_orders.sort(key=lambda x: x['time'])
    
    return filled_orders

# Exemplu de utilizare
symbol = 'BTCUSDT'
old_filled_orders = get_old_orders(symbol)

# Afișează primele 5 ordine vechi FILLED
for order in old_filled_orders[:5]:
    print(order)

    
def get_recent_filled_orders(order_type, max_age_seconds):

    all_filled_orders = get_filled_orders(order_type, symbol)
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

