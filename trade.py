import os
import time
from datetime import datetime, timedelta
#import numpy as np

from binance.client import Client
from binance.exceptions import BinanceAPIException


#my imports
import log
import utils as u
import symbols as sym
import binanceapi as api

def calculate_commissions(amount, price):
    # Comisionul de 0.10%
    return (0.001 * amount) * price

    
def calculate_buy_proc(current_price, changed_proc, decrease_proc=7):
    if changed_proc < 0:  # Daca pretul a scazut
        if abs(changed_proc) > decrease_proc:  # Daca scaderea este mai mare decat pragul specificat
            proc = 1 - 0.01  # Aproape pretul curent
        else:
            # Calculeaza procentul suplimentar necesar pentru a ajunge la pragul de scadere
            procent_suplimentar = decrease_proc + changed_proc
            proc = 1 - procent_suplimentar / 100
            if procent_suplimentar < 0:
                proc = 1 - 0.01  # Aproape pretul curent
    else:  # Daca pretul a crescut
        proc = 1 - decrease_proc/100;

    return proc


def calculate_sell_proc(initial_desired_proc, current_proc, i, max_i):
    # Calculeaza procentul dorit descrescator
    desired_proc = initial_desired_proc * (1 - (i / max_i))
    print(f"Step {i}/{max_i}: Desired proc calculated as {desired_proc}")

    # Factor de ajustare exponentiala inversata
    #adjustment_factor = np.exp(-i / max_i)
    #print(f"Step {i}/{max_i}: Adjustment factor calculated as {adjustment_factor}")
                    
    # Calculeaza procentul ajustat
    #adjusted_proc = current_proc * np.minimum(2, np.maximum(0, 1 + adjustment_factor * desired_proc))
    #print(f"Step {i}/{max_i}: Adjusted proc calculated as {adjusted_proc}")

    return desired_proc



class State:
    def __init__(self, name, price, timestamp, buy_price = None, quantity = 0.017, buy_order_id = None, sell_order_id = None, iteration = 0):
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

def ready_to_buy(old_state, new_state, threshold, max_threshold, time_limit_seconds):
    
    changed_proc = price_changed(old_state.price, current_state.price)
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
    


u.beep(1)
last_state = State("none", api.get_current_price(sym.btcsymbol), timestamp=datetime.now())
#last_state.price = 55635
if last_state.price:
   print(f"Pretul curent al BTC: {last_state.price}")

current_buy_order_id = None

#last_state.buy_order_id = 29189134843
#last_state.buy_price = 56473
#last_state.quantity = 0.01771
#states.append(last_state)


def check_orders(symbol):
    # Preluam toate ordinele deschise de vanzare
    open_orders = api.get_open_orders("SELL", symbol)

    # Preluam pretul curent pentru simbolul respectiv
    current_price = api.get_current_price(symbol)

    min_price = 899999
    max_price = 0
    # Parcurgem toate ordinele deschise
    for order_id, order_info in open_orders.items():
        order_price = order_info['price']  # Pretul ordinului
        if(order_price > max_price) :
            max_price = order_price
        if(order_price < min_price) :
            min_price = order_price
    print(f"Min vanzare {min_price} Max vanzare {max_price}")

def check_and_close_orders(symbol):
    # Preluam toate ordinele deschise de vanzare
    open_orders = api.get_open_orders("SELL", symbol)

    # Preluam pretul curent pentru simbolul respectiv
    current_price = api.get_current_price(symbol)

    # Parcurgem toate ordinele deschise
    for order_id, order_info in open_orders.items():
        order_price = order_info['price']  # Pretul ordinului
        print(f"check {order_price}  < {(current_price) + 300}")
        #Verificam daca pretul ordinului este cu 2% mai mic decat pretul curent
        #api.cancel_order(order_id) 
        #print(f"Ordinul {order_id} a fost închis deoarece pretul sau ({order_price}) este sub 2% din pretul curent ({current_price}).{(order_price) + 300 < (current_price)}")
        if (order_price)  < (current_price) + 300:
            api.cancel_order(symbol, order_id) 
            print(f"Ordinul {order_id} a fost închis deoarece pretul sau ({order_price}) este sub 2% din pretul curent ({current_price}).")
        if (order_price)  > 59500:
            print(f"Ordinul {order_id} a fost închis deoarece pretul sau ({order_price}) este foarte mare fata de  ({current_price}).")
            api.cancel_order(symbol, order_id) 
            
            
# Exemplu de utilizare:
#check_and_close_orders("BTCUSDT")
usdt = api.get_asset_info("SELL", sym.btcsymbol)
btc = api.get_asset_info("BUY", sym.btcsymbol)
print(f" BTC {btc}")
print(f" USDT {usdt}")



# Bugetul initial
budget = 1000  # USDT
order_cost_btc = 0.00004405  # BTC
max_threshold = 1.5 #% procent * 100
price_change_threshold = 0.07  # Pragul de schimbare a pretului, 0.7%
interval_time = 2 * 3600 # 2 h * 3600 seconds.
#interval_time = 97 * 79

while True:
    try:
        current_state = State("none", api.get_current_price(sym.btcsymbol), timestamp=datetime.now())
        if current_state.price is None:
            print("Eroare la obtinerea pretului. Încerc din nou în cateva secunde.")
            time.sleep(1)
            continue
        check_orders("BTCUSDT")
        interval_time = u.get_interval_time()
        changed_proc = ready_to_buy(last_state, current_state, price_change_threshold, max_threshold, timedelta(seconds = interval_time).total_seconds())
        # changed_proc = ready_to_buy(last_state, current_state, u.price_change_threshold, u.max_threshold, timedelta(seconds = interval_time).total_seconds())
                 
        for state in states[:]:  # Copy to avoid modifying the list while iterating
            if api.check_order_filled(state.buy_order_id, sym.btcsymbol):
                print(f"Ordinul de cumparare a fost executat. Incercam vanzarea in {state.name}. interatia {state.iteration}....")
                if state.sell_order_id:
                    # Check if sell order has expired
                    expiration_time = state.timestamp + timedelta(seconds=TIME_QUANT * state.iteration)
                    #print(f"Debug: Current time: {datetime.now()}, Expiration time: {expiration_time}")
                    if datetime.now() > expiration_time:
                        api.cancel_order(sym.btcsymbol, state.sell_order_id)
                        state.sell_order_id = None

                # Place or update sell order
                if not state.sell_order_id:
                    proc = calculate_sell_proc(5/100, changed_proc, state.iteration, MAX_ITERATIONS)
                    proc = max(1.001, 1 + proc)
                    sell_price = state.buy_price * proc
                    sell_order = api.place_order("SELL", sym.btcsymbol, sell_price, state.quantity)
                    if sell_order:
                        state.sell_order_id = sell_order['orderId']
                        state.iteration += 1
                        print(f"Ordin de vanzare plasat/actualizat la pretul {sell_price} = {state.buy_price} * {proc}%. ID ordin: {state.sell_order_id}")

                        
        for state in states[:]:  # Copy to avoid modifying the list while iterating
            if api.check_order_filled(state.sell_order_id, sym.btcsymbol):
                print("Ordinul de vanzare a fost executat.")
                u.beep(5)
                sell_order = client.get_order(symbol=sym.btcsymbol, orderId=state.sell_order_id)
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
            u.beep(2)
            print(f"Anulez ordinul existent de cumparare daca exista (ID:{current_buy_order_id}).")
            if current_buy_order_id:#last_state.buy_order_id
                api.cancel_order(sym.btcsymbol, current_buy_order_id)
                current_buy_order_id = None
            
            
            buy_proc = calculate_buy_proc(current_state.price, changed_proc, 5.7)  
            buy_price = current_state.price * buy_proc
            if buy_price >= current_state.price :
                buy_price = current_state.price * 0.99
            
            btc_buy_quantity = budget / buy_price
            print(f"Plasez ordinul de cumparare la pretul: {buy_price}, cantitate: {btc_buy_quantity}")
            buy_order = api.place_order("BUY", sym.btcsymbol, buy_price, btc_buy_quantity)
            
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
                print(f"Ordin de cumparare plasat la {buy_price}. ID ordin: {current_buy_order_id}")
                states.append(last_state)


    except BinanceAPIException as e:
        print(f"Eroare API Binance: {e}")
        time.sleep(1)  # Asteapta 1 secunda înainte de a reporni încercarile
    except Exception as e:
        print(f"Eroare: {e}")
        time.sleep(1)  # Asteapta 1 secunda înainte de a reporni încercarile
