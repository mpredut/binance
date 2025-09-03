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
#import binanceapi_trades as apitrades
import binanceapi_allorders as apiorders


class CacheManagerInterface(ABC):
    def __init__(self, sync_ts, symbols, filename, append_mode = True, api_client=api):
        self.cls_name = self.__class__.__name__
        
        self.sync_ts = sync_ts
        self.symbols = symbols
        self.filename = filename
        self.append_mode = append_mode
        self.api_client = api_client
        
        self.first = {symbol: True for symbol in symbols}
        self.days_back = 30
        
        self.cache = {}
        self.fetchtime_time_per_symbol = {}

        self.load_state()
        self.periodic_sync(sync_ts, False)

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
        print(f"[{self.cls_name}][Info] Load state from {self.filename} ...")
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r") as f:
                    data = json.load(f)
                    self.cache = data.get("items", {})
                    self.fetchtime_time_per_symbol = data.get("fetchtime", {})
                    if not self.cache:
                        print(f"[{self.cls_name}][warning] cache is None")
                    if not self.fetchtime_time_per_symbol:
                        print(f"[{self.cls_name}][warning] fetchtime_time_per_symbol is None")    
                    
            except Exception as e:
                print(f"[{self.cls_name}][Eroare] La citirea fișierului cache {self.filename} : {e}")
                self.update_cache()
                self.save_state()
        else :
            print(f"[{self.cls_name}][Info] File is missing, may be first time - create it")
            self.update_cache()
            self.save_state()

        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self._rebuild_fetchtime_times()

    def save_state(self):
        try:
            tmp_file = self.filename + ".tmp"
            with open(tmp_file, "w") as f:
                json.dump({
                    "items": self.cache,
                    "fetchtime": self.fetchtime_time_per_symbol
                }, f, indent=1)
            os.replace(tmp_file, self.filename)
            print(f"[{self.cls_name}][info] Save cache to {self.filename}")
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

        new_items = self.get_remote_items(symbol=symbol, startTime=startTime)
        if not new_items:
             print(f"[{self.cls_name}][Info] {symbol}:  No remote items starting with {u.timestampToTime(startTime)} ")
             return
        print(f"[{self.cls_name}][Info] {symbol}:  new_items {new_items}")     
        if not self.append_mode:  # snapshot mode (trenduri)
            # Pentru PriceTrend / PriceCache, păstrăm toată lista de elemente
            #self.cache[symbol] = new_items[0]
            self.cache[symbol] = new_items if isinstance(new_items, list) else [new_items]
        else:  # history mode (trade-uri)
            self.cache.setdefault(symbol, []).extend(new_items)
            #if symbol not in self.cache:
            #    self.cache[symbol] = []
            #self.cache[symbol].extend(new_items)
    
        
        self.fetchtime_time_per_symbol[symbol] = current_time

        print(f"[{self.cls_name}][Info] {symbol}: Adăugate {len(new_items)} items noi.")

    def update_cache(self):
        for symbol in self.symbols:
            self.update_cache_per_symbol(symbol)

    def periodic_sync(self, sync_ts=None, save_state=True):
        if sync_ts is None:
            sync_ts = self.sync_ts
        def run():
            while True:
                print(f"\n[{self.cls_name}] Sync started at {time.strftime('%Y-%m-%d %H:%M:%S')} for {self.symbols}")
                self.update_cache()
                print(f"[{self.cls_name}] save state is {save_state}.")
                if save_state:
                    self.save_state()
                print(f"[{self.cls_name}] Sync completed for {self.symbols}")
            
                time.sleep(sync_ts)

        thread = threading.Thread(target=run, daemon=False)
        thread.start()
        return thread



# ###### 
# ###### Implemetarile specifice pentru cache
# ###### 

class TradeCacheManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols=sym.symbols, filename="cache_trade.json", api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)
        self.first = {symbol: True for symbol in symbols}
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
        import binanceapi_trades as apitrades
        
        current_time = int(time.time() * 1000)
        backdays = int((current_time - startTime) / (24 * 60 * 60 * 1000))
   
        if self.first[symbol]:
            # startTime = timpul curent minus numărul de zile configurabil (convertit în milisecunde)
            startTime = current_time - self.days_back * (24 * 60 * 60 * 1000)
            backdays = self.days_back
            
        #try:
            #new_trades = api.client.get_my_trades(symbol=symbol, startTime=startTime, limit=1000)
        new_trades = apitrades.get_my_trades(order_type = None, symbol=symbol, backdays=backdays, limit=1000)
        #except Exception as e:
        #    print(f"[{self.cls_name}][Eroare] Binance API pentru {symbol}: {e}")
        #    return []
            
        self.first[symbol] = False
      
        # Setul de id-uri existente
        existing_ids = set(str(t["id"]) for t in self.cache if "id" in t)

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
        

class OrderCacheManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols=sym.symbols, filename="cache_orders.json", api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)
        self.first = {symbol: True for symbol in symbols}
        self.days_back = 30

    def _is_valid_trade(self, trade):
       required_keys = ['orderId', 'price', 'quantity', 'timestamp', 'side']
       #'orderId': 273466555, 'price': 359.0, 'quantity': 11.142, 'timestamp': 1755770187866, 'side': 'sell'}         
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
        #import binanceapi_trades as apitrades
        
        current_time = int(time.time() * 1000)
        backdays = int((current_time - startTime) / (24 * 60 * 60 * 1000))
   
        if self.first[symbol]:
            # startTime = timpul curent minus numărul de zile configurabil (convertit în milisecunde)
            startTime = current_time - self.days_back * (24 * 60 * 60 * 1000)
            backdays = self.days_back
            
        try:
            #new_trades = api.client.get_my_trades(symbol=symbol, startTime=startTime, limit=1000)
            #new_trades = apitrades.get_my_trades(order_type = None, symbol=symbol, backdays=backdays, limit=1000)
            new_orders = apiorders.get_filled_orders(order_type = None, symbol=symbol, backdays=backdays)
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] Binance API pentru {symbol}: {e}")
            return []
            
        self.first[symbol] = False
      
        # Setul de id-uri existente
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

class PriceCacheManager(CacheManagerInterface):
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


class PriceTrendCacheManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols, filename="price_trend_cache.json", api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=False)
        self.first = {symbol: True for symbol in symbols}

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
     
ORDER_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute     
TRADE_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute
PRICE_SYNC_INTERVAL_SEC = 7 * 60   # 7 minute
PRICETREND_SYNC_INTERVAL_SEC = 10 * 60   # 10 minute

_trade_cache_manager = None
_order_cache_manager = None
_price_cache_manager = None
_price_trend_cache_manager = None

# trade cache
#TRADE_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute
#trade_cache_manager = TradeCacheManager(sync_ts=TRADE_SYNC_INTERVAL_SEC,
#                                        filename="cache_trade.json", 
#                                        symbols=sym.symbols, 
#                                        api_client=api)
def get_trade_cache_manager():
    global _trade_cache_manager
    if _trade_cache_manager is None:
        _trade_cache_manager = TradeCacheManager(
            sync_ts=TRADE_SYNC_INTERVAL_SEC,
            filename="cache_trade.json",
            symbols=sym.symbols,
            api_client=api,
        )
    return _trade_cache_manager
    
# order cache
#ORDER_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute
#order_cache_manager = OrderCacheManager(sync_ts=ORDER_SYNC_INTERVAL_SEC,
#                                        filename="cache_order.json", 
#                                        symbols=sym.symbols, 
#                                        api_client=api)
def get_order_cache_manager():
    global _order_cache_manager
    if _order_cache_manager is None:
        _order_cache_manager = OrderCacheManager(
            sync_ts=ORDER_SYNC_INTERVAL_SEC,
            filename="cache_order.json",
            symbols=sym.symbols,
            api_client=api,
        )
    return _order_cache_manager

# price cache
#PRICE_SYNC_INTERVAL_SEC = 7 * 60   # 7 minute
#price_cache_manager = {}
#for symbol in sym.symbols:
#    price_cache_manager[symbol] = PricePriceTrendCacheManager(sync_ts=PRICE_SYNC_INTERVAL_SEC,
#                                                    filename=f"cache_price_{symbol}.json",
#                                                    symbols=[symbol],
#                                                    api_client=api)
def get_price_cache_manager():
    global _price_cache_manager
    if _price_cache_manager is None:
        _price_cache_manager = {
            symbol: PriceCacheManager(
                sync_ts=PRICE_SYNC_INTERVAL_SEC,
                filename=f"cache_price_{symbol}.json",
                symbols=[symbol],
                api_client=api,
            )
            for symbol in sym.symbols
        }
    return _price_cache_manager
                                                    
# price trend cache
#PRICETREND_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute
#price_trend_cache_manager = `PriceTrendCacheManager(sync_ts=PRICETREND_SYNC_INTERVAL_SEC,
#                                        filename="cache_price_trend.json", 
#                                        symbols=sym.symbols, 
#                                        api_client=api)

def get_price_trend_cache_manager():
    global _price_trend_cache_manager
    if _price_trend_cache_manager is None:
        _price_trend_cache_manager = PriceTrendCacheManager(
            sync_ts=PRICETREND_SYNC_INTERVAL_SEC,
            filename="cache_price_trend.json",
            symbols=sym.symbols,
            api_client=api,
        )
    return _price_trend_cache_manager

# ###### 
# ###### FORCE CACHE TO BE UPDATEING ####### 
# ###### 
        
if __name__ == "__main__":
    order_cache_manager = get_order_cache_manager()
    trade_cache_manager = get_trade_cache_manager()
    price_cache_manager = get_price_cache_manager()
    price_trend_cache_manager = get_price_trend_cache_manager()
    
    threads = []
    # order
    threads.append(order_cache_manager.periodic_sync(ORDER_SYNC_INTERVAL_SEC))    
    # trade
    threads.append(trade_cache_manager.periodic_sync(TRADE_SYNC_INTERVAL_SEC))
    # price
    for symbol in sym.symbols:
        threads.append(price_cache_manager[symbol].periodic_sync(PRICE_SYNC_INTERVAL_SEC))
    # price trend
    threads.append(price_trend_cache_manager.periodic_sync(PRICETREND_SYNC_INTERVAL_SEC))
    
    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("Oprit manual.")
    finally:
        print("Cleanup / închidere resurse...")
