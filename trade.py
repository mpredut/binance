import os
import time
from datetime import datetime, timedelta
#import numpy as np

from binance.client import Client
from binance.exceptions import BinanceAPIException


#my imports
import binanceapi as api
import utils as u
from binanceapi import client, symbol, precision, get_quantity_precision, get_current_price, check_order_filled,  place_order, cancel_order
from utils import beep, get_interval_time, are_difference_equal_with_aprox_proc, are_values_very_close, budget, order_cost_btc, price_change_threshold, max_threshold


def calculate_commissions(amount, price):
    # Comisionul de 0.10%
    return (0.001 * amount) * price

    
def calculate_buy_proc(current_price, changed_proc, decrease_proc=7):
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

    return proc


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
TIME_QUANT =  3600  # Example: 1 hour

def price_changed(old_price, new_price):
    change = (new_price - old_price) / old_price
    changed_proc = change * 100  # În procente
    return changed_proc

def ready_to_buy(old_state, new_state, threshold, max_treshold, time_limit_seconds):
    
    changed_proc = price_changed(last_state.price, current_state.price)
    if(abs(changed_proc) >= max_threshold) :
        return changed_proc
        
    time_elapsed = datetime.now() - old_state.timestamp
    if time_elapsed.total_seconds() >= time_limit_seconds:
        time_expired = True
    else:
        time_expired = False
    print(f"Exp:{time_limit_seconds - time_elapsed.total_seconds():.0f} RefBTC {old_state.price:.0f} {changed_proc:.2f}% BTC {new_state.price:.0f}")
     
    if(time_expired and abs(changed_proc) >= threshold) :
        return changed_proc
    else :
        return 0
    


beep(1)
last_state = State("none", get_current_price(symbol), timestamp=datetime.now())
#last_state.price = 55635
if last_state.price:
   print(f"Prețul curent al BTC: {last_state.price}")

current_buy_order_id = None

#last_state.buy_order_id = 29189134843
#last_state.buy_price = 56473
#last_state.quantity = 0.01771
#states.append(last_state)


def check_orders(symbol):
    # Preluăm toate ordinele deschise de vânzare
    open_orders = api.get_open_orders("sell", symbol)

    # Preluăm prețul curent pentru simbolul respectiv
    current_price = api.get_current_price(symbol)

    min_price = 899999
    max_price = 0
    # Parcurgem toate ordinele deschise
    for order_id, order_info in open_orders.items():
        order_price = order_info['price']  # Prețul ordinului
        if(order_price > max_price) :
            max_price = order_price
        if(order_price < min_price) :
            min_price = order_price
    print(f"Min vanzare {min_price} Max vanzare {max_price}")

def check_and_close_orders(symbol):
    # Preluăm toate ordinele deschise de vânzare
    open_orders = api.get_open_orders("sell", symbol)

    # Preluăm prețul curent pentru simbolul respectiv
    current_price = api.get_current_price(symbol)

    # Parcurgem toate ordinele deschise
    for order_id, order_info in open_orders.items():
        order_price = order_info['price']  # Prețul ordinului
        print(f"check {order_price}  < {(current_price) + 300}")
        #Verificăm dacă prețul ordinului este cu 2% mai mic decât prețul curent
        #api.cancel_order(order_id) 
        #print(f"Ordinul {order_id} a fost închis deoarece prețul său ({order_price}) este sub 2% din prețul curent ({current_price}).{(order_price) + 300 < (current_price)}")
        if (order_price)  < (current_price) + 300:
            api.cancel_order(order_id) 
            print(f"Ordinul {order_id} a fost închis deoarece prețul său ({order_price}) este sub 2% din prețul curent ({current_price}).")
        if (order_price)  > 59500:
            print(f"Ordinul {order_id} a fost închis deoarece prețul său ({order_price}) este foarte mare fata de  ({current_price}).")
            api.cancel_order(order_id) 
            
            
# Exemplu de utilizare:
check_and_close_orders("BTCUSDT")
usdt = api.get_asset_info("sell", symbol)
btc = api.get_asset_info("buy", symbol)
print(f" BTC {btc}")
print(f" USDT {usdt}")


while True:
    try:
        current_state = State("none", get_current_price(symbol), timestamp=datetime.now())
        if current_state.price is None:
            print("Eroare la obținerea prețului. Încerc din nou în câteva secunde.")
            time.sleep(1)
            continue
        check_orders("BTCUSDT")
        interval_time = get_interval_time()
        changed_proc = ready_to_buy(last_state, current_state, price_change_threshold, max_threshold, timedelta(seconds = interval_time).total_seconds())
                 
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
                    sell_order = place_order("sell", symbol, sell_price, state.quantity)
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
                commission = calculate_commissions(budget, state.buy_price) + calculate_commissions(btc_sell_quantity, sell_price)

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

        if states:
            last_state = states[-1]

        if (abs(changed_proc) > 0):
            print(f"Pretul s-a schimbat cu {changed_proc:.2f}% care este mai mult de {price_change_threshold}% in intervalul de {interval_time:.2f} secunde.")
            beep(2)
            print(f"Anulez ordinul existent de cumpărare dacă există (ID:{current_buy_order_id}).")
            if current_buy_order_id:#last_state.buy_order_id
                cancel_order(current_buy_order_id)
                current_buy_order_id = None
            
            
            buy_proc = calculate_buy_proc(current_state.price, changed_proc, 5.7)  
            buy_price = current_state.price * buy_proc
            if buy_price >= current_state.price :
                buy_price = current_state.price * 0.99
            
            btc_buy_quantity = budget / buy_price
            print(f"Plasez ordinul de cumpărare la prețul: {buy_price}, cantitate: {btc_buy_quantity}")
            buy_order = place_order("buy", symbol, buy_price, btc_buy_quantity)
            
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
