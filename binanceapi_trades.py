
import time
import datetime
import math
import sys
import json
from datetime import datetime, timedelta

####Binance
#from binance.exceptions import BinanceAPIException

#my imports
import log
import utils as u
import symbols as sym
import binanceapi as api

# 
# Cache global pentru tranzactii
#
trade_cache = []

#######
#######      get_my_trades     #######
#######

def aggregate_trades(trades):
    aggregated_trades = defaultdict(lambda: {
        'symbol': '', 'price': '', 'qty': 0, 'quoteQty': 0, 'commission': 0, 'commissionAsset': '', 'time': 0, 'isBuyer': None, 'isMaker': None, 'isBestMatch': None, 'id': 0
    })
    
    # Grupa tranzactiile pe orderId
    for trade in trades:
        orderId = trade['orderId']
        
        # Agregam datele pentru aceleasi orderId
        aggregated_trades[orderId]['symbol'] = trade['symbol']
        aggregated_trades[orderId]['price'] = trade['price']
        aggregated_trades[orderId]['qty'] += float(trade['qty'])
        aggregated_trades[orderId]['quoteQty'] += float(trade['quoteQty'])
        aggregated_trades[orderId]['commission'] += float(trade['commission'])
        aggregated_trades[orderId]['commissionAsset'] = trade['commissionAsset']
        aggregated_trades[orderId]['time'] = max(aggregated_trades[orderId]['time'], trade['time'])  # selectam timpul maxim
        aggregated_trades[orderId]['isBuyer'] = trade['isBuyer']
        aggregated_trades[orderId]['isMaker'] = trade['isMaker']
        aggregated_trades[orderId]['isBestMatch'] = trade['isBestMatch']
        aggregated_trades[orderId]['id'] = trade['id']  # pastram id-ul primei tranzactii (pentru referinta)

    # Cream lista agregata
    aggregated_list = []
    for aggregated in aggregated_trades.values():
        aggregated_list.append({
            'symbol': aggregated['symbol'],
            'id': aggregated['id'],
            'orderId': 0,  # Setam orderId-ul la 0
            'orderListId': -1,
            'price': aggregated['price'],
            'qty': f"{aggregated['qty']:.8f}",  # pastram formatul cu 8 zecimale
            'quoteQty': f"{aggregated['quoteQty']:.8f}",
            'commission': f"{aggregated['commission']:.8f}",
            'commissionAsset': aggregated['commissionAsset'],
            'time': aggregated['time'],
            'isBuyer': aggregated['isBuyer'],
            'isMaker': aggregated['isMaker'],
            'isBestMatch': aggregated['isBestMatch']
        })

    return aggregated_list
    
def get_my_trades_24(order_type, symbol, days_ago=0, limit=1000):
    
    sym.validate_ordertype(order_type)
    sym.validate_symbols(symbol)
    
    
    all_trades = []
    try:
        current_time = int(time.time() * 1000)
        
        # Calculam start_time si end_time pentru ziua specificata in urma
        end_time = current_time - days_ago * 24 * 60 * 60 * 1000
        start_time = end_time - 24 * 60 * 60 * 1000  # Cu 24 de ore in urma de la end_time

        while start_time < end_time:
            trades = api.client.get_my_trades(symbol=symbol, limit=limit, startTime=start_time, endTime=end_time)

            if not trades:
                break

            if order_type == "BUY":
                filtered_trades = [trade for trade in trades if trade['isBuyer']]
            elif order_type == "SELL":
                filtered_trades = [trade for trade in trades if not trade['isBuyer']]
            else:
                filtered_trades = trades

            all_trades.extend(filtered_trades)
            
            if len(trades) < limit:
                break

            # Ajustam `start_time` la timpul celei mai noi tranzactii pentru a continua
            start_time = trades[-1]['time'] + 1  # Ne mutam inainte cu 1 ms pentru a evita duplicatele
        
        latest_trades = {}
        for trade in all_trades:
            order_id = trade['orderId']
            
            # Verificam daca nu avem deja acest `orderId` sau daca tranzactia curenta este mai recenta
            if order_id not in latest_trades or trade['time'] > latest_trades[order_id]['time']:
                latest_trades[order_id] = trade  # Actualizam cu cea mai recenta tranzactie

        return list(latest_trades.values()) #lista nu dictionar!
        

    except Exception as e:
        print(f"An error occurred: {e}")
        return []



def get_my_trades(order_type, symbol, backdays=3, limit=1000):
    
    sym.validate_ordertype(order_type)
    sym.validate_symbols(symbol)
    
    all_trades = []
    
    try:
        for days_ago in range(backdays + 1):
            print(f"Fetching trades for day {days_ago}...")
            trades = get_my_trades_24(order_type, symbol, days_ago=days_ago, limit=limit)
            
            if not trades:
                # retry from cache .....
                trades = get_trade_orders_24(order_type, symbol, days_ago)
                if not trades:
                    print(f"No trades found for day {days_ago}.")
                    continue
            
            #filtered_trades = [trade for trade in trades if trade['isBuyer'] == (order_type == "BUY")]
            if order_type == "BUY":
                filtered_trades = [trade for trade in trades if trade['isBuyer']]
            elif order_type == "SELL":
                filtered_trades = [trade for trade in trades if not trade['isBuyer']]
            else:
                filtered_trades = trades
                
            all_trades.extend(filtered_trades)

        return all_trades

    except Exception as e:
        print(f"An error occurred: {e}") #3600 * 24 * 7
        return get_trade_orders(order_type, symbol, (backdays + 1) * 24 * 3600 )
        
        
        
def get_my_trades_simple(order_type, symbol, backdays=3, limit=1000):
   
    sym.validate_ordertype(order_type)
    sym.validate_symbols(symbol)
    
    all_trades = []
    try:
        current_time = int(time.time() * 1000) 

        max_interval = 24 * 60 * 60 * 1000

        end_time = current_time

        for day in range(backdays + 1):
            # Calculam start_time pentru ziua curenta in intervalul de 24 de ore
            start_time = end_time - max_interval
            
            trades = api.client.get_my_trades(symbol=symbol, limit=limit, startTime=start_time, endTime=end_time)

            if trades:
                #filtered_trades = [trade for trade in trades if trade['isBuyer'] == (order_type == "BUY")]
                if order_type == "BUY":
                    filtered_trades = [trade for trade in trades if trade['isBuyer']]
                elif order_type == "SELL":
                    filtered_trades = [trade for trade in trades if not trade['isBuyer']]
                else:
                    filtered_trades = trades
                
                all_trades.extend(filtered_trades)
            
            # Actualizam end_time pentru ziua anterioara (inainte de aceasta perioada de 24 de ore)
            end_time = start_time

        return all_trades

    except Exception as e:
        print(f"An error occurred: {e}")
        return []



def test_get_my_trades():
    symbol = 'BTCUSDT'
    limit = 4

    for days_ago in range (0,20):
        print(f"Testing get_my_trades_24 for {symbol} on day {days_ago}...")
        trades = get_my_trades_24(None, symbol, days_ago=days_ago, limit=limit)
        if trades:
            print(f"Found {len(trades)} trades for day {days_ago}.")
            for trade in trades[:10]:  # Afiseaza primele 10 tranzactii
                print(trade)
        else:
            print(f"No trades found for day {days_ago}.")

    backdays = 30
    limit = 10000

    # Testare fara filtrare (fara "BUY" sau "SELL")
    print("Testing get_my_trades with pagination (no order_type)...")
    trades_pagination = get_my_trades(None, symbol, backdays=backdays, limit=limit)

    print("Testing get_my_trades_simple without pagination (no order_type)...")
    trades_simple = get_my_trades_simple(None, symbol, backdays=backdays, limit=limit)

    # Testare pentru "BUY"
    print("Testing get_my_trades with pagination (buy orders)...")
    trades_pagination_buy = get_my_trades("BUY", symbol, backdays=backdays, limit=limit)

    print("Testing get_my_trades_simple without pagination (buy orders)...")
    trades_simple_buy = get_my_trades_simple("BUY", symbol, backdays=backdays, limit=limit)

    # Testare pentru "SELL"
    print("Testing get_my_trades with pagination (sell orders)...")
    trades_pagination_sell = get_my_trades("SELL", symbol, backdays=backdays, limit=limit)

    print("Testing get_my_trades_simple without pagination (sell orders)...")
    trades_simple_sell = get_my_trades_simple("SELL", symbol, backdays=backdays, limit=limit)

    # Comparam rezultatele pentru tranzactiile nefiltrate
    print("\nComparing unfiltered results...")
    if trades_pagination == trades_simple:
        print("Both functions returned the same results for unfiltered trades.")
    else:
        print("The functions returned different results for unfiltered trades.")
        print(f"Trades with pagination: {len(trades_pagination)}")
        print(f"Trades without pagination: {len(trades_simple)}")
        print("Differences found in content for unfiltered trades.")
        for i, (trade_p, trade_s) in enumerate(zip(trades_pagination, trades_simple)):
            if trade_p != trade_s:
                print(f"Difference at trade {i}:")
                print(f"Pagination trade: {trade_p}")
                print(f"Simple trade: {trade_s}")

    # Comparam rezultatele pentru tranzactiile de tip "BUY"
    print("\nComparing buy order results...")
    if trades_pagination_buy == trades_simple_buy:
        print("Both functions returned the same results for buy orders.")
    else:
        print("The functions returned different results for buy orders.")
        print(f"Buy trades with pagination: {len(trades_pagination_buy)}")
        print(f"Buy trades without pagination: {len(trades_simple_buy)}")
        print("Differences found in content for buy trades.")
        for i, (trade_p, trade_s) in enumerate(zip(trades_pagination_buy, trades_simple_buy)):
            if trade_p != trade_s:
                print(f"Difference at trade {i}:")
                print(f"Pagination trade: {trade_p}")
                print(f"Simple trade: {trade_s}")

    # Comparam rezultatele pentru tranzactiile de tip "SELL"
    print("\nComparing sell order results...")
    if trades_pagination_sell == trades_simple_sell:
        print("Both functions returned the same results for sell orders.")
    else:
        print("The functions returned different results for sell orders.")
        print(f"Sell trades with pagination: {len(trades_pagination_sell)}")
        print(f"Sell trades without pagination: {len(trades_simple_sell)}")
        print("Differences found in content for sell trades.")
        for i, (trade_p, trade_s) in enumerate(zip(trades_pagination_sell, trades_simple_sell)):
            if trade_p != trade_s:
                print(f"Difference at trade {i}:")
                print(f"Pagination trade: {trade_p}")
                print(f"Simple trade: {trade_s}")

    # Afisam cateva exemple pentru fiecare caz
    print("\nFirst few trades for unfiltered pagination:")
    for trade in trades_pagination[:5]:
        print(trade)

    print("\nFirst few buy trades with pagination:")
    for trade in trades_pagination_buy[:5]:
        print(trade)

    print("\nFirst few sell trades with pagination:")
    for trade in trades_pagination_sell[:5]:
        print(trade)

# Apelam functia de testare
#test_get_my_trades()

import os

# Functia care salveaza tranzactiile noi in fisier (completare daca exista deja)
def save_trades_to_file(order_type, symbol, filename, limit=1000, years_to_keep=2):
    
    sym.validate_ordertype(order_type)
    sym.validate_symbols(symbol)
    
    all_trades = []

    # Verificam daca fisierul exista deja
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            try:
                existing_trades = json.load(f)
                print(f"Loaded {len(existing_trades)} existing trades from {filename}.")
            except json.JSONDecodeError:
                existing_trades = []
    else:
        existing_trades = []

    # Calculam timpul de la care pastram tranzactiile (doar cele mai recente decat 'years_to_keep' ani)
    current_time_ms = int(time.time() * 1000)
    cutoff_time_ms = current_time_ms - (years_to_keep * 365 * 24 * 60 * 60 * 1000)  # Ani convertiti in milisecunde

    # Eliminam tranzactiile care sunt mai vechi decat perioada dorita
    filtered_existing_trades = [trade for trade in existing_trades if trade['time'] > cutoff_time_ms]
    print(f"Kept {len(filtered_existing_trades)} trades after filtering out old trades (older than {years_to_keep} years).")

    # Daca exista deja tranzactii, gasim cea mai recenta tranzactie salvata
    if filtered_existing_trades:
        most_recent_trade_time = max(trade['time'] for trade in filtered_existing_trades)
        print(f"Most recent trade time from file: {u.timestampToTime(most_recent_trade_time)}")

        # Calculam cate zile au trecut de la most_recent_trade_time pana la acum
        time_diff_ms = current_time_ms - most_recent_trade_time
        backdays = time_diff_ms // (24 * 60 * 60 * 1000) + 1  # Cate zile au trecut de la ultima tranzactie
    else:
        most_recent_trade_time = 0  # Daca nu exista tranzactii, incepem de la 0
        backdays = 60  # Adaugam tranzactii pentru ultimele 60 de zile daca fisierul e gol

    print(f"Fetching trades from the last {backdays} days for {symbol}, order type {order_type}.")

    # Apelam functia pentru a obtine tranzactiile recente doar din perioada lipsa
    new_trades = get_my_trades_simple(order_type, symbol, backdays=backdays, limit=limit)

    # Filtram doar tranzactiile care sunt mai recente decat cea mai recenta tranzactie din fisier
    new_trades = [trade for trade in new_trades if trade['time'] > most_recent_trade_time]

    if new_trades:
        print(f"Found {len(new_trades)} new trades.")
        
        # Adaugam doar tranzactiile noi la cele existente, dar fara cele vechi
        all_trades = filtered_existing_trades + new_trades
        all_trades = sorted(all_trades, key=lambda x: x['time'])  # Sortam dupa timp

        # Salvam doar tranzactiile filtrate si actualizate in fisier
        with open(filename, 'w') as f:
            json.dump(all_trades, f)

        print(f"Updated file with {len(all_trades)} total trades.")
    else:
        print("No new trades found to save.")



def load_trades_from_file(filename):

    global trade_cache

    if os.path.exists(filename):
        with open(filename, 'r') as f:
            try:
                trade_cache = json.load(f)
                print(f"Cache loaded with {len(trade_cache)} trades.")
            except json.JSONDecodeError:
                print("Error reading file.")
                trade_cache = []
    else:
        print(f"File {filename} not found.")
        trade_cache = []

  
# Functia care returneaza tranzactiile de tip "BUY" sau "SELL" din cache pentru un anumit simbol
def get_trade_orders_pt_referinta(order_type, symbol, max_age_seconds):

    sym.validate_ordertype(order_type)
    sym.validate_symbols(symbol)
    
    current_time_ms = int(time.time() * 1000)
    max_age_ms = max_age_seconds * 1000

    filtered_trades = [
        {
            key: (float(value) if isinstance(value, str) and value.replace('.', '', 1).isdigit() else value)
            for key, value in trade.items()
        }
        for trade in trade_cache
        if trade.get('symbol') == symbol
        and (order_type is None or trade.get('isBuyer') == (order_type == "BUY"))  # Verificam doar daca order_type nu este None
        and (current_time_ms - trade.get('time', 0)) <= max_age_ms
    ]

    return filtered_trades

  
# Functia care returneaza tranzactiile de tip "BUY" sau "SELL" din cache pentru un anumit simbol
def get_trade_orders(order_type, symbol, max_age_seconds):
    
    sym.validate_ordertype(order_type)
    sym.validate_symbols(symbol)
    
    current_time_ms = int(time.time() * 1000)
    max_age_ms = max_age_seconds * 1000

    filtered_trades = [
        {
            'symbol': trade['symbol'],
            'id': trade['id'],
            'orderId': trade['orderId'],
            'orderListId': trade['orderListId'],
            'price': float(trade['price']),
            'qty': float(trade['qty']),
            'quoteQty': float(trade['quoteQty']),
            'commission': float(trade['commission']),
            'commissionAsset': trade['commissionAsset'],
            'time': trade['time'],
            'isBuyer': trade['isBuyer'],
            'isMaker': trade['isMaker'],
            'isBestMatch': trade['isBestMatch']
        }
        for trade in trade_cache
        if trade['symbol'] == symbol 
        and (order_type is None or trade['isBuyer'] == (order_type == "BUY"))  # Verifica doar daca order_type nu este None
        and (current_time_ms - trade['time']) <= max_age_ms
    ]

    #  filtered_trades.sort(key=lambda x: x['price'])
    return filtered_trades

    
    # Functia care returneaza tranzactiile de tip "BUY" sau "SELL" din cache pentru un anumit simbol, filtrate pe zile
def get_trade_orders_24(order_type, symbol, days_back):

    sym.validate_ordertype(order_type)
    sym.validate_symbols(symbol)
        
    # Calculam inceputul si sfarsitul zilei dorite (cu days_back zile in urma)
    target_day_start = (datetime.now() - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
    target_day_end = target_day_start.replace(hour=23, minute=59, second=59, microsecond=999999)
    
    # Convertim timpii la timestamp in milisecunde
    start_timestamp = int(target_day_start.timestamp() * 1000)
    end_timestamp = int(target_day_end.timestamp() * 1000)
    
    # Filtram tranzactiile in functie de criteriile specificate
    filtered_trades = [
        {
            key: (float(value) if isinstance(value, str) and value.replace('.', '', 1).isdigit() else value)
            for key, value in trade.items()
        }
        for trade in trade_cache
        if trade.get('symbol') == symbol
        and (order_type is None or trade.get('isBuyer') == (order_type == "BUY"))  # Verificam doar daca order_type nu este None
        and start_timestamp <= trade.get('time', 0) <= end_timestamp
    ]

    # Sortam tranzactiile dupa timp, optional
    # filtered_trades.sort(key=lambda x: x['time'])
    
    return filtered_trades


def format_trade(trade, time_limit):
    trade_time = u.timestampToTime(trade['time'])
    is_within_limit = trade['time'] >= time_limit
    buy_or_sell = "BUY" if trade['isBuyer'] else "SELL"
    return f"Time: {trade_time}, OrderID: {trade['orderId']}, {buy_or_sell}, Price: {trade['price']}, Selected: {is_within_limit}"