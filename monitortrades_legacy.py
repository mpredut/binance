# cod legacy mutat din monitortrades.py pe 16 iun 2026, pastrat ca referinta -- NU e importat/folosit
# de calea activa. Botul live traieste integral in monitortrades.py. Singura referinta externa este
# tests/testdistributor.py (test deja invechit fata de semnatura actuala a ProcentDistributor).
# Importurile de mai jos exista doar ca fisierul sa fie parsabil/rulabil standalone ca referinta.

import sys
import time
import threading
from threading import Thread, Timer

import symbols as sym
from binance_api import bapi as api
from binance_api import bapi_placeorder as po
from binance_api import bapi_trades as apitrades
from binance_api import bapi_allorders as apiorders
import utils as u


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
        new_order = po.place_safe_order("SELL", symbol, target_price, filled_quantity)
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
 
    maxage_trade_s =  3 * 24 * 3600  # Câtă vechime maximă au ordinele considerate „recente"
    # get_recent_filled_orders așteaptă o DURATĂ (secunde), nu un timestamp absolut.
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
            thread = threading.Thread(target=po.place_safe_order,
                name="sell_monitor_close_orders_by_age1",
                args=("SELL", symbol, current_price + 200, quantity))
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
            thread = threading.Thread(target=po.place_safe_order,
                name="buy_monitor_close_orders_by_age1",
                args=("BUY", symbol, current_price - 200, quantity))
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
            thread = threading.Thread(target=po.place_safe_order,
                name="monitor_close_orders_by_age2",
                args=("SELL", symbol, current_price + 200, quantity))
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
            thread = threading.Thread(target=po.place_safe_order, 
            name="monitor_close_orders_by_age2",
            args=("BUY", symbol, current_price - 200, quantity))
            thread.start()

            # Resetam timpul global pentru a reporni procesul
            start_time_global = time.time()
            return  # Iesim din functie dupa prima tranzactie
        else:
            print(f"Pretul curent ({current_price}) nu a atins pragul de {procent_scazut:.2f}% fata de pretul de vanzare ({filled_price}).")



import time
trades = []

class ProcentDistributor:

    def __init__(self, start_time, expired_duration, max_procent, min_procent=0.008, unitate_timp=60, momentum_weight=0.5):
        if min_procent < 0 or max_procent < min_procent:
            raise ValueError("Invalid procent values")

        self.start_time = start_time
        self.expired_duration = max(1, expired_duration)
        self.unitate_timp = max(1, unitate_timp)

        self.initial_max_procent = max_procent
        self.max_procent = max_procent
        self.min_procent = min_procent
        self.momentum_weight = momentum_weight

        self.total_units = max(1, self.expired_duration / self.unitate_timp)
        self.update_decay()

    def update_decay(self):
        self.procent_per_unit = (self.max_procent - self.min_procent) / self.total_units

    def get_time_based_procent(self, current_time):

        if current_time <= self.start_time:
            return self.max_procent

        elapsed = current_time - self.start_time

        if elapsed >= self.expired_duration:
            return self.min_procent

        units_passed = elapsed / self.unitate_timp
        decayed = self.max_procent - units_passed * self.procent_per_unit

        return max(decayed, self.min_procent)

    def get_market_adjustment(self, current_price, buy_price):

        if buy_price <= 0:
            return 0

        price_change = (current_price - buy_price) / buy_price
        return -price_change * self.momentum_weight

    def get_final_procent(self, current_time, current_price, buy_price):

        base = self.get_time_based_procent(current_time)
        adjustment = self.get_market_adjustment(current_price, buy_price)

        return max(base + adjustment, self.min_procent)

    def update_tick(self, passed=0, half_life_duration=24*60*60):

        if passed <= 0:
            return

        decay_factor = 0.5 ** (passed * self.expired_duration / half_life_duration)

        self.max_procent = max(
            self.initial_max_procent * decay_factor,
            self.min_procent
        )

        self.update_decay()


class BuyTransaction:

    def __init__(self, trade_id, qty, buy_price, procent_desired_profit, min_procent, expired_duration, time_trade):

        self.trade_id = trade_id
        self.qty = qty
        self.buy_price = buy_price
        self.time_trade = time_trade
        self.expired_duration = expired_duration
        self.sell_order_id = None

        self.distributor = ProcentDistributor(
            start_time=time_trade,
            expired_duration=expired_duration,
            max_procent=procent_desired_profit,
            min_procent=min_procent,
        )

    def get_passed_cycles(self, current_time):
        return int((current_time - self.time_trade) // self.expired_duration)

    def get_reference_price(self, current_price, current_time, days=7):

        elapsed = current_time - self.time_trade
        passed_cycles = self.get_passed_cycles(current_time)

        if passed_cycles == 0:
            return max(self.buy_price, current_price)

        if elapsed < days * 24 * 60 * 60:
            return self.buy_price

        return current_price

    def get_proposed_sell_price(self, current_price, current_time, days=7):

        passed_cycles = self.get_passed_cycles(current_time)

        self.distributor.update_tick(
            passed=passed_cycles,
            half_life_duration=24*60*60
        )

        reference_price = self.get_reference_price(current_price, current_time, days)

        procent = self.distributor.get_final_procent(
            current_time,
            current_price,
            self.buy_price
        )

        return max(
            reference_price * (1 + procent),
            current_price * 1.001
        )


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
            new_sell_order_id = po.place_safe_order("SELL", symbol, sell_price, trade.qty)
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
        new_sell_order_id = po.place_safe_order("SELL", symbol, average_sell_price, total_quantity)
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
    monitoring_thread = Thread(
        target=monitor_trades,
        name="monitor_trades",
        args=(filename, interval, limit, years_to_keep),
        daemon=True
    )
    monitoring_thread.start()


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
