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

#from log import PRINT_CONTEXT


# disable logs by redefine with dummy
#def print(*args, **kwargs):
 #   pass
#log.print = lambda *args, **kwargs: None

#log.disable_print()

class CacheManagerInterface(ABC):
    def __init__(self, sync_ts, symbols, filename, append_mode = True, api_client=api):
        self.cls_name = self.__class__.__name__
        
        #self.enable_print = True
        #global PRINT_CONTEXT
        #log.PRINT_CONTEXT = self
        
        self.sync_ts = sync_ts
        self.symbols = symbols
        self.filename = filename
        self.append_mode = append_mode
        self.api_client = api_client

        self.days_back = 30
        
        self.cache = {}
        self.fetchtime_time_per_symbol = {}
        
        self.thread = None
        self.save_state = False
        self.lock = threading.Lock()
      
        self.fallback_time_default = int(time.time() * 1000) - self.days_back*24*60*60*1000
      
        # function calls here after all inint vars
        self.load_state()
        self.thread = self.periodic_sync(sync_ts, False)
    

    #def get_all_symbols_from_cache(self):
    #    return list(set(t.get("symbol") for t in self.cache if "symbol" in t))
    def get_all_symbols_from_cache(self):
        with self.lock:
            return list(self.cache.keys())       
        
    @abstractmethod
    def rebuild_fetchtime_times(self):
        """Metoda abstractă – trebuie implementată de clasele derivate."""
        pass 
    
    
    def __rebuild_fetchtime_times(self):
        last_times_per_sym = self.rebuild_fetchtime_times()
        if not last_times_per_sym:
            last_times_per_sym = defaultdict(int)
            for symbol, trades in self.cache.items():
                for trade in trades:
                    # Caută "time" sau "timestamp", dacă nu există -> 0
                    time_ = trade.get("time") or trade.get("timestamp") or 0
                    if time_ > last_times_per_sym[symbol]:
                        last_times_per_sym[symbol] = time_
            # Offset de siguranță (60 sec)
            for symbol in last_times_per_sym:
                last_times_per_sym[symbol] = max(0, last_times_per_sym[symbol] - 60_000)                
        if not last_times_per_sym:
            # Fallback: folosim data fișierului
            fallback_time_file = 0
            if os.path.exists(self.filename): #TODO is daca am date in fisier
                fallback_time_file = int(os.path.getmtime(self.filename) * 1000) - 60_000
            fallback_time = min(self.fallback_time_default, fallback_time_file)
            return {symbol: fallback_time for symbol in self.symbols}
        return last_times_per_sym
          
        
    def load_state(self):
        print(f"[{self.cls_name}][Info] Load state from {self.filename} ...")
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r") as f:
                    data = json.load(f)
                    with self.lock:
                        self.cache = data.get("items", {})
                        if not isinstance(self.cache, dict):
                            # dacă fișierul avea format vechi (listă), transformăm în dict
                            self.cache = {sym: item for sym, item in zip(self.symbols, self.cache)}
                            print(f"[{self.cls_name}][warning] self.cache is not Dict!!!!")    
                        
                        self.fetchtime_time_per_symbol = data.get("fetchtime", {})
                        if not self.cache:
                            print(f"[{self.cls_name}][warning] cache is None")
                        if not self.fetchtime_time_per_symbol:
                            print(f"[{self.cls_name}][warning] fetchtime_time_per_symbol is None")    
                    
            except Exception as e:
                print(f"[{self.cls_name}][Eroare] La citirea fișierului cache {self.filename} : {e}")
                self.update_cache()
                self.save_state_to_file()
        else :
            print(f"[{self.cls_name}][Info] File is missing, may be is it first time run. Creating it ....")
            self.update_cache()
            self.save_state_to_file()


    def save_state_to_file(self):
        try:
            with self.lock:
                tmp_file = self.filename + ".tmp"
                with open(tmp_file, "w") as f:
                    json.dump({
                        "items": self.cache,
                        "fetchtime": self.fetchtime_time_per_symbol
                    }, f, indent=1)
                os.replace(tmp_file, self.filename)
                print(f"[{self.cls_name}][info] Save cache to {self.filename}")
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] La salvarea fișierului cache {self.filename} / .tmp : {e}")


    @abstractmethod
    def get_remote_items(self, symbol, startTime):
        """Metoda abstractă – trebuie implementată de clasele derivate."""
        pass 
        
     
    def filter_new_items(self, cache_items, new_items):
        seen = {json.dumps(it, sort_keys=True) for it in cache_items}
        unique_new = []
        for item in new_items:
            key = json.dumps(item, sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique_new.append(item)
        return unique_new
        
    
    def update_cache_per_symbol(self, symbol):
        
        current_time = int(time.time() * 1000)
        startTime = self.fetchtime_time_per_symbol.get(symbol, self.fallback_time_default)

        new_items = self.get_remote_items(symbol=symbol, startTime=startTime)
        if not new_items:
             print(f"[{self.cls_name}][Info] {symbol}:  No remote items starting with {u.timestampToTime(startTime)} ")
             return
        
        if symbol not in self.cache:
            self.cache[symbol] = [] #self.cache.setdefault(symbol, []).extend(new_items)
            
        count_new_items = len(new_items)
        print(f"[{self.cls_name}][Info] {symbol}:  new_items {new_items}") 
       
        new_items = self.filter_new_items(self.cache[symbol], new_items)
        print(f"[{self.cls_name}][Info] {symbol}:  Din {count_new_items} pastrez doar {len(new_items)}") 
        if not new_items:
            return
            
        with self.lock:  # 👈 scriere protejată
            if self.append_mode:    # history mode (trade-uri) - # Pentru PriceOrders / Price / (Price)Trade , păstrăm toată lista de elemente   
                #if isinstance(new_items, dict):
                #    new_items = [new_items]
                #elif not isinstance(new_items, list):
                #    new_items = [new_items]                    
                self.cache[symbol].extend(new_items)
            else: # snapshot mode (trenduri)  
                self.cache[symbol] = new_items if isinstance(new_items, list) else [new_items]             #self.cache[symbol] = new_items[0]
          
            self.fetchtime_time_per_symbol[symbol] = current_time

        print(f"[{self.cls_name}][Info] {symbol}: Adăugate {len(new_items)} items noi.")

    def update_cache(self):
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self.__rebuild_fetchtime_times()
        
        for symbol in self.symbols:
            self.update_cache_per_symbol(symbol)


    def periodic_sync(self, sync_ts=None, save_state=True):
        if sync_ts is not None:
            self.sync_ts = sync_ts
        self.save_state = save_state
        
        def run():
            while True:
                print(f"\n[{self.cls_name}] Sync started at {time.strftime('%Y-%m-%d %H:%M:%S')} for {self.symbols}")
                self.update_cache()
                print(f"[{self.cls_name}] save state is {self.save_state}.")
                if self.save_state:
                    self.save_state_to_file()
                print(f"[{self.cls_name}] Sync completed for {self.symbols}")
            
                time.sleep(self.sync_ts)

        if self.thread is None:
            self.thread = threading.Thread(target=run, daemon=False)
            self.thread.start()
        return self.thread
    
    def enable_save_state_to_file(self):
        self.save_state = True



# ###### 
# ###### Implemetarile specifice pentru cache
# ###### 

class CacheTradeManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols=sym.symbols, filename="cache_trade.json", api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)

    def _is_valid_trade(self, trade):
        required_keys = ['symbol', 'id', 'orderId', 'price', 'qty', 'time', 'isBuyer']
        return all(k in trade for k in required_keys)

    def rebuild_fetchtime_times(self):
        return None
        
    def get_remote_items(self, symbol, startTime):
        import importlib
        apitrades = importlib.import_module("binanceapi_trades")
        
        current_time = int(time.time() * 1000)
        backdays = int((current_time - startTime) / (24 * 60 * 60 * 1000))
        
        new_trades = api.client.get_my_trades(symbol=symbol, startTime=startTime, limit=1000)
        #new_trades = apitrades.get_my_trades(order_type=None, symbol=symbol, backdays=backdays, limit=1000)
 
        existing_ids = set(str(t["id"]) for t in self.cache.get(symbol, []) if "id" in t)
        print(f"[{self.cls_name}][info] Număr de trades noi: {len(new_trades)}")     
        unique_new_trades = []
        for t in new_trades:
            if not self._is_valid_trade(t):
                print(f"[{self.cls_name}] Trade invalid: {t}")
                continue

            trade_id = str(t["id"])
            if trade_id not in existing_ids:
                unique_new_trades.append(t)
                existing_ids.add(trade_id)

        print(f"[{self.cls_name}][info] Număr de unique_new_trades trades noi: {len(unique_new_trades)}")            
        return unique_new_trades
        

class CacheOrderManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols=sym.symbols, filename="cache_orders.json", api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)
        
    def _is_valid_trade(self, trade):
       required_keys = ['orderId', 'price', 'quantity', 'timestamp', 'side']    
       return all(k in trade for k in required_keys)
 
    def get_all_symbols_from_cache(self):
        return list(set(t.get("symbol") for t in self.cache if "symbol" in t))

    def rebuild_fetchtime_times(self):
        return None
        
    def get_remote_items(self, symbol, startTime):
        #import binanceapi_trades as apitrades
        import binanceapi_allorders as apiorders
        
        current_time = int(time.time() * 1000)
        #backdays = int((current_time - startTime) / (24 * 60 * 60 * 1000))
               
        #new_trades = api.client.get_my_trades(symbol=symbol, startTime=startTime, limit=1000)
        #new_trades = apitrades.get_my_trades(order_type = None, symbol=symbol, backdays=backdays, limit=1000)
        new_orders = apiorders.get_filled_orders(order_type = None, symbol=symbol, startTime=startTime)
               
        existing_ids = set(str(t["orderId"]) for t in self.cache if "orderId" in t)

        print(f"[{self.cls_name}][info] Număr de trades noi: {len(new_orders)}")
        unique_new_orders = []

        for t in new_orders:
            if not self._is_valid_trade(t):
                print(f"[{self.cls_name}] Trade invalid: {t}")
                continue

            trade_id = str(t["orderId"])
            if trade_id not in existing_ids:
                unique_new_orders.append(t)
                existing_ids.add(trade_id)

        print(f"[{self.cls_name}][info] Număr de unique_new_orders orders noi: {len(unique_new_orders)}")
        
        return unique_new_orders


class CachePriceManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols, filename, api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api)

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


class CachePriceTrendManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols, filename="price_trend_cache.json", api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=False)

    def get_all_symbols_from_cache(self):
        return [t.get("symbol") for t in self.cache if "symbol" in t]

    def rebuild_fetchtime_times(self):
        """
        Deducem timpul ultimei înregistrări per simbol din self.cache
        """
        last_times = defaultdict(int)
        for price_trend in self.cache:
            symbol = price_trend.get("symbol")
            ts = price_trend.get("timestamp", 0) * 1000
            if ts > last_times[symbol]:
                last_times[symbol] = ts

        # offset de siguranță (-60 secunde)
        for symbol in last_times:
            last_times[symbol] = max(0, last_times[symbol] - 60_000)

        return dict(last_times)
        
    def get_remote_items(self, symbol, startTime):
        # TODO : import priceanalysis name file
        filename = "priceanalysis.json"
        if not os.path.exists(filename):
            print(f"[{self.cls_name}] Fișierul {self.filename} nu există.")
            return []

        try:
            with open(filename, "r") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[{self.cls_name}] Eroare citire {self.filename}: {e}")
            return []

        if symbol not in data:
            return []

        return [data[symbol]]
        
            
# ###### 
# ###### GLOBAL VARIABLE FOR CACHE ####### 
# ###### 
     
ORDER_SYNC_INTERVAL_SEC = 3 * 60/10   # 3 minute     
TRADE_SYNC_INTERVAL_SEC = 3 * 60/15   # 3 minute
PRICE_SYNC_INTERVAL_SEC = 7 * 60/30   # 7 minute
PRICETREND_SYNC_INTERVAL_SEC = 10 * 60/100   # 10 minute

class CacheFactory:
    _instances = {}

    _CONFIG = {
        "Trade": {
            "class": CacheTradeManager,
            "filename": "cache_trade.json",
            "sync_ts": lambda: TRADE_SYNC_INTERVAL_SEC,
        },
        "Order": {
            "class": CacheOrderManager,
            "filename": "cache_order.json",
            "sync_ts": lambda: ORDER_SYNC_INTERVAL_SEC,
        },
        "Price": {
            "class": CachePriceManager,
            "filename": None,  # dict per simbol
            "sync_ts": lambda: PRICE_SYNC_INTERVAL_SEC,
        },
        "PriceTrend": {
            "class": CachePriceTrendManager,
            "filename": "cache_price_trend.json",
            "sync_ts": lambda: PRICETREND_SYNC_INTERVAL_SEC,
        },
    }

    @classmethod
    def get(cls, name, symbols=None):
        if name not in cls._CONFIG:
            raise ValueError(f"Unknown cache type: {name}")

        if name not in cls._instances:
            config = cls._CONFIG[name]
            manager_class = config["class"]
            sync_ts = config["sync_ts"]()
            if symbols is None:
                symbols = sym.symbols

            if name == "Price":
                # dict per simbol
                cls._instances[name] = {
                    s: manager_class(
                        sync_ts=sync_ts,
                        filename=f"cache_price_{s}.json",
                        symbols=[s],
                        api_client=api,
                    )
                    for s in symbols
                }
            else:
                cls._instances[name] = manager_class(
                    sync_ts=sync_ts,
                    filename=config["filename"],
                    symbols=symbols,
                    api_client=api,
                )

        return cls._instances[name]
        
def get_cache_manager(name, symbols=None):
    return CacheFactory.get(name, symbols)


# ###### 
# ###### FORCE CACHE TO BE UPDATEING ####### 
# ###### 
        
if __name__ == "__main__":
    threads = []

    for name, config in CacheFactory._CONFIG.items():
        cache = get_cache_manager(name)
        interval = config["sync_ts"]()  # obținem intervalul de sincronizare

        if name == "Price":
            # dict per simbol
            for manager in cache.values():
                threads.append(manager.periodic_sync(interval))
        else:
            threads.append(cache.periodic_sync(interval))

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("Oprit manual.")
    finally:
        print("Cleanup / închidere resurse...")
