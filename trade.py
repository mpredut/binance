import os
import time
from datetime import datetime, timedelta

from binance.client import Client
from binance.exceptions import BinanceAPIException

from utils import beep, precision, client, symbol, budget, order_cost_btc, price_change_threshold, get_interval_time

#import numpy as np

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

def calculate_commissions(amount, price):
    # Comisionul de 0.10%
    return (0.001 * amount) * price

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


def calculate_buy_price(current_price, changed_proc, decrease_proc=7):
    if changed_proc < 0:  # Dacă prețul a scăzut
        if abs(changed_proc) > decrease_proc:  # Dacă scăderea este mai mare decât pragul specificat
            proc = 1 - 0.01  # Aproape prețul curent
        else:
            # Calculează procentul suplimentar necesar pentru a ajunge la pragul de scădere
            procent_suplimentar = decrease_proc + changed_proc
            proc = 1 - procent_suplimentar / 100
            if procent_suplimentar < 0:
                proc = 1 - 0.01  # Aproape prețul curent
    else:  # Dacă prețul a crescut
        proc = 1 - decrease_proc/100;

    buy_price = current_price * proc
    buy_price = max(buy_price, 0)

    return buy_price


def calculate_sell_proc(initial_desired_proc, current_proc, i, max_i):
    # Calculează procentul dorit descrescător
    desired_proc = initial_desired_proc * (1 - (i / max_i))
    print(f"Step {i}/{max_i}: Desired proc calculated as {desired_proc}")

    # Factor de ajustare exponențială inversată
    #adjustment_factor = np.exp(-i / max_i)
    #print(f"Step {i}/{max_i}: Adjustment factor calculated as {adjustment_factor}")
                    
    # Calculează procentul ajustat
    #adjusted_proc = current_proc * np.minimum(2, np.maximum(0, 1 + adjustment_factor * desired_proc))
    #print(f"Step {i}/{max_i}: Adjusted proc calculated as {adjusted_proc}")

    return desired_proc


def price_changed(old_price, new_price):
    change = (new_price - old_price) / old_price
    changed_proc = change * 100  # În procente
    print(f"{changed_proc:.2f}%  BTC {new_price}")
    return changed_proc

class State:
    def __init__(self, name, price, timestamp, buy_price = None, quantity = None, buy_order_id = None, sell_order_id = None, iteration = 0):
        self.name = name
        self.buy_order_id = buy_order_id
        self.sell_order_id = sell_order_id
        self.price = price
        self.buy_price = buy_price
        self.quantity = quantity
        self.iteration = iteration
        self.timestamp = timestamp

states = []  # List to hold all trade states

MAX_ITERATIONS = 20
TIME_QUANT =  180 #3600  # Example: 1 hour


def price_changed_significantly_intime(old_state, new_state, threshold, time_limit_seconds):
    time_elapsed = datetime.now() - old_state.timestamp
    if time_elapsed.total_seconds() > time_limit_seconds:
        change = abs(new_state.price - old_state.price) / old_state.price
        changeproc = change * 100  # În procente
        #print(f"Schimbarea procentuală este: {changeproc:.2f}%   (pret1 {old_state.price} pret 2 {new_state.price})")
        return change >= threshold
    else:
        print(f" Timpul rămas: {time_limit_seconds - time_elapsed.total_seconds():.2f} secunde")
        return False



beep(1)
last_state = State("none", get_current_price(), timestamp=datetime.now())
#last_state.price = 55635
if last_state.price:
   print(f"Prețul curent al BTC: {last_state.price}")

current_buy_order_id = None

#last_state.buy_order_id = 29189134843
#last_state.buy_price = 56473
#last_state.quantity = 0.01771
#states.append(last_state)
while True:
    try:
        current_state = State("none", get_current_price(), timestamp=datetime.now())
        if current_state.price is None:
            print("Eroare la obținerea prețului. Încerc din nou în câteva secunde.")
            time.sleep(1)
            continue
            
        changed_proc = price_changed(last_state.price, current_state.price)
                 
        for state in states[:]:  # Copy to avoid modifying the list while iterating
            if check_order_filled(state.buy_order_id):
                print(f"Ordinul de cumparare a fost executat. Incercam vanzarea in {state.name}. interatia {state.iteration}....")
                if state.sell_order_id:
                    # Check if sell order has expired
                    expiration_time = state.timestamp + timedelta(seconds=TIME_QUANT * state.iteration)
                    #print(f"Debug: Current time: {datetime.now()}, Expiration time: {expiration_time}")
                    if datetime.now() > expiration_time:
                        cancel_order(state.sell_order_id)
                        state.sell_order_id = None

                # Place or update sell order
                if not state.sell_order_id:
                    proc = calculate_sell_proc(5/100, changed_proc, state.iteration, MAX_ITERATIONS)
                    proc = max(1.001, 1 + proc)
                    sell_price = state.buy_price * proc
                    sell_order = place_sell_order(sell_price, state.quantity)
                    if sell_order:
                        state.sell_order_id = sell_order['orderId']
                        state.iteration += 1
                        print(f"Ordin de vânzare plasat/actualizat la prețul {sell_price} = {state.buy_price} * {proc}%. ID ordin: {state.sell_order_id}")

                        
        for state in states[:]:  # Copy to avoid modifying the list while iterating
            if check_order_filled(state.sell_order_id):
                print("Ordinul de vânzare a fost executat.")
                beep(5)
                sell_order = client.get_order(symbol=symbol, orderId=state.sell_order_id)
                sell_price = float(sell_order['price'])
                btc_sell_quantity = float(sell_order['origQty'])

                value_sell = btc_sell_quantity * sell_price
                cost_buy_order_usdt = order_cost_btc * state.buy_price
                cost_sell_order_usdt = order_cost_btc * sell_price

                profit_brut = value_sell - budget - cost_buy_order_usdt - cost_sell_order_usdt
                commission = calculate_commissions(budget, buy_price) + calculate_commissions(btc_sell_quantity, sell_price)

                profit_net = profit_brut - commission
                budget += profit_net

                print(f"Profit net: {profit_net:.2f} USDT. Buget actual: {budget:.2f} USDT.")
                states.remove(state)
            if state.iteration > MAX_ITERATIONS:
                print(f"Incercam vanzarea in pierdere. interatia {state.iteration}....")
                state.name = "pierdere"
                state.iteration = 0
                state.timestamp = datetime.now()
                state.buy_price = current_state.price

        interval_time = get_interval_time()
        if states:
            last_state = states[-1]

        if price_changed_significantly_intime(last_state, current_state, price_change_threshold, timedelta(seconds = interval_time).total_seconds()):
            print(f"Prețul s-a schimbat cu {changed_proc}% care este mai mult de {price_change_threshold * 100}% in intervalul de {interval_time} secunde.")
            beep(2)
            print(f"Anulez ordinul existent de cumpărare dacă există (ID:{current_buy_order_id}).")
            if current_buy_order_id:#last_state.buy_order_id
                cancel_order(current_buy_order_id)
                current_buy_order_id = None

            buy_price = calculate_buy_price(current_state.price, changed_proc, 5.7)  
            if buy_price >= current_state.price :
                buy_price = current_state.price * 0.99
            
            btc_buy_quantity = budget / buy_price
            print(f"Plasez ordinul de cumpărare la prețul: {buy_price}, cantitate: {btc_buy_quantity}")
            buy_order = place_buy_order(buy_price, btc_buy_quantity)
            
            if buy_order:
                last_state = State("Profit",
                    buy_order_id=buy_order['orderId'],
                    sell_order_id=None,
                    price=current_state.price,
                    buy_price=buy_price,
                    iteration=0,
                    timestamp=datetime.now()
                )
                current_buy_order_id = buy_order['orderId']
                print(f"Ordin de cumpărare plasat la {buy_price}. ID ordin: {current_buy_order_id}")
                states.append(last_state)


    except BinanceAPIException as e:
        print(f"Eroare API Binance: {e}")
        time.sleep(1)  # Așteaptă 1 secundă înainte de a reporni încercările
    except Exception as e:
        print(f"Eroare: {e}")
        time.sleep(1)  # Așteaptă 1 secundă înainte de a reporni încercările
