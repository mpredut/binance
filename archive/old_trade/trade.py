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
import bapi as api
import bapi_placeorder as po

def calculate_commissions(amount, price):
    # Comisionul de 0.10%
    return (0.001 * amount) * price

    
def calculate_buy_proc(current_price, changed_proc, decrease_proc=7):
    if changed_proc < 0:  # If the price has fallen.
        if abs(changed_proc) > decrease_proc:  # If the fall is larger than the given threshold.
            proc = 1 - 0.01  # Close to the current price.
        else:
            # Compute the extra percentage needed to reach the fall threshold.
            procent_suplimentar = decrease_proc + changed_proc
            proc = 1 - procent_suplimentar / 100
            if procent_suplimentar < 0:
                proc = 1 - 0.01  # Close to the current price.
    else:  # If the price has risen.
        proc = 1 - decrease_proc/100;

    return proc


def calculate_sell_proc(initial_desired_proc, current_proc, i, max_i):
    # Compute the decreasing target percentage.
    desired_proc = initial_desired_proc * (1 - (i / max_i))
    print(f"Step {i}/{max_i}: Desired proc calculated as {desired_proc}")

    # Factor de ajustare exponentiala inversata
    #adjustment_factor = np.exp(-i / max_i)
    #print(f"Step {i}/{max_i}: Adjustment factor calculated as {adjustment_factor}")
                    
    # Compute the adjusted percentage.
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
    changed_proc = change * 100  # In procente
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
   print(f"The current BTC price: {last_state.price}")

current_buy_order_id = None

#last_state.buy_order_id = 29189134843
#last_state.buy_price = 56473
#last_state.quantity = 0.01771
#states.append(last_state)


def check_orders(symbol):
    # Fetch every open sell order.
    open_orders = api.get_open_orders("SELL", symbol)

    # Fetch the current price for that symbol.
    current_price = api.get_current_price(symbol)

    min_price = 899999
    max_price = 0
    # Walk through every open order.
    for order_id, order_info in open_orders.items():
        order_price = order_info['price']  # The order's price.
        if(order_price > max_price) :
            max_price = order_price
        if(order_price < min_price) :
            min_price = order_price
    print(f"Min sell {min_price} Max sell {max_price}")

def check_and_close_orders(symbol):
    # Fetch every open sell order.
    open_orders = api.get_open_orders("SELL", symbol)

    # Fetch the current price for that symbol.
    current_price = api.get_current_price(symbol)

    # Walk through every open order.
    for order_id, order_info in open_orders.items():
        order_price = order_info['price']  # The order's price.
        print(f"check {order_price}  < {(current_price) + 300}")
        #Check whether the order's price is 2% below the current price.
        #api.cancel_order(order_id) 
        #print(f"Order {order_id} was closed because its price ({order_price}) is more than 2% below the current price ({current_price}).{(order_price) + 300 < (current_price)}")
        if (order_price)  < (current_price) + 300:
            api.cancel_order(symbol, order_id) 
            print(f"Order {order_id} was closed because its price ({order_price}) is more than 2% below the current price ({current_price}).")
        if (order_price)  > 59500:
            print(f"Order {order_id} was closed because its price ({order_price}) is very high compared with ({current_price}).")
            api.cancel_order(symbol, order_id) 
            
            
# A usage example:
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
            print("Failed to obtain the price. Retrying in a few seconds.")
            time.sleep(1)
            continue
        check_orders("BTCUSDT")
        interval_time = u.get_interval_time()
        changed_proc = ready_to_buy(last_state, current_state, price_change_threshold, max_threshold, timedelta(seconds = interval_time).total_seconds())
        # changed_proc = ready_to_buy(last_state, current_state, u.price_change_threshold, u.max_threshold, timedelta(seconds = interval_time).total_seconds())
                 
        for state in states[:]:  # Copy to avoid modifying the list while iterating
            if api.check_order_filled(state.buy_order_id, sym.btcsymbol):
                print(f"The buy order was filled. Trying to sell in {state.name}. iteration {state.iteration}....")
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
                    sell_order = po.place_order("SELL", sym.btcsymbol, sell_price, state.quantity)
                    if sell_order:
                        state.sell_order_id = sell_order['orderId']
                        state.iteration += 1
                        print(f"A sell order was placed/updated at the price {sell_price} = {state.buy_price} * {proc}%. Order ID: {state.sell_order_id}")

                        
        for state in states[:]:  # Copy to avoid modifying the list while iterating
            if api.check_order_filled(state.sell_order_id, sym.btcsymbol):
                print("The sell order was filled.")
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
                print(f"Trying to sell at a loss. iteration {state.iteration}....")
                state.name = "loss"
                state.iteration = 0
                state.timestamp = datetime.now()
                state.buy_price = current_state.price

        if states:
            last_state = states[-1]

        if (abs(changed_proc) > 0):
            print(f"The price changed by {changed_proc:.2f}%, which is more than {price_change_threshold}% over the interval of {interval_time:.2f} seconds.")
            u.beep(2)
            print(f"Cancelling the existing buy order if there is one (ID:{current_buy_order_id}).")
            if current_buy_order_id:#last_state.buy_order_id
                api.cancel_order(sym.btcsymbol, current_buy_order_id)
                current_buy_order_id = None
            
            
            buy_proc = calculate_buy_proc(current_state.price, changed_proc, 5.7)  
            buy_price = current_state.price * buy_proc
            if buy_price >= current_state.price :
                buy_price = current_state.price * 0.99
            
            btc_buy_quantity = budget / buy_price
            print(f"Placing the buy order at the price: {buy_price}, quantity: {btc_buy_quantity}")
            buy_order = po.place_order("BUY", sym.btcsymbol, buy_price, btc_buy_quantity)
            
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
                print(f"A buy order was placed at {buy_price}. Order ID: {current_buy_order_id}")
                states.append(last_state)


    except BinanceAPIException as e:
        print(f"Eroare API Binance: {e}")
        time.sleep(1)  # Wait 1 second before restarting the attempts.
    except Exception as e:
        print(f"Eroare: {e}")
        time.sleep(1)  # Wait 1 second before restarting the attempts.
