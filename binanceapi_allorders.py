
################
def get_my_trades_24(symbol, days_ago, order_type=None, limit=1000):
    all_trades = []
    try:
        current_time = int(time.time() * 1000)
        
        # Calculăm start_time și end_time pentru ziua specificată în urmă
        end_time = current_time - days_ago * 24 * 60 * 60 * 1000
        start_time = end_time - 24 * 60 * 60 * 1000  # Cu 24 de ore în urmă de la end_time

        # Apelăm API-ul pentru tranzacții în intervalul specificat
        while start_time < end_time:
            trades = client.get_my_trades(symbol=symbol, limit=limit, startTime=start_time, endTime=end_time)

            if not trades:
                break

            print("GASIT")
            
            # Dacă order_type este specificat, filtrăm tranzacțiile
            if order_type == "buy":
                filtered_trades = [trade for trade in trades if trade['isBuyer']]
            elif order_type == "sell":
                filtered_trades = [trade for trade in trades if not trade['isBuyer']]
            else:
                # Dacă nu e specificat order_type, nu aplicăm niciun filtru
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


# symbol = 'BTCUSDT'
# limit = 2

# for days_ago in range (0,20):
    # print(f"Testing get_my_trades_24 for {symbol} on day {days_ago}...")
    # trades = get_my_trades_24(symbol, days_ago, limit)
    # if trades:
        # print(f"Found {len(trades)} trades for day {days_ago}.")
        # for trade in trades[:5]:  # Afișează primele 5 tranzacții
            # print(trade)
    # else:
        # print(f"No trades found for day {days_ago}.")


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
    backdays = 30
    limit = 1000

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
#test_get_my_trades()

import os
# Funcția care salvează tranzacțiile noi în fișier (completare dacă există deja)
def save_trades_to_file(order_type, symbol, filename, limit=1000):
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

    # Dacă există deja tranzacții, găsim cea mai recentă tranzacție salvată
    if existing_trades:
        most_recent_trade_time = max(trade['time'] for trade in existing_trades)
        print(f"Most recent trade time from file: {most_recent_trade_time}")

        # Calculăm câte zile au trecut de la most_recent_trade_time până la acum
        current_time = int(time.time() * 1000)
        time_diff_ms = current_time - most_recent_trade_time
        backdays = time_diff_ms // (24 * 60 * 60 * 1000) + 1  # Câte zile au trecut de la ultima tranzacție
    else:
        most_recent_trade_time = 0  # Dacă nu există tranzacții, începem de la 0
        backdays = 60  # Adăugăm tranzacții pentru ultimele 60 de zile dacă fișierul e gol

    print(f"Fetching trades from the last {backdays} days.")

    # Apelăm funcția pentru a obține tranzacțiile recente doar din perioada lipsă
    new_trades = get_my_trades_simple(order_type, symbol, backdays=backdays, limit=limit)

    # Filtrăm doar tranzacțiile care sunt mai recente decât cea mai recentă tranzacție din fișier
    new_trades = [trade for trade in new_trades if trade['time'] > most_recent_trade_time]

    if new_trades:
        print(f"Found {len(new_trades)} new trades.")
        
        # Adăugăm doar tranzacțiile noi la cele existente
        all_trades = existing_trades + new_trades
        all_trades = sorted(all_trades, key=lambda x: x['time'])  # Sortăm după timp

        # Salvăm doar tranzacțiile noi la fișier
        with open(filename, 'w') as f:
            json.dump(all_trades, f)

        print(f"Updated file with {len(all_trades)} total trades.")
    else:
        print("No new trades found to save.")

#save_trades_to_file(None, "BTCUSDT", "trades_BTCUSDT.json", limit=1000)

# Exemplu de utilizare pentru a obține tranzacțiile de tip buy
#buy_trades = get_filled_trades('buy', 'BTCUSDT', backdays=7*2, limit=2)
#sell_trades = get_filled_trades('sell', 'BTCUSDT', backdays=7*2, limit=4)

  
  
  
  #######
#start_time = int((datetime.datetime.now() - datetime.timedelta(days=backdays)).timestamp() * 1000)
def get_filled_orders(order_type, symbol, backdays=3):
    try:
        end_time = int(time.time() * 1000)  # milisecunde
        
        interval_hours = 1
        interval_ms = interval_hours * 60 * 60 * 1000  # interval_hours de ore în milisecunde
        start_time = end_time - backdays * 24 * 60 * 60 * 1000
       
        all_filtered_orders = []

        # Parcurgem intervale de 24 de ore și colectăm ordinele
        while start_time < end_time:
            current_end_time = min(start_time + interval_ms, end_time)
            orders = client.get_all_orders(symbol=symbol, startTime=start_time, endTime=current_end_time, limit=1000)
            print(f"orders : {len(orders)}")
            
            # Filtrăm ordinele complet executate și pe cele care corespund tipului de ordin specificat
            filtered_orders = [
                {
                    'orderId': order['orderId'],
                    'price': float(order['price']),
                    'quantity': float(order['origQty']),
                    'timestamp': order['time'] / 1000,  # Timpul în secunde
                    'side': order['side'].lower()
                }
                for order in orders if order['status'] == 'FILLED' and order['side'].lower() == order_type.lower()
            ]
            
            all_filtered_orders.extend(filtered_orders)
            
            # Actualizăm start_time pentru următorul interval
            start_time = current_end_time
        
        print(f"Filtered filled orders of type '{order_type}': {len(all_filtered_orders)}")
        #print("First few filled orders for inspection:")
        #for filled_order in all_filtered_orders[:5]:  # Afișează primele 5 ordine complet executate
            #print(filled_order)
        return all_filtered_orders

    except Exception as e:
        print(f"An error occurred: {e}")
        return []



start_time = int(time.time() * 1000) - 1 * 24 * 60 * 60 * 1000  # Cu 3 zile în urmă
end_time = int(time.time() * 1000)  # Momentul curent
order_type = "buy"  # sau "sell", sau None pentru ambele tipuri

#all_filtered_orders = get_all_orders_in_time_range(order_type, symbol, start_time, end_time)
#all_filtered_orders = get_filled_orders(order_type, symbol)
#print(f"Filtered filled orders of type '{order_type}': {len(all_filtered_orders)}")
#print("First few filled orders for inspection:")
#for filled_order in all_filtered_orders[:5]:  # Afișează primele 5 ordine complet executate
    #print(filled_order)
        

def get_recent_filled_orders(order_type, max_age_seconds):

    all_filled_orders = get_filled_orders(order_type, symbol)
    recent_filled_orders = []
    current_time = time.time()
    if(len(all_filled_orders) < 1) :
        return []

    print(len(all_filled_orders))
    order_time = current_time
    for order in all_filled_orders:
        order_time = order['timestamp']
        if current_time - order_time <= max_age_seconds:
            recent_filled_orders.append(order)

    # Sort the recent_filled_orders by price in ascending order
    recent_filled_orders.sort(key=lambda x: x['price'])

    return recent_filled_orders


