import json
import os
import time

from datetime import datetime
import threading
from threading import Thread,Timer
import pandas as pd

####Binance
#from binance.client import Client
#from binance.exceptions import BinanceAPIException

#my imports
import binanceapi as api
import binanceapi_trades as apitrades
import binanceapi_allorders as apiorders

import utils
import log
#import alert


def adjust_monitor_interval(initial_interval, min_interval, total_duration, elapsed_time):
    if elapsed_time >= total_duration:
        return min_interval
    
    interval_range = initial_interval - min_interval
    time_fraction = elapsed_time / total_duration
    current_interval = initial_interval - (interval_range * time_fraction)
    
    return max(current_interval, min_interval)
    
def calculate_target_price(filled_price, current_price, procent_defined, time_fraction):
    # Calculul procentului ajustat inițial
    procent_adjusted = (procent_defined * (1 - time_fraction)) - (1 - current_price / filled_price)

    # Calculul prețului țintă inițial
    target_price = filled_price * (1 + procent_adjusted)
    
    # Dacă target_price a ajuns sub current_price, îl ajustăm
    if target_price < current_price:
        # Definim un dinamic_procent care scade treptat în timp
        dinamic_procent = 0.01 * (1 - time_fraction) + 1  # Începe de la 1.01 și scade către 1
        target_price = current_price * dinamic_procent
    
    return target_price
 
procent_defined = 0.10  # Procentul inițial (10%)
def sell_order_gradually(order, start_time, end_time):

    filled_quantity = order['quantity']
    filled_price = order['price']
    close_order_id = order.get('orderId')
    order_id = None

    initial_interval = 20  # Interval inițial de monitorizare (în secunde)
    min_interval = 5       # Interval minim de monitorizare (în secunde)
    total_duration = end_time - start_time  # Durata totală a procesului
    current_time = start_time
    
    #while time.time() < end_time:
    while current_time < end_time:
        #elapsed_time = time.time() - start_time
        elapsed_time = current_time - start_time
        monitor_interval = adjust_monitor_interval(initial_interval, min_interval, total_duration, elapsed_time)
         
        print(f"Monitor interval: {monitor_interval:.2f} seconds")

        current_price = get_current_price(api.symbol)

        if current_price is None:
            print("Eroare la obținerea prețului. Încerc din nou în câteva secunde.")
            time.sleep(monitor_interval)
            continue

        
        time_fraction = elapsed_time / total_duration
        target_price = calculate_target_price(filled_price, current_price, procent_defined, time_fraction)

        # Calculăm prețul propus
        #if current_price > filled_price:
        #    target_price = max(filled_price * 1.01, current_price * 1.01)  # Preț mai mare cu 1%
        #else:
        #    target_price = filled_price * (1 + time_fraction * (current_price / filled_price - 1))

        print(f"Vânzare graduală: target_price={target_price:.2f}, current_price={current_price:.2f}")
        print(f"Elapsed Time: {elapsed_time:.2f} seconds, Target Price: {target_price:.2f} USD")

        # Anulăm ordinul anterior înainte de a plasa unul nou
        if order_id:
            if api.check_order_filled(order_id) :
                return; #order filled!
            cancel_order(order_id)
            print(f"Anulat ordinul anterior cu ID: {order_id}")

        # Plasăm ordinul de vânzare
        new_order = api.place_order("sell", symbol, target_price, filled_quantity)
        if new_order:
            order_id = new_order['orderId']
            print(f"Plasat ordin de vânzare la prețul {target_price:.2f}. New Order ID: {order_id}")
        else:
            print("Eroare la plasarea ordinului de vânzare.")
            order_id = None  # Resetează ID-ul ordinului dacă plasarea eșuează
        
        # Așteptăm un interval ajustat înainte de următoarea ajustare
        time.sleep(monitor_interval)
        current_time += monitor_interval



def monitor_filled_buy_orders_old():
    if threading.active_count() > 1:  # Dacă sunt deja fire active (în afară de firul principal)
        print("Fire active detectate, ieșim din funcție pentru a nu porni fire noi.")
        return
 
    max_age_seconds =  3 * 24 * 3600  # Timpul maxim în care ordinele executate sunt considerate recente (2 ore)
    filled_buy_orders = apiorders.get_recent_filled_orders('buy', symbol, max_age_seconds)

    for order in filled_buy_orders:
        current_time = time.time()
        end_time = current_time + 2 * 3600  # Procesul durează două ore
        print("marius")
        print(order)
        # Pornim un fir nou pentru fiecare ordin de cumpărare executat recent
        #thread = threading.Thread(target=sell_order_gradually, args=(order, current_time, end_time))
        #thread = threading.Thread(target=sell_order_gradually, args=(order, current_time, end_time, filled_price, current_price, procent_defined))      
        #thread.start()


def get_close_buy_orders_without_sell(api, max_age_seconds, profit_percentage):
    close_buy_orders = apitrades.get_trade_orders('buy', symbol, max_age_seconds)
    close_sell_orders = apitrades.get_trade_orders('sell', symbol, max_age_seconds)
    
    # Lista de ordere 'buy' care nu au un 'sell' asociat cu profitul dorit
    buy_orders_without_sell = []

    for buy_order in close_buy_orders:
        filled_price = buy_order['filled_price']
        symbol = buy_order['symbol']
        buy_quantity = buy_order['quantity']  # Cantitatea cumpărată
        
        # Filtrează orderele de tip 'sell' asociate cu acest 'buy' (același simbol și cu prețul dorit)
        related_sell_orders = [
            order for order in close_sell_orders 
            if order['symbol'] == symbol and order['filled_price'] >= filled_price * (1 + profit_percentage / 100)
        ]
        
        # Calculează suma cantității vândute pentru orderele 'sell' găsite
        total_sell_quantity = sum(order['quantity'] for order in related_sell_orders)
        
        # Dacă cantitatea totală vândută este mai mică decât cantitatea cumpărată
        if total_sell_quantity < buy_quantity:
            # Adaugă buy_order la lista de ordere care încă nu au sell complet
            buy_orders_without_sell.append(buy_order)

    return buy_orders_without_sell
    

def monitor_close_orders_by_age1(max_age_seconds):
    if threading.active_count() > 2:  # Dacă sunt deja fire active (în afară de firul principal)
        print("Fire active detectate, ieșim din funcție pentru a nu porni fire noi.")
        return
 
    close_buy_orders = apitrades.get_trade_orders('buy',  symbol, max_age_seconds)

    print(f"BUY ORDERS, {len(close_buy_orders)}")
    current_price = api.get_current_price(api.symbol)
    for order in close_buy_orders:
        current_time = time.time()
        end_time = current_time + 2 * 3600  # Procesul durează două ore
        filled_price = order['price']
        quantity = float(order['qty']) #quantity

        if current_price >= filled_price * 1.04 or utils.are_values_very_close(current_price, filled_price * 1.04):  # Dacă prețul curent este cu 7% mai mare
            print(f"Prețul curent ({current_price}) este cu 4% mai mare decât prețul de cumpărare ({filled_price}). Inițiem vânzarea.cantitate{quantity}")
            
            # Pornim un fir nou pentru a vinde BTC-ul
            thread = threading.Thread(target=api.place_order, args=("sell", symbol, current_price + 200, quantity))
            #sell_order_gradually, args=(order, current_time, end_time))
            thread.start()
            #return
        else:
            print(f"Prețul curent ({current_price}) nu a atins încă pragul de 4% față de prețul de cumpărare ({filled_price}).")
            #return
            
    close_sell_orders = apitrades.get_trade_orders('sell',  symbol, max_age_seconds)
    sorted_sell_orders = sorted(close_sell_orders, key=lambda x: x['price'])
    close_sell_orders = sorted_sell_orders
    print(f"SELL ORDERS, {len(close_sell_orders)}")
    for order in close_sell_orders:
        current_time = time.time()
        end_time = current_time + 2 * 3600  # Procesul durează două ore
        filled_price = order['price']
        quantity = float(order['qty']) #quantity

        if current_price <= filled_price * 0.94 or utils.are_values_very_close(current_price, filled_price * 0.94):  # Dacă prețul curent este cu 7% mai mare
            print(f"Prețul curent ({current_price}) este cu 4% mai mic decât prețul de vanzare ({filled_price}). Inițiem cumpararea.cantitate{quantity}.")
            
            # Pornim un fir nou pentru a vinde BTC-ul
            thread = threading.Thread(target=api.place_order, args=("buy", symbol, current_price - 200, quantity))
            #sell_order_gradually, args=(order, current_time, end_time))
            thread.start()
            #return
        else:
            print(f"Prețul curent ({current_price}) nu a atins încă pragul de 4% față de prețul de vanzare ({filled_price}).")
            #return        



# Variabilă globală care stochează timpul de început al monitorizării
start_time_global = None

def monitor_close_orders_by_age2(max_age_seconds):
    global start_time_global
    
    if threading.active_count() > 2:  # Dacă sunt deja fire active (în afară de firul principal)
        print("Fire active detectate, ieșim din funcție pentru a nu porni fire noi.")
        return
    
    # Inițializăm timpul global la prima execuție
    if start_time_global is None:
        start_time_global = time.time()

    # Calculăm timpul total scurs de la prima execuție a funcției
    current_time = time.time()
    elapsed_time = current_time - start_time_global
    interval_durata = 2 * 3600  # Durata maximă (2 ore)

    # Calculăm procentul în funcție de timpul scurs (de la 4% până la 0%)
    procent_scazut = max(0, 4 - (4 * (elapsed_time / interval_durata)))
    
    print(f"Procentul actual: {procent_scazut:.2f}%")

    # Obținem comenzile de cumpărare
    close_buy_orders = apitrades.get_trade_orders('buy', symbol, max_age_seconds)
    print(f"BUY ORDERS, {len(close_buy_orders)}")
    
    current_price = api.get_current_price(api.symbol)

    for order in close_buy_orders:
        filled_price = order['price']
        quantity = float(order['qty'])  # Cantitatea

        # Verificăm dacă prețul curent a crescut cu procentul dinamic
        if current_price >= filled_price * (1 + procent_scazut / 100) or utils.are_values_very_close(current_price, filled_price * (1 + procent_scazut / 100)):
            print(f"Prețul curent ({current_price}) este cu {procent_scazut:.2f}% mai mare decât prețul de cumpărare ({filled_price}). Inițiem vânzarea. Cantitate: {quantity}")
            
            # Pornim un fir nou pentru a vinde BTC-ul
            thread = threading.Thread(target=api.place_order, args=("sell", symbol, current_price + 200, quantity))
            thread.start()
            
            # Resetăm timpul global pentru a reporni procesul
            start_time_global = time.time()
            return  # Ieșim din funcție după prima tranzacție
        else:
            print(f"Prețul curent ({current_price}) nu a atins pragul de {procent_scazut:.2f}% față de prețul de cumpărare ({filled_price}).")
    
    # Obținem comenzile de vânzare
    close_sell_orders = apitrades.get_trade_orders('sell', symbol, max_age_seconds)
    sorted_sell_orders = sorted(close_sell_orders, key=lambda x: x['price'])
    close_sell_orders = sorted_sell_orders
    print(f"SELL ORDERS, {len(close_sell_orders)}")
    
    for order in close_sell_orders:
        filled_price = order['price']
        quantity = float(order['qty'])  # Cantitatea

        # Verificăm dacă prețul curent a scăzut cu procentul dinamic
        if current_price <= filled_price * (1 - procent_scazut / 100) or utils.are_values_very_close(current_price, filled_price * (1 - procent_scazut / 100)):
            print(f"Prețul curent ({current_price}) este cu {procent_scazut:.2f}% mai mic decât prețul de vânzare ({filled_price}). Inițiem cumpărarea. Cantitate: {quantity}")
            
            # Pornim un fir nou pentru a cumpăra BTC-ul
            thread = threading.Thread(target=api.place_order, args=("buy", symbol, current_price - 200, quantity))
            thread.start()

            # Resetăm timpul global pentru a reporni procesul
            start_time_global = time.time()
            return  # Ieșim din funcție după prima tranzacție
        else:
            print(f"Prețul curent ({current_price}) nu a atins pragul de {procent_scazut:.2f}% față de prețul de vânzare ({filled_price}).")



import time
trades = []
  
class ProcentDistributor:
    def __init__(self, t1, expired_duration, max_procent, min_procent = 0.008, unitate_timp=60):
        if max_procent < min_procent:
            raise ValueError(f"max_procent ({max_procent}) cannot be smaller than min_procent ({min_procent})")
        self.procent = max_procent #TOTO remove self.
        self.max_procent = max_procent
        self.min_procent = min_procent
        self.t1 = t1
        self.unitate_timp = unitate_timp
        self.expired_duration = expired_duration
        self.update_period_time(t1, self.expired_duration)      
        self.update_max_procent(max(max_procent, min_procent))
        
    def get_procent(self, current_time):
        if current_time < self.t1:
            print(f"get max procent {self.max_procent} because before start time {utils.timestampToTime(self.t1)}")
            return self.max_procent
        if current_time > self.t1 + self.expired_duration:#t2
            print(f"get min procent {self.min_procent} because expiration {self.expired_duration}")
            return max(0, self.min_procent)
        units_passed = (current_time - self.t1) / self.unitate_timp
        #print(f"units_passed: {units_passed} procent_per_unit: {self.procent_per_unit:.2f}")
        return max(self.max_procent - (units_passed * self.procent_per_unit), self.min_procent)
    
    def get_procent_by(self, current_time, current_price, buy_price):
        self.procent = self.calculate_procent_by(current_price, buy_price) #TOTO remove self.
        if current_time < self.t1:
            return self.procent
        if current_time > self.t1 + self.expired_duration:#t2
            return max(0, self.min_procent)
        units_passed = (current_time - self.t1) / self.unitate_timp
        procent_per_unit = self.procent / self.total_units
        return max(self.procent - (units_passed * self.procent_per_unit), self.min_procent)
    
    def update_period_time(self, t1, expired_duration):
        self.t1 = t1
        self.expired_duration = max(expired_duration, 1)
        self.total_units = expired_duration / self.unitate_timp
        #self.update_max_procent(max(max_procent, min_procent))
     
   #don't call from outside class!!
    def update_max_procent(self, procent):
        if procent is not None:
            self.update_period_time(self.t1, self.expired_duration)
            self.max_procent = procent
            self.procent_per_unit = self.max_procent / self.total_units
            #print(f"aici max_procent{self.max_procent} procent_per_unit{self.procent_per_unit:.8f} , total_units{ self.total_units}")
      
    def calculate_procent_by(self, current_price, buy_price):
        price_difference_percentage = ((current_price - buy_price) / buy_price)
        procent_desired_profit = self.max_procent
        procent_desired_profit += price_difference_percentage
        procent_desired_profit = max(procent_desired_profit, self.min_procent) #TODO: review if max
        #print(f"adjusted_init_procent_by: {procent_desired_profit}")
        return procent_desired_profit
        
    def update_tick(self, passs = 0,  half_life_duration=24*60*60) :
        #todo cheama update_period_time inaite
        max_procent = utils.asymptotic_decrease(self.max_procent, self.expired_duration, passs, half_life_duration)
        print(f"max procent from : {self.max_procent:.2f} to {max_procent}")
        self.update_max_procent(max_procent)
        
        
class BuyTransaction:
    def __init__(self, trade_id, qty, buy_price, procent_desired_profit, min_procent, expired_duration, time_trade):
        self.trade_id = trade_id
        self.buy_price = buy_price
        self.t1 = time_trade 
        self.time_trade = time_trade  # Timpul tranzacției de cumpărare sau time.time()
        self.expired_duration = expired_duration
        self.distributor = ProcentDistributor(self.t1, expired_duration, procent_desired_profit, min_procent)
        self.sell_order_id = None
        self.current_time = time.time()
        self.passed = (self.current_time - self.t1) // self.expired_duration
        self.qty = qty

    def get_proposed_sell_price(self, current_price, current_time, days=7):
        print(f"Time away {utils.secondsToHours(current_time - self.time_trade):.2f} h. We are at pass {self.passed}")
         
        if current_time - self.t1 >= self.expired_duration:
            self.passed +=1
            print(f" Updating distrib with new duration {utils.secondsToHours(2 * self.expired_duration):.2f} h.")
            self.t1 = current_time
            self.distributor.update_period_time(current_time, 2 * self.expired_duration)
            self.distributor.update_tick(self.passed, half_life_duration=24*60*60)
            
        if self.passed == 0 :
            price = max(self.buy_price, current_price)
        elif self.passed * self.expired_duration < days * 24 * 60 * 60 : #on profit for x days * 24h
            print(f"Still less than 24h. Use buy price {self.buy_price} as reference")
            price = self.buy_price
        else :
            print(f"Use current price {current_price} as reference")
            price = current_price                   #escape sell no profit after x days * 24h
        
        procent_time_based = self.distributor.get_procent(current_time)
        procent_price_based = self.distributor.get_procent_by(current_time, current_price, self.buy_price)
        print(f"Current Price: {current_price}, Buy Price: {self.buy_price}")
        print(f"Using Time-based Procent versus Price-based Procent: {procent_time_based:.5f}<->{procent_price_based:.5f}")
  
        procent = procent_price_based
        proposed_sell_price = max(price * (1 + procent), current_price * 1.001)
        print(f"Proposed Sell Price Calculation: {proposed_sell_price:.2f} , procent used {procent}")
        
        return proposed_sell_price


def update_trades(trades, symbol, max_age_seconds, procent_desired_profit, expired_duration, min_procent):
    new_trades = apitrades.get_trade_orders('buy', symbol, max_age_seconds)
    #TODO fiter trades care sunt prea recente sub 2 ore
    for trade in new_trades:
        if not any(t.trade_id == trade['id'] for t in trades):
            trades.append(BuyTransaction(
                trade_id=trade['id'],
                qty=trade['qty'],
                buy_price=trade['price'],
                procent_desired_profit=procent_desired_profit,  # Procentul inițial
                min_procent=min_procent,
                expired_duration=expired_duration,  # Durată de 2.7 ore * (3600 secunde)
                time_trade=trade['time'] / 1000  # Convertim timpul din milisecunde în secunde
            ))
    new_trade_ids = {trade['id'] for trade in new_trades}
    trades[:] = [t for t in trades if t.trade_id in new_trade_ids]
    #trades.sort(key=lambda t: t.buy_price)
    trades.sort(key=lambda t: t.buy_price, reverse=True)


def apply_sell_orders(trades, days, force_sell):
    placed_order_count = 0
    total_weighted_price = 0
    total_quantity = 0

      
    current_time = time.time()    
    current_price = api.get_current_price(api.symbol)

    count = 0
    for trade in trades:
        
        print(f"\nTrade {count} ({trade.trade_id})") 
        count+=1
        if trade.sell_order_id and api.check_order_filled(trade.sell_order_id['orderId']):
            print(f"check_order_filled {trade.sell_order_id}")
            trade.sell_order_id = 0  # Marcăm ca executat
        if trade.sell_order_id == 0:
            continue  # Sărim peste tranzacțiile marcate ca executate

        sell_price = trade.get_proposed_sell_price(current_price, current_time, days=days)
        if force_sell: #disperare!!!
            print("\nDISPERARE\n Vand la pretul curent!")
            sell_price = min(sell_price, current_price * 1.001)

        if trade.sell_order_id:
            #print(f"cancel {trade.sell_order_id}")
            api.cancel_order(trade.sell_order_id['orderId'])
            trade.sell_order_id = None

        # Verificăm dacă numărul de ordine a depășit 8
        if placed_order_count < 6:
            new_sell_order_id = api.place_order("sell", symbol, sell_price, trade.qty)
            trade.sell_order_id = new_sell_order_id
            placed_order_count += 1
        else:
            #print(f"Plasare un singur ordin de vazare: Cantitate {trade.qty}, Pret {sell_price}")
            # Adăugăm tranzacția în calculul mediei ponderate
            total_weighted_price += sell_price * trade.qty
            total_quantity += trade.qty
            trade.sell_order_id = None  # Nu plasăm imediat ordinul, dar marcăm ca în proces


    print("\n")
    # Dacă au fost ordine suplimentare, calculăm media ponderată și plasăm un singur ordin
    if total_quantity > 0:
        average_sell_price = total_weighted_price / total_quantity
        print(f"Total: Cantitate {total_quantity}, Pret {average_sell_price}")
        #quantity = min(api.get_asset_info("sell", symbol), total_quantity)
        new_sell_order_id = api.place_order("sell", symbol, average_sell_price, total_quantity)
        #trade.sell_order_id = new_sell_order_id


# Funcția principală care rulează periodic actualizările și cache-ul
def monitor_trades(order_type, symbol, filename, interval=3600, limit=1000, years_to_keep=2):
    while True:
        # Actualizăm fișierul de tranzacții
        apitrades.save_trades_to_file(order_type, symbol, filename, limit=limit, years_to_keep=years_to_keep)
        # Reîncărcăm tranzacțiile în cache
        apitrades.load_trades_from_file(filename)   
        time.sleep(interval)

# Funcția pentru a porni monitorizarea periodică într-un thread separat
def start_monitoring(order_type, symbol, filename, interval=3600, limit=1000, years_to_keep=2):
    monitoring_thread = Thread(target=monitor_trades, args=(order_type, symbol, filename, interval, limit, years_to_keep), daemon=True)
    monitoring_thread.start()



# Cache-ul care va fi actualizat periodic
default_values_sell_recommendation = {
    "BTCUSDT": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7
    },
    "ETHUSDT": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7
    }
}
sell_recommendation = {}

def update_sell_recommendation(file_path):
    global sell_recommendation
    try:
        df = pd.read_csv(file_path)
        sell_recommendation = {
            row['symbol']: {
                'force_sell': row['force_sell'],
                'procent_desired_profit': row['procent_desired_profit'],
                'expired_duration': eval(str(row['expired_duration'])),  # Evaluăm expresiile matematice
                'min_procent': row['min_procent'],
                'days_after_use_current_price': row['days_after_use_current_price']
            } for index, row in df.iterrows()
        }
        print(f"sell_recommendation updated from file! ")
    except FileNotFoundError:
        print(f"Error: File {file_path} not found. Using default values.")
        sell_recommendation = default_values_sell_recommendation
    except Exception as e:
        print(f"Error reading file: {e}. Using default values.")
        sell_recommendation = default_values_sell_recommendation

    # Reprogramăm citirea fișierului la fiecare 2 minute
    Timer(120, update_sell_recommendation, [file_path]).start()
    
def display_sell_recommendation():
    print("Current sell_recommendation content:")
    for symbol, data in sell_recommendation.items():
        print(f"Symbol: {symbol}")
        for key, value in data.items():
            print(f"  {key}: {value}")
        print()
    
max_age_seconds =  3 * 24 * 3600  # Timpul maxim în care ordinele executate/filled sunt considerate recente (3 zile)
interval = 60 * 4 #4 minute
taosymbol = 'TAO'
api.get_binance_symbols(taosymbol)
taosymbol = 'TAOUSDT'
#taosymbol_target_price = api.get_current_price(taosymbol)
#api.place_order("buy", taosymbol, taosymbol_target_price - 10, 1)

        
def main():

    file_path = "sell_recommendation.csv"
    update_sell_recommendation(file_path)
    display_sell_recommendation()
    #monitor_trades(order_type, symbol, filename, interval=3600, limit=1000, years_to_keep=2)

    # Pornim monitorizarea periodică a tranzacțiilor
    start_monitoring(order_type, symbol, filename, interval=interval, limit=1000, years_to_keep=2)
    time.sleep(5)
    
    while True:
        data = sell_recommendation[symbol]
        procent_desired_profit = data['procent_desired_profit']
        expired_duration = data['expired_duration']
        min_procent = data['min_procent']
        
        force_sell = data['force_sell']
        days_after_use_current_price = data['days_after_use_current_price']
        
        close_buy_orders = apitrades.get_trade_orders('buy', symbol, max_age_seconds)
        print(f"get_trade_orders:           Found {len(close_buy_orders)} close 'buy' orders in the last {utils.secondsToDays(max_age_seconds)} days.")
        close_sell_orders = apitrades.get_trade_orders('sell', symbol, max_age_seconds)
        print(f"get_trade_orders:           Found {len(close_sell_orders)} close 'sell' orders in the last {utils.secondsToDays(max_age_seconds)} days.")
        orders = apitrades.get_trade_orders(None, symbol, max_age_seconds)
        print(f"get_trade_orders:           Total found {len(orders)} orders in the last {utils.secondsToDays(max_age_seconds)} day.")
        time.sleep(2)       
    
        update_trades(trades, symbol, max_age_seconds, procent_desired_profit, expired_duration, min_procent)
        apply_sell_orders(trades, days_after_use_current_price, force_sell)
        #monitor_close_orders_by_age2(max_age_seconds)
        time.sleep(10*4)  # Periodic, verificăm ordinele în cache
        
        
if __name__ == "__main__":
    symbol = "BTCUSDT"
    filename = "trades_BTCUSDT.json"
    order_type = None
    main()

    