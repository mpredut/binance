import os
import sys
import time
import datetime
import json

import pandas as pd

import threading
from threading import Thread,Timer

####Binance
#from binance.client import Client
#from binance.exceptions import BinanceAPIException

#my imports
import symbols as sym
import binanceapi as api
import binanceapi_trades as apitrades
import binanceapi_allorders as apiorders

import utils as u
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
    # Calculul procentului ajustat initial
    procent_adjusted = (procent_defined * (1 - time_fraction)) - (1 - current_price / filled_price)

    # Calculul pretului tinta initial
    target_price = filled_price * (1 + procent_adjusted)
    
    # Daca target_price a ajuns sub current_price, il ajustam
    if target_price < current_price:
        # Definim un dinamic_procent care scade treptat in timp
        dinamic_procent = 0.01 * (1 - time_fraction) + 1  # incepe de la 1.01 si scade catre 1
        target_price = current_price * dinamic_procent
    
    return target_price
 
procent_defined = 0.10  # Procentul initial (10%)
def sell_order_gradually(order, start_time, end_time):

    symbol = sym.btcsymbol
    filled_quantity = order['quantity']
    filled_price = order['price']
    close_order_id = order.get('orderId')
    order_id = None

    initial_interval = 20  # Interval initial de monitorizare (in secunde)
    min_interval = 5       # Interval minim de monitorizare (in secunde)
    total_duration = end_time - start_time  # Durata totala a procesului
    current_time = start_time
    
    #while time.time() < end_time:
    while current_time < end_time:
        #elapsed_time = time.time() - start_time
        elapsed_time = current_time - start_time
        monitor_interval = adjust_monitor_interval(initial_interval, min_interval, total_duration, elapsed_time)
         
        print(f"Monitor interval: {monitor_interval:.2f} seconds")

        current_price = get_current_price(symbol)

        if current_price is None:
            print("Eroare la obtinerea pretului. incerc din nou in cateva secunde.")
            time.sleep(monitor_interval)
            continue

        
        time_fraction = elapsed_time / total_duration
        target_price = calculate_target_price(filled_price, current_price, procent_defined, time_fraction)

        # Calculam pretul propus
        #if current_price > filled_price:
        #    target_price = max(filled_price * 1.01, current_price * 1.01)  # Pret mai mare cu 1%
        #else:
        #    target_price = filled_price * (1 + time_fraction * (current_price / filled_price - 1))

        print(f"Vanzare graduala: target_price={target_price:.2f}, current_price={current_price:.2f}")
        print(f"Elapsed Time: {elapsed_time:.2f} seconds, Target Price: {target_price:.2f} USD")

        # Anulam ordinul anterior inainte de a plasa unul nou
        if order_id:
            if api.check_order_filled(order_id, symbol) :
                return; #order filled!
            api.cancel_order(symbol, order_id)
            print(f"Anulat ordinul anterior cu ID: {order_id}")

        # Plasam ordinul de vanzare
        new_order = api.place_safe_order("SELL", symbol, target_price, filled_quantity)
        if new_order:
            order_id = new_order['orderId']
            print(f"Plasat ordin de vanzare la pretul {target_price:.2f}. New Order ID: {order_id}")
        else:
            print("Eroare la plasarea ordinului de vanzare.")
            order_id = None  # Reseteaza ID-ul ordinului daca plasarea esueaza
        
        # Asteptam un interval ajustat inainte de urmatoarea ajustare
        time.sleep(monitor_interval)
        current_time += monitor_interval



def monitor_filled_buy_orders_old():
    if threading.active_count() > 1:  # Daca sunt deja fire active (in afara de firul principal)
        print("Fire active detectate, iesim din functie pentru a nu porni fire noi.")
        return
 
    maxage_trade_s =  3 * 24 * 3600  # Timpul maxim in care ordinele executate sunt considerate recente (2 ore)
    filled_buy_orders = apiorders.get_recent_filled_orders("BUY", sym.btcsymbol, maxage_trade_s)

    for order in filled_buy_orders:
        current_time = time.time()
        end_time = current_time + 2 * 3600  # Procesul dureaza doua ore
        print("marius")
        print(order)
        # Pornim un fir nou pentru fiecare ordin de cumparare executat recent
        #thread = threading.Thread(target=sell_order_gradually, args=(order, current_time, end_time))
        #thread = threading.Thread(target=sell_order_gradually, args=(order, current_time, end_time, filled_price, current_price, procent_defined))      
        #thread.start()


def get_close_buy_orders_without_sell(api, maxage_trade_s, profit_percentage):
    symbol = sym.btcsymbol
    #close_buy_orders = apitrades.get_trade_orders("BUY", symbol, maxage_trade_s)
    #close_sell_orders = apitrades.get_trade_orders("SELL", symbol, maxage_trade_s)
    close_buy_orders = apiorders.get_trade_orders("BUY", symbol, maxage_trade_s)
    close_sell_orders = apiorders.get_trade_orders("SELL", symbol, maxage_trade_s)
    
    # Lista de ordere "BUY" care nu au un "SELL" asociat cu profitul dorit
    buy_orders_without_sell = []

    for buy_order in close_buy_orders:
        filled_price = buy_order['filled_price']
        symbol = buy_order['symbol']
        buy_quantity = buy_order['quantity']  # Cantitatea cumparata
        
        # Filtreaza orderele de tip "SELL" asociate cu acest "BUY" (acelasi simbol si cu pretul dorit)
        related_sell_orders = [
            order for order in close_sell_orders 
            if order['symbol'] == symbol and order['filled_price'] >= filled_price * (1 + profit_percentage / 100)
        ]
        
        # Calculeaza suma cantitatii vandute pentru orderele "SELL" gasite
        total_sell_quantity = sum(order['quantity'] for order in related_sell_orders)
        
        # Daca cantitatea totala vanduta este mai mica decat cantitatea cumparata
        if total_sell_quantity < buy_quantity:
            # Adauga buy_order la lista de ordere care inca nu au sell complet
            buy_orders_without_sell.append(buy_order)

    return buy_orders_without_sell
    

def monitor_close_orders_by_age1(maxage_trade_s):
    if threading.active_count() > 2:  # Daca sunt deja fire active (in afara de firul principal)
        print("Fire active detectate, iesim din functie pentru a nu porni fire noi.")
        return
 
    symbol = sym.btcsymbol
    #close_buy_orders = apitrades.get_trade_orders("BUY",  symbol, maxage_trade_s)
    close_buy_orders = apiorders.get_trade_orders("BUY",  symbol, maxage_trade_s)

    print(f"BUY ORDERS, {len(close_buy_orders)}")
    current_price = api.get_current_price(symbol)
    for order in close_buy_orders:
        current_time = time.time()
        end_time = current_time + 2 * 3600  # Procesul dureaza doua ore
        filled_price = order['price']
        quantity = float(order['qty']) #quantity

        if current_price >= filled_price * 1.04 or u.are_close(current_price, filled_price * 1.04):  # Daca pretul curent este cu 7% mai mare
            print(f"Pretul curent ({current_price}) este cu 4% mai mare decat pretul de cumparare ({filled_price}). Initiem vanzarea.cantitate{quantity}")
            
            # Pornim un fir nou pentru a vinde BTC-ul
            thread = threading.Thread(target=api.place_safe_order, args=("SELL", symbol, current_price + 200, quantity))
            #sell_order_gradually, args=(order, current_time, end_time))
            thread.start()
            #return
        else:
            print(f"Pretul curent ({current_price}) nu a atins inca pragul de 4% fata de pretul de cumparare ({filled_price}).")
            #return
            
    #close_sell_orders = apitrades.get_trade_orders("SELL",  symbol, maxage_trade_s)
    close_sell_orders = apiorders.get_trade_orders("SELL",  symbol, maxage_trade_s)
    sorted_sell_orders = sorted(close_sell_orders, key=lambda x: x['price'])
    close_sell_orders = sorted_sell_orders
    print(f"SELL ORDERS, {len(close_sell_orders)}")
    for order in close_sell_orders:
        current_time = time.time()
        end_time = current_time + 2 * 3600  # Procesul dureaza doua ore
        filled_price = order['price']
        quantity = float(order['qty']) #quantity

        if current_price <= filled_price * 0.94 or u.are_close(current_price, filled_price * 0.94):  # Daca pretul curent este cu 7% mai mare
            print(f"Pretul curent ({current_price}) este cu 4% mai mic decat pretul de vanzare ({filled_price}). Initiem cumpararea.cantitate{quantity}.")
            
            # Pornim un fir nou pentru a vinde BTC-ul
            thread = threading.Thread(target=api.place_safe_order, args=("BUY", symbol, current_price - 200, quantity))
            #sell_order_gradually, args=(order, current_time, end_time))
            thread.start()
            #return
        else:
            print(f"Pretul curent ({current_price}) nu a atins inca pragul de 4% fata de pretul de vanzare ({filled_price}).")
            #return        



# Variabila globala care stocheaza timpul de inceput al monitorizarii
start_time_global = None

def monitor_close_orders_by_age2(maxage_trade_s):
    global start_time_global
    
    symbol = sym.btcsymbol
    if threading.active_count() > 2:  # Daca sunt deja fire active (in afara de firul principal)
        print("Fire active detectate, iesim din functie pentru a nu porni fire noi.")
        return
    
    # Initializam timpul global la prima executie
    if start_time_global is None:
        start_time_global = time.time()

    # Calculam timpul total scurs de la prima executie a functiei
    current_time = time.time()
    elapsed_time = current_time - start_time_global
    interval_durata = 2 * 3600  # Durata maxima (2 ore)

    # Calculam procentul in functie de timpul scurs (de la 4% pana la 0%)
    procent_scazut = max(0, 4 - (4 * (elapsed_time / interval_durata)))
    
    print(f"Procentul actual: {procent_scazut:.2f}%")

    # Obtinem comenzile de cumparare
    #close_buy_orders = apitrades.get_trade_orders("BUY", symbol, maxage_trade_s)
    close_buy_orders = apiorders.get_trade_orders("BUY", symbol, maxage_trade_s)
    print(f"BUY ORDERS, {len(close_buy_orders)}")
    
    current_price = api.get_current_price(symbol)

    for order in close_buy_orders:
        filled_price = order['price']
        quantity = float(order['qty'])  # Cantitatea

        # Verificam daca pretul curent a crescut cu procentul dinamic
        if current_price >= filled_price * (1 + procent_scazut / 100) or u.are_close(current_price, filled_price * (1 + procent_scazut / 100)):
            print(f"Pretul curent ({current_price}) este cu {procent_scazut:.2f}% mai mare decat pretul de cumparare ({filled_price}). Initiem vanzarea. Cantitate: {quantity}")
            
            # Pornim un fir nou pentru a vinde BTC-ul
            thread = threading.Thread(target=api.place_safe_order, args=("SELL", symbol, current_price + 200, quantity))
            thread.start()
            
            # Resetam timpul global pentru a reporni procesul
            start_time_global = time.time()
            return  # Iesim din functie dupa prima tranzactie
        else:
            print(f"Pretul curent ({current_price}) nu a atins pragul de {procent_scazut:.2f}% fata de pretul de cumparare ({filled_price}).")
    
    # Obtinem comenzile de vanzare
    #close_sell_orders = apitrades.get_trade_orders("SELL", symbol, maxage_trade_s)
    close_sell_orders = apiorders.get_trade_orders("SELL", symbol, maxage_trade_s)
    sorted_sell_orders = sorted(close_sell_orders, key=lambda x: x['price'])
    close_sell_orders = sorted_sell_orders
    print(f"SELL ORDERS, {len(close_sell_orders)}")
    
    for order in close_sell_orders:
        filled_price = order['price']
        quantity = float(order['qty'])  # Cantitatea

        # Verificam daca pretul curent a scazut cu procentul dinamic
        if current_price <= filled_price * (1 - procent_scazut / 100) or u.are_close(current_price, filled_price * (1 - procent_scazut / 100)):
            print(f"Pretul curent ({current_price}) este cu {procent_scazut:.2f}% mai mic decat pretul de vanzare ({filled_price}). Initiem cumpararea. Cantitate: {quantity}")
            
            # Pornim un fir nou pentru a cumpara BTC-ul
            thread = threading.Thread(target=api.place_safe_order, args=("BUY", symbol, current_price - 200, quantity))
            thread.start()

            # Resetam timpul global pentru a reporni procesul
            start_time_global = time.time()
            return  # Iesim din functie dupa prima tranzactie
        else:
            print(f"Pretul curent ({current_price}) nu a atins pragul de {procent_scazut:.2f}% fata de pretul de vanzare ({filled_price}).")



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
            print(f"get max procent {self.max_procent} because before start time {u.timestampToTime(self.t1)}")
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
        max_procent = u.asymptotic_decrease(self.max_procent, self.expired_duration, passs, half_life_duration)
        print(f"max procent from : {self.max_procent:.2f} to {max_procent}")
        self.update_max_procent(max_procent)
        
        
class BuyTransaction:
    def __init__(self, trade_id, qty, buy_price, procent_desired_profit, min_procent, expired_duration, time_trade):
        self.trade_id = trade_id
        self.buy_price = buy_price
        self.t1 = time_trade 
        self.time_trade = time_trade  # Timpul tranzactiei de cumparare sau time.time()
        self.expired_duration = expired_duration
        self.distributor = ProcentDistributor(self.t1, expired_duration, procent_desired_profit, min_procent)
        self.sell_order_id = None
        self.current_time = time.time()
        self.passed = (self.current_time - self.t1) // self.expired_duration
        self.qty = qty

    def get_proposed_sell_price(self, current_price, current_time, days=7):
        print(f"Time away {u.secondsToHours(current_time - self.time_trade):.2f} h. We are at pass {self.passed}")
         
        if current_time - self.t1 >= self.expired_duration:
            self.passed +=1
            print(f" Updating distrib with new duration {u.secondsToHours(2 * self.expired_duration):.2f} h.")
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


def update_trades(trades, symbol, maxage_trade_s, procent_desired_profit, expired_duration, min_procent):
    #new_trades = apitrades.get_trade_orders("BUY", symbol, maxage_trade_s)
    new_trades = apiorders.get_trade_orders("BUY", symbol, maxage_trade_s)
    #TODO fiter trades care sunt prea recente sub 2 ore
    for trade in new_trades:
        if not any(t.trade_id == trade['id'] for t in trades):
            trades.append(BuyTransaction(
                trade_id=trade['id'],
                qty=trade['qty'],
                buy_price=trade['price'],
                procent_desired_profit=procent_desired_profit,  # Procentul initial
                min_procent=min_procent,
                expired_duration=expired_duration,  # Durata de 2.7 ore * (3600 secunde)
                time_trade=trade['time'] / 1000  # Convertim timpul din milisecunde in secunde
            ))
    new_trade_ids = {trade['id'] for trade in new_trades}
    trades[:] = [t for t in trades if t.trade_id in new_trade_ids]
    #trades.sort(key=lambda t: t.buy_price)
    trades.sort(key=lambda t: t.buy_price, reverse=True)


def apply_sell_orders(trades, days, force_sell):
    symbol = sym.btcsymbol

    placed_order_count = 0
    total_weighted_price = 0
    total_quantity = 0

      
    current_time = time.time()    
    current_price = api.get_current_price(symbol)

    count = 0
    for trade in trades:
        
        print(f"\nTrade {count} ({trade.trade_id})") 
        count+=1
        if trade.sell_order_id and api.check_order_filled(trade.sell_order_id['orderId'], symbol):
            print(f"check_order_filled {trade.sell_order_id}")
            trade.sell_order_id = 0  # Marcam ca executat
        if trade.sell_order_id == 0:
            continue  # Sarim peste tranzactiile marcate ca executate

        sell_price = trade.get_proposed_sell_price(current_price, current_time, days=days)
        if force_sell: #disperare!!!
            print("\nDISPERARE\n Vand la pretul curent!")
            sell_price = min(sell_price, current_price * 1.001)

        if trade.sell_order_id:
            #print(f"cancel {trade.sell_order_id}")
            api.cancel_order(symbol, trade.sell_order_id['orderId'])
            trade.sell_order_id = None

        # Verificam daca numarul de ordine a depasit 8
        if placed_order_count < 6:
            new_sell_order_id = api.place_safe_order("SELL", symbol, sell_price, trade.qty)
            trade.sell_order_id = new_sell_order_id
            placed_order_count += 1
        else:
            #print(f"Plasare un singur ordin de vazare: Cantitate {trade.qty}, Pret {sell_price}")
            # Adaugam tranzactia in calculul mediei ponderate
            total_weighted_price += sell_price * trade.qty
            total_quantity += trade.qty
            trade.sell_order_id = None  # Nu plasam imediat ordinul, dar marcam ca in proces


    print("\n")
    # Daca au fost ordine suplimentare, calculam media ponderata si plasam un singur ordin
    if total_quantity > 0:
        average_sell_price = total_weighted_price / total_quantity
        print(f"Total: Cantitate {total_quantity}, Pret {average_sell_price}")
        #quantity = min(api.get_asset_info("SELL", symbol), total_quantity)
        new_sell_order_id = api.place_safe_order("SELL", symbol, average_sell_price, total_quantity)
        #trade.sell_order_id = new_sell_order_id
        



# Functia principala care ruleaza periodic actualizarile si cache-ul
def monitor_trades(filename, interval=3600, limit=1000, years_to_keep=2):
    order_type = None
    while True:
        for symbol in sym.symbols:
            apitrades.save_trades_to_file(order_type, symbol, filename, limit=limit, years_to_keep=years_to_keep)
        
        # Reincarcam tranzactiile in cache
        apitrades.load_trades_from_file(filename)   
        time.sleep(interval)

# Functia pentru a porni monitorizarea periodica intr-un thread separat
def start_monitoring(filename, interval=3600, limit=1000, years_to_keep=2):
    monitoring_thread = Thread(target=monitor_trades, args=(filename, interval, limit, years_to_keep), daemon=True)
    monitoring_thread.start()


def print_number_of_trades(maxage_trade_s):
    print(f"TRADE COUNT")
    for symbol in sym.symbols:
        print(f"For {symbol}")
        close_buy_orders = apitrades.get_trade_orders("BUY", symbol, maxage_trade_s)
        print(f"get_trade_orders:           Found {len(close_buy_orders)} close 'BUY' orders in the last {u.secondsToDays(maxage_trade_s)} days.")

        close_sell_orders = apitrades.get_trade_orders("SELL", symbol, maxage_trade_s)
        print(f"get_trade_orders:           Found {len(close_sell_orders)} close 'SELL' orders in the last {u.secondsToDays(maxage_trade_s)} days.")

        orders = apitrades.get_trade_orders(None, symbol, maxage_trade_s)
        print(f"get_trade_orders:           Total found {len(orders)} orders in the last {u.secondsToDays(maxage_trade_s)} days.")


def print_number_of_orders(maxage_trade_s):
    print(f"ORDER COUNT")
    for symbol in sym.symbols:
        print(f"For {symbol}")
        close_buy_orders = apiorders.get_trade_orders("BUY", symbol, maxage_trade_s)
        print(f"get_trade_orders:           Found {len(close_buy_orders)} close 'BUY' orders in the last {u.secondsToDays(maxage_trade_s)} days.")

        close_sell_orders = apiorders.get_trade_orders("SELL", symbol, maxage_trade_s)
        print(f"get_trade_orders:           Found {len(close_sell_orders)} close 'SELL' orders in the last {u.secondsToDays(maxage_trade_s)} days.")

        orders = apiorders.get_trade_orders(None, symbol, maxage_trade_s)
        print(f"get_trade_orders:           Total found {len(orders)} orders in the last {u.secondsToDays(maxage_trade_s)} days.")




# Cache-ul care va fi actualizat periodic
default_values_sell_recommendation = {
    "BTCUSDC": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7,
        'slope': 0.0,      # Valoare default pentru slope
        'pos': 0,          # Valoare default pentru pos
        'gradient': 0.0,   # Valoare default pentru gradient
        'tick': 0,         # Valoare default pentru tick
        'min': 0.0,        # Valoare default pentru min
        'max': 0.0         # Valoare default pentru max
    },
    "TAOUSDC": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7,
        'slope': 0.0,      # Valoare default pentru slope
        'pos': 0,          # Valoare default pentru pos
        'gradient': 0.0,   # Valoare default pentru gradient
        'tick': 0,         # Valoare default pentru tick
        'min': 0.0,        # Valoare default pentru min
        'max': 0.0         # Valoare default pentru max
    },
    "ETHUSDC": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7,
        'slope': 0.0,      # Valoare default pentru slope
        'pos': 0,          # Valoare default pentru pos
        'gradient': 0.0,   # Valoare default pentru gradient
        'tick': 0,         # Valoare default pentru tick
        'min': 0.0,        # Valoare default pentru min
        'max': 0.0         # Valoare default pentru max
    }
}
sell_recommendation = {}

class StateTracker:
    def __init__(self):
        self.states = {}  # To hold states for each symbol

    def update_sell_recommendation(self, file_path):
        global sell_recommendation
        try:
            df = pd.read_csv(file_path)
            sell_recommendation = {
                row['symbol']: {
                    'force_sell': eval(str(row['force_sell'])),
                    'procent_desired_profit': eval(str(row['procent_desired_profit'])),
                    'expired_duration': eval(str(row['expired_duration'])),  # Evaluam expresiile matematice
                    'min_procent': eval(str(row['min_procent'])),
                    'days_after_use_current_price': eval(str(row['days_after_use_current_price'])),
                    'slope': eval(str(row.get('slope', 0.0))),         # Citire cu valoare default daca nu exista
                    'pos': eval(str(row.get('pos', 0))),               # Citire cu valoare default daca nu exista
                    'gradient': eval(str(row.get('gradient', 0.0))),   # Citire cu valoare default daca nu exista
                    'tick': eval(str(row.get('tick', 0))),             # Citire cu valoare default pentru tick daca nu exista
                    'min': eval(str(row.get('min', 0.0))),             # Citire cu valoare default pentru min daca nu exista
                    'max': eval(str(row.get('max', 0.0)))              # Citire cu valoare default pentru max daca nu exista
                } for index, row in df.iterrows()
            }
            print(f"sell_recommendation updated from file!")
                
            # Update the states based on the current sell_recommendation
            self.update_states_from_sell_recommendation()
        except FileNotFoundError:
            print(f"Error: File {file_path} not found. Using default values.")
            sell_recommendation = default_values_sell_recommendation
        except Exception as e:
            print(f"Error reading file: {file_path}. Error : {e}. Using default values.")
            sell_recommendation = default_values_sell_recommendation

        # Reprogram the update for every 2 minutes
        #Timer(120, self.update_sell_recommendation, [file_path]).start()
        
        t = Timer(120, self.update_sell_recommendation, [file_path])
        t.daemon = True  # Asigură că acest thread nu blochează închiderea procesului
        t.start()



    def update_states_from_sell_recommendation(self):
        for symbol, data in sell_recommendation.items():
            slope = data['slope']
            tick = data['tick']
            min_val = data['min']
            max_val = data['max']
            
            # If the symbol does not exist in the states, initialize it
            if symbol not in self.states:
                self.states[symbol] = []

            # Get the last state for this symbol (if it exists)
            last_state = self.states[symbol][-1] if self.states[symbol] else None

            # Process the state based on slope conditions
            self.process_state(symbol, slope, tick, min_val, max_val, last_state)

    def process_state(self, symbol, slope, tick, min_val, max_val, last_state):
        # If there is no previous state, create a new one
        if last_state is None:
            new_state = {
                'slope': slope,
                'tick': tick,
                'min': min_val,
                'max': max_val
            }
            self.states[symbol].append(new_state)
            return

        # If slope is the same as the last state, update the current state's tick and min/max
        if slope * last_state['slope'] > 0 or (slope == last_state['slope']):  # Au acelasi semn:
            last_state['tick'] = tick
            last_state['min'] = min(last_state['min'], min_val)
            last_state['max'] = max(last_state['max'], max_val)
        else:
            # If slope has changed, create a new state
            new_state = {
                'slope': slope,
                'tick': tick,
                'min': min_val,
                'max': max_val
            }
            self.states[symbol].append(new_state)

    def display_states(self):
        print("Current states:")
        for symbol, states_list in self.states.items():
            print(f"Symbol: {symbol}")
            for i, state in enumerate(states_list):
                print(f"  State {i + 1}:")
                for key, value in state.items():
                    print(f"    {key}: {value}")
            print()


    def display_sell_recommendation(self):
        print("Current sell_recommendation content:")
        for symbol, data in sell_recommendation.items():
            print(f"Symbol: {symbol}")
            for key, value in data.items():
                print(f"  {key}: {value}")
            print()


state_tracker = StateTracker()

# Functie simplificata care verifica daca trendul este de crestere
def is_trend_up(symbol):
    slope = sell_recommendation[symbol]['slope']
    gradient = sell_recommendation[symbol]['gradient']
    return slope > 0 or (slope == 0 and gradient > 0)


def get_relevant_trade(trade_orders, trade_type, threshold_s, symbol):
    if not trade_orders:
        print(f"Warning: No {trade_type} transactions for that currency!!!")
        return None, 0, True
        
    current_time_s = int(time.time())
     
    trade_orders.sort(key=lambda x: x['timestamp'], reverse=True)
    trade_price = float(trade_orders[0]['price'])
    trade_time = float(trade_orders[0]['timestamp']) / 1000  # Timpul în secunde
    print(f"{trade_type.capitalize()} price for {symbol}: {trade_price} at {u.timeToHMS(trade_time)}")
    
    can_trade = True
    if current_time_s - trade_time < threshold_s:
        print(f"Tranzactii de {trade_type.upper()} prea recente."
            f"A trecut doar {u.secondsToHours(current_time_s - trade_time):.2f} h. Astept sa treaca {u.secondsToHours(threshold_s)} h.")
        can_trade = False

    return trade_price, trade_time, can_trade


#//todo: review 0.5
def monitor_price_and_trade(symbol, sbs, maxage_trade_s, gain_threshold=0.07, lost_threshold=0.033):
    #try:
    
    qty = 1    
    threshold_s = 3 * 60 * 60 # 3 h
    current_time_s = int(time.time())
    
    # 1. Obtine ordinele de cumparare si vanzare recente pentru simbol
    #trade_orders_buy = apitrades.get_trade_orders("BUY", symbol, maxage_trade_s)
    #trade_orders_sell = apitrades.get_trade_orders("SELL", symbol, maxage_trade_s)
    trade_orders_buy = apiorders.get_trade_orders("BUY", symbol, maxage_trade_s)
    trade_orders_sell = apiorders.get_trade_orders("SELL", symbol, maxage_trade_s)
    if not (trade_orders_buy or trade_orders_sell):
        print(f"No trade orders found for {symbol} in the last {maxage_trade_s} seconds.")
        return 
    buy_price, buy_time, can_buy = get_relevant_trade(trade_orders_buy, "BUY", threshold_s, symbol)
    sell_price, sell_time, can_sell = get_relevant_trade(trade_orders_sell, "SELL", threshold_s, symbol)

    threshold_all_s = 1 * 60 * 60 # 1 h
    if current_time_s - max(buy_time, sell_time)  < threshold_all_s:
        print(f"Trades too ... recente."
            f"Pass only {u.secondsToHours(current_time_s -  max(buy_time, sell_time)):.2f} h. Wait to pass {u.secondsToHours(threshold_all_s)} h.")
        can_trade = False
        
    
    # 2. Obtine pretul curent de pe piata
    current_price = api.get_current_price(symbol)
    print(f"Current price for {symbol}: {current_price}")

    # 3. Verifica ordinele de cumparare
    if trade_orders_buy:
        if not buy_price:
            print(f"No buy_price !!!!!")
            return
        price_increase = (current_price - buy_price) / buy_price
        price_decrease = (buy_price - current_price) / buy_price

        print(f"(increase: {price_increase * 100}%, decrease: {price_decrease * 100}%)")
        # 3.1. Verifica daca trebuie sa plasezi un ordin de vanzare
        if price_increase > gain_threshold or u.are_close(price_increase, gain_threshold, target_tolerance_percent=1.0):
            if not is_trend_up(symbol):
                print(f"Price increased with {price_increase * 100}% by more than {gain_threshold * 100}% versus buy price and not trend up!")
                if can_sell:
                    api.place_order_smart("SELL", symbol, current_price, 
                        qty, safeback_seconds=sbs, force=False, cancelorders=True, hours=2, pair=False)
                else:
                    print("No can sell")
                #api.place_SELL_order(symbol, current_price, qty)
                #api.place_order_smart("BUY", sym.btcsymbol, proposed_price, 0.017, safeback_seconds=16*3600+60,
                #    force=True, cancelorders=True, hours=1)
            else :
                print(f"No action taken, because trend is up!")
        elif price_decrease > lost_threshold or u.are_close(price_decrease, lost_threshold, target_tolerance_percent=1.0):
            if not is_trend_up(symbol):
                print(f"Price decreased with {price_decrease * 100}% by more than {lost_threshold * 100}% versus buy price and not trend up!")
                if can_sell:
                    api.place_order_smart("SELL", symbol, current_price, 
                        qty, safeback_seconds=sbs, force=False, cancelorders=True, hours=2, pair=True)
                #api.place_SELL_order(symbol, current_price, qty)
                else:
                    print("No can sell")
            else:
                print(f"No action taken, because trend is up!")
        else:
            print(f"Nothing interesting")

    # 4. Verifica ordinele de vanzare
    if trade_orders_sell:     
        if not sell_price:
            print(f"No sell_price !!!!!")
        return
        price_decrease_versus_sell = (sell_price - current_price) / sell_price
        print(f"(price_decrease_versus_sell: {price_decrease_versus_sell * 100}%)")
        if price_decrease_versus_sell > gain_threshold or u.are_close(price_decrease_versus_sell, gain_threshold, target_tolerance_percent=1.0):
            if is_trend_up(symbol):
                print(f"Price decreased with {price_decrease_versus_sell * 100}% by more than {gain_threshold * 100}% versus sell price: Placing buy order")
                #api.cancel_orders_old_or_outlier("BUY", "BTCUSDT", qty, hours=0.5, price_difference_percentage=0.1)
                if can_buy:
                    api.place_order_smart("BUY", symbol, current_price + 0.5, 
                        qty, safeback_seconds=sbs, cancelorders=True, hours=48, pair=False)
                else:
                   print("No can buy")
            else :
                print(f"No action taken, because trend is down!")

    return

    #except Exception as e:
    #    print(f"An error occurred while monitoring the price: {e}")

def main():             
    #api.place_SELL_order_at_market("BTCUSDT", 0.017)
    #return
  
    filename = "trades.json" 
    
    maxage_trade_s =  4 * 24 * 3600  # Timpul maxim in care ordinele executate/filled sunt considerate recente (3 zile)
    interval = 60 * 4 #4 minute

    #api.get_binance_symbols(sym.taosymbol)

    file_path = "sell_recommendation.csv"
    state_tracker.update_sell_recommendation(file_path)
    state_tracker.display_sell_recommendation()
    #monitor_trades(order_type, sym.symbol, filename, interval=3600, limit=1000, years_to_keep=01)

    # Pornim monitorizarea periodica a tranzactiilor
    #start_monitoring(filename, interval=interval, limit=1000, years_to_keep=0.09)
    time.sleep(5)

    #for i in range(0, 5):
    #close_sell_orders = apitrades.get_trade_orders("SELL", sym.taosymbol, maxage_trade_s)
    close_sell_orders = apiorders.get_trade_orders("SELL", sym.taosymbol, maxage_trade_s)
    print(f"get_trade_orders:           Found {len(close_sell_orders)} close 'SELL' orders in the last {u.secondsToDays(maxage_trade_s)} days.")
    close_buy_orders = apiorders.get_trade_orders("BUY", sym.taosymbol, maxage_trade_s)
    print(f"get_trade_orders:           Found {len(close_buy_orders)} close 'BUY' orders in the last {u.secondsToDays(maxage_trade_s)} days.")
    print(f"close_buy_orders {close_buy_orders}")
    print(f"close_sell_orders {close_sell_orders}")
    
    #return
    
    #taosymbol_target_price = api.get_current_price(sym.taosymbol)
    #api.place_safe_order("BUY", sym.taosymbol, taosymbol_target_price - 10, 1)

    d = 14
    while True:

        #state_tracker.display_states()
        print_number_of_orders(maxage_trade_s)
        print_number_of_trades(maxage_trade_s)
        
        print("-----BTC------")
        monitor_price_and_trade(sym.btcsymbol, sbs=d*24*3600+60, maxage_trade_s=3600*24*7)
        #print("-----TAOUSDT------")
        #monitor_price_and_trade(sym.taosymbol,sbs=d*24*3600+60, maxage_trade_s=3600*24*17, gain_threshold=0.092, lost_threshold=0.049)
        print("-----TAOUSDC------")
        monitor_price_and_trade(sym.taosymbol,sbs=d*24*3600+60, maxage_trade_s=3600*24*17, gain_threshold=0.092, lost_threshold=0.049)
        print("--------------")
  
        data = sell_recommendation[sym.btcsymbol]
        procent_desired_profit = data['procent_desired_profit']
        expired_duration = data['expired_duration']
        min_procent = data['min_procent']
        force_sell = data['force_sell']
        days_after_use_current_price = data['days_after_use_current_price']      
        
        #update_trades(trades, sym.btcsymbol, maxage_trade_s, procent_desired_profit, expired_duration, min_procent)
        #apply_sell_orders(trades, days_after_use_current_price, force_sell)
        #monitor_close_orders_by_age2(maxage_trade_s)
        time.sleep(60*0.8)  # Astept 1.8 minute.
        
        
def test() :
    filename="trades.json"
    limit=1000
    years_to_keep=0.09
    order_type=None
    #for symbol in sym.symbols:
    #    apitrades.save_trades_to_file(order_type, symbol, filename, limit=limit, years_to_keep=years_to_keep)
    
    apitrades.save_trades_to_file(order_type, sym.taosymbol, filename, limit=limit, years_to_keep=years_to_keep)
    apitrades.load_trades_from_file(filename)
    #trade_orders_buy = apitrades.get_trade_orders(None, sym.taosymbol, 24 * 60 * 60 * 11)
    #trade_orders_buy = apiorders.get_trade_orders(None, sym.taosymbol, 24 * 60 * 60 * 11)
    trade_orders_buy = apiorders.get_trade_orders(None, sym.taosymbol, 24 * 60 * 60 * 11)
    trade_orders_buy = apiorders.get_trade_orders(None, sym.taosymbol, 24 * 60 * 60 * 11)
    
    print(f"{len(trade_orders_buy)}, {trade_orders_buy}")
    sys.exit(1)
    
if __name__ == "__main__":
    
     main()
    #test()
    # try:
        # main()
    # except Exception as e:
        # print(f"Eroare capturata: {e}")
    # finally:
        # print("Fortare inchidere...")
        # sys.exit(1)  # opreste toate daemon threads
    