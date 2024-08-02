import os
import time
from datetime import datetime, timedelta

from binance.client import Client
from binance.exceptions import BinanceAPIException

from utils import beep, precision, client, symbol, budget, order_cost_btc, price_change_threshold, interval_time


def place_buy_order(price, quantity):
    try:
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

def calculate_buy_price(current_price, changed_proc):
    if changed_proc < 0: # Dacă prețul a scăzut
        if abs(changed_proc) > 7: # Dacă scăderea este mai mare de 7%, cumpără apx la prețul curent
            proc = 0.99
        else:
            # Calculează procentul suplimentar necesar pentru a ajunge la 7%
            procent_suplimentar = 7 + changed_proc
            proc = (1 - procent_suplimentar / 100)# Calculează prețul buy_price cu diferența suplimentară
            if procent_suplimentar < 0 :
                proc = 0.99
    else:# Dacă prețul a crescut
        proc = 0.93  # 7% sub prețul curent

    buy_price = current_state.price * proc 
    buy_price = max(buy_price, 0)
    
    return buy_price


def price_changed(old_price, new_price):
    change = (new_price - old_price) / old_price
    changed_proc = change * 100  # În procente
    print(f"{changed_proc:.2f}%  BTC {new_price}")
    return changed_proc

class State:
    def __init__(self, price, timestamp):
        self.price = price
        self.timestamp = timestamp

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

last_state = State(get_current_price(), timestamp=datetime.now())

if last_state.price:
    print(f"Prețul curent al BTC: {last_state.price}")

current_buy_order_id = None
current_sell_order_id = None
buy_price = 0

while True:
    try:
        current_state = State(get_current_price(), timestamp=datetime.now())
        if current_state.price is None:
            print("Eroare la obținerea prețului. Încerc din nou în câteva secunde.")
            time.sleep(1)
            continue
            
        changed_proc = price_changed(last_state.price, current_state.price)

        if current_sell_order_id and check_order_filled(current_sell_order_id):
            print("Ordinul de vânzare a fost executat.")
            beep(5)
            sell_order = client.get_order(symbol=symbol, orderId=current_sell_order_id)
            sell_price = float(sell_order['price'])
            btc_sell_quantity = float(sell_order['origQty'])

            value_sell = btc_sell_quantity * sell_price
            cost_buy_order_usdt = order_cost_btc * buy_price
            cost_sell_order_usdt = order_cost_btc * sell_price

            profit_brut = value_sell - budget - cost_buy_order_usdt - cost_sell_order_usdt
            commission = calculate_commissions(budget, buy_price) + calculate_commissions(btc_sell_quantity, sell_price)

            profit_net = profit_brut - commission
            budget += profit_net

            print(f"Profit net: {profit_net:.2f} USDT. Buget actual: {budget:.2f} USDT.")
            
            current_sell_order_id = None

        if current_buy_order_id and check_order_filled(current_buy_order_id):
            print("Ordinul de cumpărare a fost executat.")
            beep(1)
            buy_order = client.get_order(symbol=symbol, orderId=current_buy_order_id)
            buy_price = float(buy_order['price'])
            btc_buy_quantity = float(buy_order['origQty'])

            sell_price = buy_price * 1.05  # 5% peste prețul de cumpărare
            btc_effective_quantity = btc_buy_quantity - order_cost_btc
            print(f"Plasez ordinul de vânzare la prețul: {sell_price}, cantitate: {btc_effective_quantity}")
            sell_order = place_sell_order(sell_price, btc_effective_quantity)

            if sell_order:
                current_sell_order_id = sell_order['orderId']
                print(f"Ordin de vânzare plasat la {sell_price}. ID ordin: {current_sell_order_id}")

        if price_changed_significantly_intime(last_state, current_state, price_change_threshold, timedelta(seconds = interval_time).total_seconds()):
            print(f"Prețul s-a schimbat cu {changed_proc}% care este mai mult de {price_change_threshold * 100}% in intervalul de {interval_time} secunde.")
            beep(2)
            print(f"Anulez ordinul existent de cumpărare dacă există (ID:{current_buy_order_id}).")
            if current_buy_order_id:
                cancel_order(current_buy_order_id)
                current_buy_order_id = None

            buy_price = calculate_buy_price(current_state.price, changed_proc)  
            if buy_price >= current_state.price :
                buy_price = current_state.price * 0.999
            
            buy_price = round(buy_price, 0)
            btc_buy_quantity = round(budget / buy_price, 5)
            print(f"Plasez ordinul de cumpărare la prețul: {buy_price}, cantitate: {btc_buy_quantity}")
            buy_order = place_buy_order(buy_price, btc_buy_quantity)

            if buy_order:
                last_state = State(current_state.price, timestamp=datetime.now())
                current_buy_order_id = buy_order['orderId']
                print(f"Ordin de cumpărare plasat la {buy_price}. ID ordin: {current_buy_order_id}")

    except BinanceAPIException as e:
        print(f"Eroare API Binance: {e}")
        time.sleep(1)  # Așteaptă 1 secundă înainte de a reporni încercările
    except Exception as e:
        print(f"Eroare neașteptată: {e}")
        time.sleep(1)  # Așteaptă 1 secundă înainte de a reporni încercările
