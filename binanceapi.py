
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
            open_sell_orders = get_open_sell_orders(symbol)
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
            open_buy_orders = get_open_buy_orders(symbol)
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


def check_order_filled(order_id):
    try:
        if not order_id:
            return False
        order = client.get_order(symbol=symbol, orderId=order_id)
        return order['status'] == 'FILLED'
    except BinanceAPIException as e:
        print(f"Eroare la verificarea stării ordinului: {e}")
        return False


def get_filled_orders_Bed(order_type, symbol, backdays=2):

    #all_orders = client.get_all_orders(symbol=symbol)
    end_time = int(time.time()) * 1000 #miliseconds
    start_time = end_time - backdays * 24 * 60 * 60 * 1000 -  60 * 60 * 1000  # numărul de zile convertit în milisecunde
    start_time = int((datetime.datetime.now() - datetime.timedelta(days=backdays)).timestamp() * 1000)
        
    all_orders = client.get_all_orders(symbol=symbol, startTime=start_time, endTime=end_time, limit=1000000)
    print(f"Total orders fetched: {len(all_orders)}")
    # Filtrăm ordinele complet executate și pe cele care corespund tipului de ordin specificat
    filtered_orders = [
        {
            'orderId': order['orderId'],
            'price': float(order['price']),
            'quantity': float(order['origQty']),
            'timestamp': order['time'] / 1000,  # Timpul în secunde
            'side': order['side'].lower()
        }
        #print(f"Order ID: {order['orderId']}, Status: {order['status']}, Side: {order['side']}, Type: {order['type']}, Time: {order['time']}")
        for order in all_orders if order['status'] == 'FILLED' and order['side'].lower() == order_type.lower()
    ]
    
    for order in all_orders:
        # Afișăm fiecare ordin pentru a verifica structura și valorile cheilor relevante
        #print(f"Order ID: {order['orderId']}, Status: {order['status']}, Side: {order['side']}, Type: {order['type']}, Time: {order['time']}")

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
    #print("First few filled orders for inspection:")
     #for filled_order in filtered_orders[:5]:  # Afișează primele 5 ordine complet executate
        #print(filled_order)
        
    return filtered_orders
    
    
def get_filled_orders(order_type, symbol, backdays=3):
    try:
        end_time = int(time.time() * 1000)  # milisecunde
        
        interval_hours=1
        interval_ms = interval_hours * 60 * 60 * 1000  # interval_hours de ore în milisecunde
        start_time = end_time - backdays * 24 * 60 * 60 * 1000
        
        all_filtered_orders = []

        # Parcurgem intervale de 24 de ore și colectăm ordinele
        while start_time < end_time:
            current_end_time = min(start_time + interval_ms, end_time)
            orders = client.get_all_orders(symbol=symbol, startTime=start_time, endTime=current_end_time, limit=1000)
            print(f"orders of type '{order_type}': {len(orders)}")
            
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



def get_all_orders_in_time_range(order_type, symbol, start_time, end_time):
    all_orders = []
    last_order_id = None
    first_request = True

    while True:
        # Pregătim parametrii pentru cererea API
        params = {
            'symbol': symbol,
            'startTime': start_time,
            'endTime': end_time,
            'limit': 1000
        }
        
        # Adăugăm `orderId` doar pentru cererile următoare după prima
        #if not first_request:
            #params['orderId'] = last_order_id

        # Obținem ordinele cu parametrii actuali
        orders = client.get_all_orders(**params)
        
        if not orders:
            break

        # Setăm flagul pentru prima cerere la False după prima cerere
        first_request = False
        
        # Filtrăm ordinele dacă este specificat `order_type`
        if order_type:
            orders = [order for order in orders if order['side'].lower() == order_type.lower()]
        
        all_orders.extend(orders)
        last_order_id = orders[-1]['orderId']

        # Dacă ultimele ordine din pagină sunt în afara intervalului de timp, ne oprim
        if orders[-1]['time'] >= end_time:
            break

    # Filtrăm ordinele care sunt complet executate (FILLED)
    filled_orders = [
        {
            'orderId': order['orderId'],
            'price': float(order['price']),
            'quantity': float(order['origQty']),
            'timestamp': order['time'] / 1000,  # Timpul în secunde
            'side': order['side'].lower()
        }
        for order in all_orders if order['status'] == 'FILLED'
    ]

    return filled_orders


   
start_time = int(time.time() * 1000) - 1 * 24 * 60 * 60 * 1000  # Cu 3 zile în urmă
end_time = int(time.time() * 1000)  # Momentul curent
order_type = "buy"  # sau "sell", sau None pentru ambele tipuri

#all_filtered_orders = get_all_orders_in_time_range(order_type, symbol, start_time, end_time)
#all_filtered_orders = get_filled_orders(order_type, symbol)
#print(f"Filtered filled orders of type '{order_type}': {len(all_filtered_orders)}")
#print("First few filled orders for inspection:")
#for filled_order in all_filtered_orders[:5]:  # Afișează primele 5 ordine complet executate
    #print(filled_order)
        

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
        end_time = orders[-1]['time']  # atentie pt secunde inparte la 10000. Ajustează end_time pentru a continua de la ultimul ordin
        
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
    
    #current_time = int(time.time() * 1000)  # Convert current time to milliseconds
    current_time = int(time.time())

    print(f"Try cancel {len(open_orders)} {order_type} orders type ... ")
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
                'quantity': float(order['origQty']),
                'timestamp': order['time'] / 1000
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
                'quantity': float(order['origQty']),
                'timestamp': order['time'] / 1000
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
                'quantity': float(order['origQty']),
                'timestamp': order['time'] / 1000
            }
            for order in open_orders if order['side'] == order_type.upper()
        }
        
        return filtered_orders
    except BinanceAPIException as e:
        print(f"Error getting open {order_type} orders: {e}")
        return {}


