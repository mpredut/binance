import json
import os
import time
import datetime
from datetime import datetime
from abc import ABC, abstractmethod
from collections import defaultdict
import threading

#my imports
import log
import utils as u
import symbols as sym
import binanceapi as api
import binanceapi_trades as apitrades


class CacheManagerInterface(ABC):
    def __init__(self, symbols, filename, api_client=api):
        self.cls_name = self.__class__.__name__
        self.api_client = api_client
        self.symbols = symbols
        self.filename = filename
        self.cache = []
        self.fetchtime_time_per_symbol = {}

        self.load_state()
        # self.periodic_sync()

    @abstractmethod
    def rebuild_fetchtime_times(self):
        """Metoda abstractă – trebuie implementată de clasele derivate."""
        pass 
        
    def _rebuild_fetchtime_times(self):
        last_times = self.rebuild_fetchtime_times()
        if not last_times:
            # Fallback: folosim data fișierului
            if os.path.exists(self.filename):
                fallback_time = int(os.path.getmtime(self.filename) * 1000) - 60_000
                return {symbol: fallback_time for symbol in self.get_all_symbols_from_cache()}
        return last_times
          
        
    def load_state(self):
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r") as f:
                    data = json.load(f)
                    self.cache = data.get("items", [])
                    self.fetchtime_time_per_symbol = data.get("fetchtime", {})
            except Exception as e:
                print(f"[{self.cls_name}][Eroare] La citirea fișierului cache {self.filename} : {e}")

        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self._rebuild_fetchtime_times()

    def save_state(self):
        try:
            tmp_file = self.filename + ".tmp"
            with open(tmp_file, "w") as f:
                json.dump({
                    "items": self.cache,
                    "fetchtime": self.fetchtime_time_per_symbol
                }, f)
            os.replace(tmp_file, self.filename)
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] La salvarea fișierului cache: {e}")

    @abstractmethod
    def get_remote_items(self, symbol, startTime):
        """Metoda abstractă – trebuie implementată de clasele derivate."""
        pass 
                           
    def update_cache_per_symbol(self, symbol):
        # Timpul curent ca referință de endTime
        current_time = int(time.time() * 1000)
        startTime = self.fetchtime_time_per_symbol.get(symbol, 0)

        unique_new_items = self.get_remote_items(symbol=symbol, startTime=startTime)
        
        self.cache.extend(unique_new_items)
        self.fetchtime_time_per_symbol[symbol] = current_time

        print(f"[{self.cls_name}][Info] {symbol}: Adăugate {len(unique_new_items)} items noi.")

    def update_cache(self):
        for symbol in self.symbols:
            self.update_cache_per_symbol(symbol)
        self.save_state()

   
    def periodic_sync(self, sync_interval_sec):
        def run():
            while True:
                print(f"\n[{self.cls_name}] Sync started at {time.strftime('%Y-%m-%d %H:%M:%S')} for {self.symbols}")
                self.update_cache()
                print(f"[{self.cls_name}] Sync completed for {self.symbols}")
            
                time.sleep(sync_interval_sec)

        thread = threading.Thread(target=run, daemon=False)
        thread.start()
        return thread



# ###### 
# ###### Implemetarile specifice pentru cache
# ###### 
class TradeCacheManager(CacheManagerInterface):
    def __init__(self, symbols=sym.symbols, filename="cachetrade.json", api_client=api):
        super().__init__(symbols, filename, api_client)
        self.first = True
        self.days_back = 30

    def _is_valid_trade(self, trade):
        required_keys = ['symbol', 'id', 'orderId', 'price', 'qty', 'time', 'isBuyer']
        return all(k in trade for k in required_keys)
 
    def get_all_symbols_from_cache(self):
        return list(set(t.get("symbol") for t in self.cache if "symbol" in t))

    def rebuild_fetchtime_times(self):
        # Deducem timpul ultimei interogări per simbol din cache
        last_times = defaultdict(int)
        for trade in self.cache:
            symbol = trade.get("symbol")
            time_ = trade.get("time", 0)
            if time_ > last_times[symbol]:
                last_times[symbol] = time_

        # Offset de siguranță (60 sec)
        for symbol in last_times:
            last_times[symbol] = max(0, last_times[symbol] - 60_000)
      
        return dict(last_times)
        
    def get_remote_items(self, symbol, startTime):
            
        current_time = int(time.time() * 1000)
        backdays = int((current_time - startTime) / (24 * 60 * 60 * 1000))
   
        if self.first:
            # startTime = timpul curent minus numărul de zile configurabil (convertit în milisecunde)
            startTime = current_time - self.days_back * (24 * 60 * 60 * 1000)
            backdays = self.days_back
            
        try:
            #new_trades = api.client.get_my_trades(symbol=symbol, startTime=startTime, limit=1000)
            new_trades = apitrades.get_my_trades(order_type = None, symbol=symbol, backdays=backdays, limit=1000)
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] Binance API pentru {symbol}: {e}")
            return []
            
        self.first = False

        # Elimină duplicatele (după id dacă există, altfel după time + symbol)
        existing_keys = set(
            (t.get("id"), t["symbol"]) for t in self.cache if "id" in t
        )

        print(f"[{self.cls_name}][info] Număr de trades noi: {len(new_trades)}")
        unique_new_trades = []
        for t in new_trades:
            if not self._is_valid_trade(t):
                print(f"[{self.cls_name}] BED DAY???")
                continue 
            key = (t.get("id"), t["symbol"]) if "id" in t else (t["symbol"], t["time"])
            if key not in existing_keys:
                unique_new_trades.append(t)
                existing_keys.add(key)
        
        print(f"[{self.cls_name}][info] Număr de unique_new_trades trades noi: {len(unique_new_trades)}")            
        return unique_new_trades
        


class PriceCacheManager(CacheManagerInterface):
    def __init__(self, symbols, filename="cacheprice.json", api_client=api):
        super().__init__(symbols, filename, api_client)


    def rebuild_fetchtime_times(self):
        if not self.cache:
            return {}
        last_times = {symbol: max(entry[0] for entry in self.cache if entry) for symbol in self.symbols}
        return last_times


    def get_remote_items(self, symbol, startTime):
        try:
            price = self.api_client.get_current_price(symbol=symbol)
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] Binance API pentru {symbol}: {e}")
            return []

        timestamp = int(time.time())  # timestamp UTC în secunde
        # Conversie în local
        local_dt = datetime.fromtimestamp(timestamp)  # local time
        local_ts_ms = int(local_dt.timestamp() * 1000)

        price_entry = [local_ts_ms, price]

        return [price_entry]

    def get_all_symbols_from_cache(self):
        return self.symbols

# ###### 
# ###### GLOBAL VARIABLE FOR CACHE ####### 
# ###### 

# trade cache
trade_cache_manager = TradeCacheManager(filename="cache_trade.json", symbols=sym.symbols, api_client=api)
    
# price cache
price_cache_manager = {}
for symbol in sym.symbols:
    price_cache_manager[symbol] = PriceCacheManager(filename=f"cache_price_{symbol}.json",
                                                    symbols=[symbol],
                                                    api_client=api)
                                                    


TRADE_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute
PRICE_SYNC_INTERVAL_SEC = 7 * 60   # 7 minute
# ###### 
# ###### FORCE CACHE TO BE UPDATEING ####### 
# ###### 
        
if __name__ == "__main__":
    threads = []
    threads.append(trade_cache_manager.periodic_sync(TRADE_SYNC_INTERVAL_SEC))
    for symbol in sym.symbols:
        threads.append(price_cache_manager[symbol].periodic_sync(PRICE_SYNC_INTERVAL_SEC))

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("Oprit manual.")
    finally:
        print("Cleanup / închidere resurse...")
