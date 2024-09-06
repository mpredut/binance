
import time
import datetime
import math
import sys
import json

####Binance
from binance.client import Client
from binance.exceptions import BinanceAPIException

#my imports
from binanceapi import client

# 
# Cache global pentru tranzacții
#
trade_cache = []

#######
#######      get_my_trades     #######
#######

def get_my_trades_24(symbol, days_ago, order_type=None, limit=1000):
    all_trades = []
    try:
        current_time = int(time.time() * 1000)
        
        # Calculăm start_time și end_time pentru ziua specificată în urmă
        end_time = current_time - days_ago * 24 * 60 * 60 * 1000
        start_time = end_time - 24 * 60 * 60 * 1000  # Cu 24 de ore în urmă de la end_time

        while start_time < end_time:
            trades = client.get_my_trades(symbol=symbol, limit=limit, startTime=start_time, endTime=end_time)

            if not trades:
                break

            if order_type == "buy":
                filtered_trades = [trade for trade in trades if trade['isBuyer']]
            elif order_type == "sell":
                filtered_trades = [trade for trade in trades if not trade['isBuyer']]
            else:
                filtered_trades = trades

            all_trades.extend(filtered_trades)
            
            if len(trades) < limit:
                break

            # Ajustăm `start_time` la timpul celei mai noi tranzacții pentru a continua
            start_time = trades[-1]['time'] + 1  # Ne mutăm înainte cu 1 ms pentru a evita duplicatele
            
        return all_trades

    except Exception as e:
        print(f"An error occurred: {e}")
        return []



def get_my_trades(order_type, symbol, backdays=3, limit=1000):
    all_trades = []
    
    try:
        for days_ago in range(backdays):
            print(f"Fetching trades for day {days_ago}...")
            trades = get_my_trades_24(symbol, days_ago, limit)
            
            if not trades:
                print(f"No trades found for day {days_ago}.")
                continue
            
            #filtered_trades = [trade for trade in trades if trade['isBuyer'] == (order_type == "buy")]
            if order_type == "buy":
                filtered_trades = [trade for trade in trades if trade['isBuyer']]
            elif order_type == "sell":
                filtered_trades = [trade for trade in trades if not trade['isBuyer']]
            else:
                filtered_trades = trades
                
            all_trades.extend(filtered_trades)

        return all_trades

    except Exception as e:
        print(f"An error occurred: {e}")
        return []
        
        
def get_my_trades_simple(order_type, symbol, backdays=3, limit=1000):
    all_trades = []
    try:
        current_time = int(time.time() * 1000) 

        max_interval = 24 * 60 * 60 * 1000

        end_time = current_time

        for day in range(backdays):
            # Calculăm start_time pentru ziua curentă în intervalul de 24 de ore
            start_time = end_time - max_interval
            
            trades = client.get_my_trades(symbol=symbol, limit=limit, startTime=start_time, endTime=end_time)

            if trades:
                #filtered_trades = [trade for trade in trades if trade['isBuyer'] == (order_type == "buy")]
                if order_type == "buy":
                    filtered_trades = [trade for trade in trades if trade['isBuyer']]
                elif order_type == "sell":
                    filtered_trades = [trade for trade in trades if not trade['isBuyer']]
                else:
                    filtered_trades = trades
                
                all_trades.extend(filtered_trades)
            
            # Actualizăm end_time pentru ziua anterioară (înainte de această perioadă de 24 de ore)
            end_time = start_time

        return all_trades

    except Exception as e:
        print(f"An error occurred: {e}")
        return []



def test_get_my_trades():
    symbol = 'BTCUSDT'
    limit = 2

    for days_ago in range (0,20):
        print(f"Testing get_my_trades_24 for {symbol} on day {days_ago}...")
        trades = get_my_trades_24(symbol, days_ago, limit)
        if trades:
            print(f"Found {len(trades)} trades for day {days_ago}.")
            for trade in trades[:10]:  # Afișează primele 10 tranzacții
                print(trade)
        else:
            print(f"No trades found for day {days_ago}.")

    backdays = 30
    limit = 2

    # Testare fără filtrare (fără 'buy' sau 'sell')
    print("Testing get_my_trades with pagination (no order_type)...")
    trades_pagination = get_my_trades(None, symbol, backdays=backdays, limit=limit)

    print("Testing get_my_trades_simple without pagination (no order_type)...")
    trades_simple = get_my_trades_simple(None, symbol, backdays=backdays, limit=limit)

    # Testare pentru 'buy'
    print("Testing get_my_trades with pagination (buy orders)...")
    trades_pagination_buy = get_my_trades("buy", symbol, backdays=backdays, limit=limit)

    print("Testing get_my_trades_simple without pagination (buy orders)...")
    trades_simple_buy = get_my_trades_simple("buy", symbol, backdays=backdays, limit=limit)

    # Testare pentru 'sell'
    print("Testing get_my_trades with pagination (sell orders)...")
    trades_pagination_sell = get_my_trades("sell", symbol, backdays=backdays, limit=limit)

    print("Testing get_my_trades_simple without pagination (sell orders)...")
    trades_simple_sell = get_my_trades_simple("sell", symbol, backdays=backdays, limit=limit)

    # Comparăm rezultatele pentru tranzacțiile nefiltrate
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

    # Comparăm rezultatele pentru tranzacțiile de tip 'buy'
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

    # Comparăm rezultatele pentru tranzacțiile de tip 'sell'
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

    # Afișăm câteva exemple pentru fiecare caz
    print("\nFirst few trades for unfiltered pagination:")
    for trade in trades_pagination[:5]:
        print(trade)

    print("\nFirst few buy trades with pagination:")
    for trade in trades_pagination_buy[:5]:
        print(trade)

    print("\nFirst few sell trades with pagination:")
    for trade in trades_pagination_sell[:5]:
        print(trade)

# Apelăm funcția de testare
test_get_my_trades()

import os

# Funcția care salvează tranzacțiile noi în fișier (completare dacă există deja)
def save_trades_to_file(order_type, symbol, filename, limit=1000, years_to_keep=2):
    all_trades = []

    # Verificăm dacă fișierul există deja
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            try:
                existing_trades = json.load(f)
                print(f"Loaded {len(existing_trades)} existing trades from {filename}.")
            except json.JSONDecodeError:
                existing_trades = []
    else:
        existing_trades = []

    # Calculăm timpul de la care păstrăm tranzacțiile (doar cele mai recente decât 'years_to_keep' ani)
    current_time_ms = int(time.time() * 1000)
    cutoff_time_ms = current_time_ms - (years_to_keep * 365 * 24 * 60 * 60 * 1000)  # Ani convertiți în milisecunde

    # Eliminăm tranzacțiile care sunt mai vechi decât perioada dorită
    filtered_existing_trades = [trade for trade in existing_trades if trade['time'] > cutoff_time_ms]
    print(f"Kept {len(filtered_existing_trades)} trades after filtering out old trades (older than {years_to_keep} years).")

    # Dacă există deja tranzacții, găsim cea mai recentă tranzacție salvată
    if filtered_existing_trades:
        most_recent_trade_time = max(trade['time'] for trade in filtered_existing_trades)
        print(f"Most recent trade time from file: {utils.convert_timestamp_to_human_readable(most_recent_trade_time)}")

        # Calculăm câte zile au trecut de la most_recent_trade_time până la acum
        time_diff_ms = current_time_ms - most_recent_trade_time
        backdays = time_diff_ms // (24 * 60 * 60 * 1000) + 1  # Câte zile au trecut de la ultima tranzacție
    else:
        most_recent_trade_time = 0  # Dacă nu există tranzacții, începem de la 0
        backdays = 60  # Adăugăm tranzacții pentru ultimele 60 de zile dacă fișierul e gol

    print(f"Fetching trades from the last {backdays} days.")

    # Apelăm funcția pentru a obține tranzacțiile recente doar din perioada lipsă
    new_trades = apitrades.get_my_trades_simple(order_type, symbol, backdays=backdays, limit=limit)

    # Filtrăm doar tranzacțiile care sunt mai recente decât cea mai recentă tranzacție din fișier
    new_trades = [trade for trade in new_trades if trade['time'] > most_recent_trade_time]

    if new_trades:
        print(f"Found {len(new_trades)} new trades.")
        
        # Adăugăm doar tranzacțiile noi la cele existente, dar fără cele vechi
        all_trades = filtered_existing_trades + new_trades
        all_trades = sorted(all_trades, key=lambda x: x['time'])  # Sortăm după timp

        # Salvăm doar tranzacțiile filtrate și actualizate în fișier
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


# Funcția care returnează tranzacțiile de tip 'buy' sau 'sell' din cache
def get_trade_orders(order_type, max_age_seconds):
    current_time_ms = int(time.time() * 1000)
    max_age_ms = max_age_seconds * 1000

    # Filtrăm tranzacțiile din cache care sunt de tipul corect și mai recente decât max_age_seconds
    filtered_trades = [
        trade for trade in trade_cache
        if trade['isBuyer'] == (order_type == 'buy') and (current_time_ms - trade['time']) <= max_age_ms
    ]

    return filtered_trades



  