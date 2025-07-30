import os
import json
import time
from datetime import datetime
from binance import Client


import threading

# Asigură-te că trade_cache e global și inițializat înainte de apeluri repetate
trade_cache = []
import os
import json
import time
from binance import Client

trade_cache = []  # Listă globală unificată
file_mod_times = {}  # simbol -> ultima salvare

SAVE_INTERVAL_SEC = 600  # 10 minute


def load_trades_from_file_once(base_filename, symbol):
    filename = f"{base_filename}_{symbol}.json"

    if os.path.exists(filename):
        try:
            with open(filename, 'r') as f:
                trades = json.load(f)
                trade_cache.extend(trades)
                file_mod_times[symbol] = os.path.getmtime(filename)
                print(f"Loaded {len(trades)} trades for {symbol}")
        except json.JSONDecodeError:
            print(f"Error reading {filename}, skipping")
    else:
        file_mod_times[symbol] = 0


def fetch_and_cache_new_trades(api, symbol, base_filename, limit=1000):
    global trade_cache

    current_time_ms = int(time.time() * 1000)
    existing_trades = [t for t in trade_cache if t.get("symbol") == symbol]
    most_recent_time = max((t['time'] for t in existing_trades), default=0)

    start_time = most_recent_time + 1 if most_recent_time else None
    end_time = current_time_ms

    try:
        if start_time:
            new_trades = api.client.get_my_trades(symbol=symbol, limit=limit, startTime=start_time, endTime=end_time)
        else:
            new_trades = api.client.get_my_trades(symbol=symbol, limit=limit, endTime=end_time)
    except Exception as e:
        print(f"Error fetching trades for {symbol}: {e}")
        return

    existing_ids = {t['id'] for t in existing_trades}
    unique_new_trades = [t for t in new_trades if t['id'] not in existing_ids]

    if unique_new_trades:
        print(f"Fetched {len(unique_new_trades)} new trades for {symbol}")
        trade_cache.extend(unique_new_trades)
    else:
        print(f"No new trades for {symbol}")


def maybe_save_trades_to_file(base_filename, symbol):
    now = time.time()
    last_saved = file_mod_times.get(symbol, 0)

    if now - last_saved < SAVE_INTERVAL_SEC:
        print(f"Skip saving {symbol}, not enough time passed.")
        return

    filename = f"{base_filename}_{symbol}.json"
    trades_for_symbol = [t for t in trade_cache if t.get("symbol") == symbol]

    try:
        with open(filename, 'w') as f:
            json.dump(trades_for_symbol, f, indent=2)
        file_mod_times[symbol] = now
        print(f"Saved {len(trades_for_symbol)} trades for {symbol} to {filename}")
    except Exception as e:
        print(f"Failed to save {symbol}: {e}")




def sync_trades(api, base_filename, order_type="BUY"):
    for symbol in sym.symbols:
        fetch_and_cache_new_trades(api, symbol, base_filename)
        maybe_save_trades_to_file(base_filename, symbol)


def periodic_sync(api, base_filename, order_type="BUY"):
    # Execută sincronizarea o dată
    print(f"\n--- Sync started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    sync_trades(api, base_filename, order_type)
    print(f"--- Sync completed ---")

    # Planifică următoarea rulare
    threading.Timer(SYNC_INTERVAL_SEC, periodic_sync, args=(api, base_filename, order_type)).start()



for symbol in sym.symbols:
    load_trades_from_file_once('my_trades', symbol)

# Pornește sincronizarea automată
periodic_sync(api, base_filename='my_trades', order_type='BUY')


# Aplicatia rămâne vie
    while True:
        time.sleep(3600)  # Sau folosește alt mecanism pentru a ține aplicația ac