import json
import os
import time
from collections import defaultdict


#my imports
import log
import utils as u
import symbols as sym
import binanceapi as api

SYNC_INTERVAL_SEC = 600/10*5  # 10 minute

class TradeCacheManager:
    def __init__(self, filename="cache.json"):
        self.filename = filename
        self.trade_cache = []
        self.last_fetch_time_per_symbol = {}

        self.load_state()

    def load_state(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r") as f:
                    data = json.load(f)
                    self.trade_cache = data.get("trades", [])
                    self.last_fetch_time_per_symbol = data.get("last_fetch", {})
            except Exception as e:
                print(f"[Eroare] La citirea fișierului cache: {e}")

        if not self.last_fetch_time_per_symbol:
            self.last_fetch_time_per_symbol = self._rebuild_last_fetch_times()

    def _rebuild_last_fetch_times(self):
        # Deducem timpul ultimei interogări per simbol din cache
        last_times = defaultdict(int)
        for trade in self.trade_cache:
            symbol = trade.get("symbol")
            time_ = trade.get("time", 0)
            if time_ > last_times[symbol]:
                last_times[symbol] = time_

        # Offset de siguranță (60 sec)
        for symbol in last_times:
            last_times[symbol] = max(0, last_times[symbol] - 60_000)

        if not last_times:
            # Fallback: folosim data fișierului
            fallback_time = int(os.path.getmtime(self.filename) * 1000) - 60_000
            return {symbol: fallback_time for symbol in self.get_all_symbols_from_cache()}
        
        return dict(last_times)

    def get_all_symbols_from_cache(self):
        return list(set(t.get("symbol") for t in self.trade_cache if "symbol" in t))

    def save_state(self):
        try:
            tmp_file = self.filename + ".tmp"
            with open(tmp_file, "w") as f:
                json.dump({
                    "trades": self.trade_cache,
                    "last_fetch": self.last_fetch_time_per_symbol
                }, f)
            os.replace(tmp_file, self.filename)
        except Exception as e:
            print(f"[Eroare] La salvarea fișierului cache: {e}")

    def _is_valid_trade(self, trade):
        required_keys = ['symbol', 'id', 'orderId', 'price', 'qty', 'time', 'isBuyer']
        return all(k in trade for k in required_keys)
    
    def update_symbol_from_binance(self, symbol):
        # Timpul curent ca referință de endTime
        current_time = int(time.time() * 1000)
        start_time = self.last_fetch_time_per_symbol.get(symbol, 0)

        try:
            new_trades = api.client.get_my_trades(symbol=symbol, startTime=start_time)
        except Exception as e:
            print(f"[Eroare] Binance API pentru {symbol}: {e}")
            return

        # Elimină duplicatele (după id dacă există, altfel după time + symbol)
        existing_keys = set(
            (t.get("id"), t["symbol"]) for t in self.trade_cache if "id" in t
        )

        unique_new_trades = []
        for t in new_trades:
            if not self._is_valid_trade(t):
                print("f"BED DAY???")
                continue 
            key = (t.get("id"), t["symbol"]) if "id" in t else (t["symbol"], t["time"])
            if key not in existing_keys:
                unique_new_trades.append(t)
                existing_keys.add(key)

        self.trade_cache.extend(unique_new_trades)
        self.last_fetch_time_per_symbol[symbol] = current_time

        print(f"[Info] {symbol}: Adăugate {len(unique_new_trades)} tranzacții noi.")

    def update_all(self, symbols):
        for symbol in symbols:
            self.update_symbol_from_binance(symbol, client)
        self.save_state()


cache_manager = TradeCacheManager("cache.json")
periodic_sync()

def periodic_sync():
    print(f"\n--- Sync started at {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    cache_manager.update_all(sym.symbols)
    print(f"--- Sync completed ---")
    
    # Planifică următoarea rulare
    threading.Timer(SYNC_INTERVAL_SEC, periodic_sync, args=()).start()
