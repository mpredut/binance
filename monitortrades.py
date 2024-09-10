import json
import os
import time
from threading import Thread
from datetime import datetime
import threading

####Binance
#from binance.client import Client
#from binance.exceptions import BinanceAPIException

#my imports
import binanceapi as api
import binanceapi_trades as apitrades
import binanceapi_allorders as apiorders

import utils

# Funcția principală care rulează periodic actualizările și cache-ul
def monitor_trades(order_type, symbol, filename, interval=3600, limit=1000, years_to_keep=2):
    while True:
        # Actualizăm fișierul de tranzacții
        apitrades.save_trades_to_file(order_type, symbol, filename, limit=limit, years_to_keep=years_to_keep)
        
        # Reîncărcăm tranzacțiile în cache
        apitrades.load_trades_from_file(filename)
        
        # Așteptăm intervalul configurat înainte de a repeta procesul
        time.sleep(interval)

# Funcția pentru a porni monitorizarea periodică într-un thread separat
def start_monitoring(order_type, symbol, filename, interval=3600, limit=1000, years_to_keep=2):
    monitoring_thread = Thread(target=monitor_trades, args=(order_type, symbol, filename, interval, limit, years_to_keep), daemon=True)
    monitoring_thread.start()



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
        self.unitate_timp = unitate_timp
        self.update_period_time(t1, expired_duration)      
        self.update_max_procent(max(max_procent, min_procent))
        
    def get_procent(self, current_time):
        if current_time < self.t1:
            return self.max_procent
        if current_time > self.t2:
            print(f"current_time {current_time} > self.t2 {self.t2}")
            return max(0, self.min_procent)
        units_passed = (current_time - self.t1) / self.unitate_timp
        print(f"units_passed: {units_passed}")
        print(f"procent_per_unit: {self.procent_per_unit}")
        return max(self.max_procent - (units_passed * self.procent_per_unit), self.min_procent)
    
    def get_procent_by(self, current_time, current_price, buy_price):
        if current_time < self.t1:
            return self.procent
        if current_time > self.t2:
            return max(0, self.min_procent)
        self.procent = self.calculate_procent_by(current_price, buy_price) #TOTO remove self.
        units_passed = (current_time - self.t1) / self.unitate_timp
        procent_per_unit = self.procent / self.total_units
        return max(self.procent - (units_passed * self.procent_per_unit), self.min_procent)
    
    def update_period_time(self, t1, expired_duration):
        self.t1 = t1
        self.t2 = self.t1 + max(expired_duration, 1)
        self.total_units = (self.t2 - self.t1) / self.unitate_timp
   
    def update_max_procent(self, procent):
        if procent is not None:
            self.max_procent = procent
            self.procent_per_unit = self.max_procent / self.total_units
      
    def calculate_procent_by(self, current_price, buy_price):
        price_difference_percentage = ((current_price - buy_price) / buy_price)
        procent_desired_profit = self.max_procent
        procent_desired_profit += price_difference_percentage
        procent_desired_profit = max(procent_desired_profit, self.min_procent) #TODO: review if max
        print(f"adjust_init_procent_by: {procent_desired_profit}")
        return procent_desired_profit
        
        
class BuyTransaction:
    def __init__(self, trade_id, qty, buy_price, procent_desired_profit, expired_duration, time_trade):
        self.trade_id = trade_id
        self.qty = qty
        self.buy_price = buy_price
        self.t1 = time.time()#time_trade  # Timpul tranzacției de cumpărare
        self.expired_duration = expired_duration
        self.distributor = ProcentDistributor(self.t1, expired_duration, procent_desired_profit)
        self.sell_order_id = None

    def get_proposed_sell_price(self, current_price, current_time):
        if current_time - self.t1 >= self.expired_duration:
            print(f"Time expired. Updating distributor period for current_time {current_time} with duration {self.expired_duration}.")
            self.distributor.update_period_time(current_time, self.expired_duration)
        
        price = max(self.buy_price, current_price)
        
        procent_time_based = self.distributor.get_procent(current_time)
        procent_price_based = self.distributor.get_procent_by(current_time, current_price, self.buy_price)
        
        proposed_sell_price = price * (1 + procent_time_based / 100)
        
        print(f"Current Price: {current_price}, Buy Price: {self.buy_price}")
        print(f"Using Time-based Procent versus Price-based Procent: {procent_time_based:.5f}<->{procent_price_based:.5f}")
        print(f"Proposed Sell Price Calculation: {proposed_sell_price:.2f}")
        
        return proposed_sell_price


def update_trades(trades, symbol, max_age_seconds):
    new_trades = apitrades.get_trade_orders('buy', symbol, max_age_seconds)
    for trade in new_trades:
        if not any(t.trade_id == trade['id'] for t in trades):
            trades.append(BuyTransaction(
                trade_id=trade['id'],
                qty=trade['qty'],
                buy_price=trade['price'],
                procent_desired_profit=0.07,  # Procentul inițial
                expired_duration=2*3600,  # Durată de 2 ore 2 * (3600 secunde)
                time_trade=trade['time'] / 1000  # Convertim timpul din milisecunde în secunde
            ))
    new_trade_ids = {trade['id'] for trade in new_trades}
    trades[:] = [t for t in trades if t.trade_id in new_trade_ids]
    #trades.sort(key=lambda t: t.buy_price)
    trades.sort(key=lambda t: t.buy_price, reverse=True)


def apply_sell_orders(trades, current_price, current_time, expired_duration, procent_desired_profit, symbol):
    placed_order_count = 0
    total_weighted_price = 0
    total_quantity = 0

    for trade in trades:
            
        if trade.sell_order_id and api.check_order_filled(trade.sell_order_id['orderId']):
            print(f"check_order_filled {trade.sell_order_id}")
            trade.sell_order_id = 0  # Marcăm ca executat
        if trade.sell_order_id == 0:
            continue  # Sărim peste tranzacțiile marcate ca executate

        sell_price = trade.get_proposed_sell_price(current_price, current_time)

        if trade.sell_order_id:
            #print(f"cancel {trade.sell_order_id}")
            api.cancel_order(trade.sell_order_id['orderId'])
            trade.sell_order_id = None

        # Verificăm dacă numărul de ordine a depășit 8
        if placed_order_count < 6:
            print(f"Plasare ordin de vanzare: Cantitate {trade.qty}, Preț {sell_price}")
            new_sell_order_id = api.place_order("sell", symbol, sell_price, trade.qty)
            trade.sell_order_id = new_sell_order_id
            placed_order_count += 1
        else:
            #print(f"Plasare un singur ordin de vazare: Cantitate {trade.qty}, Pret {sell_price}")
            # Adăugăm tranzacția în calculul mediei ponderate
            total_weighted_price += sell_price * trade.qty
            total_quantity += trade.qty
            trade.sell_order_id = None  # Nu plasăm imediat ordinul, dar marcăm ca în proces


    # Dacă au fost ordine suplimentare, calculăm media ponderată și plasăm un singur ordin
    if total_quantity > 0:
        average_sell_price = total_weighted_price / total_quantity
        quantity = min(api.get_asset_info("sell", symbol), total_quantity)
        print(f"Total: Cantitate {quantity}, Pret {average_sell_price}")
        new_sell_order_id = api.place_order("sell", symbol, average_sell_price, quantity)
        #trade.sell_order_id = new_sell_order_id


max_age_seconds =  3 * 24 * 3600  # Timpul maxim în care ordinele executate/filled sunt considerate recente (3 zile)
# Exemplu de apel pentru a porni monitorizarea periodică

def main():


    # Pornim monitorizarea periodică a tranzacțiilor
    start_monitoring(order_type, symbol, filename, interval=interval, limit=1000, years_to_keep=2)

    # Simulare: extragem ordinele recente de tip 'buy'
    while True:
        time.sleep(10*2)  # Periodic, verificăm ordinele în cache
        #max_age_seconds = 86400 *8
        close_buy_orders = apitrades.get_trade_orders('buy', symbol, max_age_seconds)  # Extragere ordine de 'buy' în ultimele 24 de ore
        print(f"get_trade_orders:           Found {len(close_buy_orders)} close 'buy' orders in the last {utils.convert_seconds_to_days(max_age_seconds)} days.")
        close_sell_orders = apitrades.get_trade_orders('sell', symbol, max_age_seconds)  # Extragere ordine de 'buy' în ultimele 24 de ore
        print(f"get_trade_orders:           Found {len(close_sell_orders)} close 'sell' orders in the last {utils.convert_seconds_to_days(max_age_seconds)} days.")
        #close_orders_all = apiorders.get_recent_filled_orders('buy', symbol, max_age_seconds)  # Extragere ordine de 'buy' în ultimele 24 de ore
        #print(f"get_recent_filled_orders:   Found {len(close_orders_all)} close 'buy' orders in the last 24 hours.")
        #print(close_orders)
        #print(close_orders_all)
        # Pasul 1: Obține ordinele din ultimele 24 de ore
        orders = apitrades.get_trade_orders(None, symbol, 60 * 60 * 24)
        print(f"get_trade_orders:           Found {len(orders)} orders in the last {utils.convert_seconds_to_days(60 * 60 * 24)} day.")
                
        procent_desired_profit = 0.07 #0.7%
        current_time = time.time()    
        current_price = api.get_current_price(api.symbol)
        update_trades(trades, symbol, max_age_seconds)
        expired_duration = 3600 * 2.5 #h
        apply_sell_orders(trades, current_price, current_time, expired_duration, procent_desired_profit, api.symbol)
        #monitor_close_orders_by_age2(max_age_seconds)
        
        
if __name__ == "__main__":
    symbol = "BTCUSDT"
    filename = "trades_BTCUSDT.json"
    order_type = None
    interval = 3600/2  # 1 oră
    main()

    