import time
import datetime
import random
from binance.client import Client
from binance.exceptions import BinanceAPIException

from apikeys import api_key, api_secret

client = Client(api_key, api_secret)

symbol = 'BTCUSDT'
monitor_interval = 7.77  # Intervalul de monitorizare în secunde

def place_sell_order(price, quantity):
    try:
        price = round(price, 2)
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

def cancel_order(order_id):
    try:
        client.cancel_order(symbol=symbol, orderId=order_id)
        print(f"Ordinul cu ID {order_id} a fost anulat.")
    except BinanceAPIException as e:
        print(f"Eroare la anularea ordinului: {e}")

def get_current_price():
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        return float(ticker['price'])
    except BinanceAPIException as e:
        print(f"Eroare la obținerea prețului curent: {e}")
        return None

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
        print(f"Eroare la obținerea ordinelor deschise: {e}")
        return {}


def are_difference_aprox_proc(value1, value2, target_percent = 1.0):
    max_iterations = random.randint(1, 100)
    if max_iterations < 1:
        max_iterations = 1
    if max_iterations > 100:
        max_iterations = 100
    # Calculează initial_tolerance ca 1% din target_percent
    initial_tolerance = target_percent * 0.01
    tolerance_step = initial_tolerance * 0.1
    #print(f"initial_tolerance {initial_tolerance}:")
    #print(f"tolerance_step {tolerance_step}:")

    #valoarea maximă a toleranței  nu depășește jumătate din target_percent
    max_tolerance = max_iterations * tolerance_step + initial_tolerance
    if max_tolerance > target_percent / 2:
        # Ajustează tolerance_step pentru a respecta limita
        tolerance_step = (target_percent / 2 - initial_tolerance) / max_iterations
        print(f"tolerance_step adjust {tolerance_step}:")

    def calculate_difference_percent(val1, val2):
        return abs(val1 - val2) / ((val1 + val2) / 2) * 100

    iteration = 0
    tolerance = initial_tolerance

    while iteration < max_iterations:
        difference_percent = calculate_difference_percent(value1, value2)
        lower_bound = target_percent - tolerance
        upper_bound = target_percent + tolerance
        #return lower_bound <= difference_percent <= upper_bound
        
        #print(f"Iteration {iteration}:")
        #print(f"  Difference percent: {difference_percent:.4f}%")
        #print(f"  Lower bound: {lower_bound:.4f}%")
        #print(f"  Upper bound: {upper_bound:.4f}%")
        
        if lower_bound <= difference_percent <= upper_bound:
            return True, iteration, tolerance

        tolerance += tolerance_step
        iteration += 1

    return False, iteration, tolerance
   

def are_values_very_close(value1, value2, target_percent=1.0):
    max_iterations = random.randint(1, 100)
    if max_iterations < 1:
        max_iterations = 1
    if max_iterations > 100:
        max_iterations = 100
    # Calculează initial_tolerance ca 1% din target_percent
    initial_tolerance = target_percent * 0.01
    tolerance_step = initial_tolerance * 0.1
    #print(f"initial_tolerance {initial_tolerance}:")
    #print(f"tolerance_step {tolerance_step}:")

    #valoarea maximă a toleranței  nu depășește jumătate din target_percent
    max_tolerance = max_iterations * tolerance_step + initial_tolerance
    if max_tolerance > target_percent / 2:
        # Ajustează tolerance_step pentru a respecta limita
        tolerance_step = (target_percent / 2 - initial_tolerance) / max_iterations
        print(f"tolerance_step adjust {tolerance_step}:")

    def calculate_difference_percent(val1, val2):
        return abs(val1 - val2) / ((val1 + val2) / 2) * 100

    iteration = 0
    tolerance = initial_tolerance

    while iteration < max_iterations:
        difference_percent = calculate_difference_percent(value1, value2)
        #lower_bound = target_percent - tolerance
        upper_bound = target_percent + tolerance
        #return lower_bound <= difference_percent <= upper_bound
        
        #print(f"Iteration {iteration}:")
        #print(f"  Difference percent: {difference_percent:.4f}%")
        #print(f"  Upper bound: {upper_bound:.4f}%")
        
        if difference_percent <= upper_bound:
            return True, iteration, tolerance

        tolerance += tolerance_step
        iteration += 1

    return False, iteration, tolerance
    
   
def monitor_sell_orders():
   
    
   while True:
        try:
            sell_orders = get_open_sell_orders()  # Inițializăm cu ordinele curente de vânzare
            if not sell_orders:
                print("Nu există ordine de vânzare deschise inițial.")
            current_price = get_current_price()
            if current_price is None:
                print("Eroare la obținerea prețului. Încerc din nou în câteva secunde.")
                time.sleep(2)
                continue
            print(f"Prețul curent BTC: {current_price:.2f}")
 
            for order_id in list(sell_orders.keys()):
                sell_order = sell_orders[order_id]
                sell_price = sell_order['price']
                
                difference_percent = abs(current_price - sell_price) / sell_price * 100
                print(f"sell price {sell_price}, current price {current_price} difference: {difference_percent:.2f}%, Order ID {order_id} ")
                
                are_close, iterations, final_tolerance = are_values_very_close(current_price, sell_price, 0.77)
                if are_close:
                    print(f"Current price {current_price} and sell price {sell_price} are close! ")
                    cancel_order(order_id)
                    
                    new_sell_price = round(current_price * 1.001 + 500, 2)
                    quantity = sell_order['quantity']
                    
                    new_order = place_sell_order(new_sell_price, quantity)
                    if new_order:    
                        sell_orders[new_order['orderId']] = {
                            'price': new_sell_price,
                            'quantity': quantity
                        }
                        del sell_orders[order_id]
                        print(f"Update order from {sell_price} to {new_sell_price}. New ID: {new_order['orderId']}")
                    else:
                        print("Eroare la plasarea noului ordin de vânzare.")
                        
            time.sleep(monitor_interval)
            
        except BinanceAPIException as e:
            print(f"Eroare API Binance: {e}")
            time.sleep(1)
        except Exception as e:
            print(f"Eroare: {e}")
            time.sleep(1)

monitor_sell_orders()
