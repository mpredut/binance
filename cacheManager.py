import json
import os
import time
import datetime
import asyncio
import threading
import importlib
import builtins
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Optional

#my imports
import log
import utils as u
import symbols as sym
import bapi as api

#from log import PRINT_CONTEXT


# disable logs by redefine with dummy
def print(*args, **kwargs):
   pass
#log.print = lambda *args, **kwargs: None

#log.disable_print()

# WS-only mode: when True, polling for Order/Trade/AssetValue is paused while WS is healthy.
WS_ONLY_MODE = False
WS_LOSS_TIMEOUT_SEC = 40 # 600  # 10 minute 
WS_EVENT_LOG_ENABLED = True

_ws_health_lock = threading.Lock()
_ws_available = False
_ws_last_event_ts = 0.0
_ws_is_healthy = False


def _mark_ws_available(value):
    global _ws_available
    with _ws_health_lock:
        _ws_available = value


def _mark_ws_event_received():
    global _ws_last_event_ts, _ws_is_healthy
    with _ws_health_lock:
        _ws_last_event_ts = time.time()
        _ws_is_healthy = True


def _mark_ws_unhealthy():
    print("UNHAPPY -:( ) : _mark_ws_unhealthy ")
    global _ws_is_healthy
    with _ws_health_lock:
        _ws_is_healthy = False


def _should_poll_for_manager(cls_name):
    if not WS_ONLY_MODE:
        return True
    ws_managed_classes = {"CacheOrderManager", "CacheTradeManager", "CacheAssetValueManager"}
    if cls_name not in ws_managed_classes:
        return True
    with _ws_health_lock:
        return (not _ws_available) or (not _ws_is_healthy)

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
        self.lock = threading.RLock()
      
        self.fallback_time_default = int(time.time() * 1000) - self.days_back*24*60*60*1000
      
        # function calls here after all inint vars
        self.load_state()
        self.periodic_sync(sync_ts, False)
    

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
                    #time_ = trade.get("time") or trade.get("timestamp") or 0
                    if isinstance(trade, dict):
                        time_ = trade.get("time") or trade.get("timestamp") or 0
                    elif isinstance(trade, list) and len(trade) > 0:
                        time_ = trade[0]  # pentru format [timestamp_ms, price]
                    else:
                        time_ = 0
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
                self.query_remote_and_update_cache()
                self.save_state_to_file_if_enabled()
        else :
            print(f"[{self.cls_name}][Info] File is missing, may be is it first time run. Creating it ....")
            self.query_remote_and_update_cache()
            self.save_state_to_file_if_enabled()


    def save_state_to_file_if_enabled(self):
        if not self.save_state:
            return
        try:
            with self.lock:
                tmp_file = self.filename + ".tmp"
                with open(tmp_file, "w") as f:
                    json.dump({
                        "items": self.cache,
                        "fetchtime": self.fetchtime_time_per_symbol
                    }, f, indent=1)
                os.replace(tmp_file, self.filename)
                print(f"[{self.cls_name}][info] Save cache to file {self.filename}")
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
        
    
    def update_cache_per_symbol(self, symbol, new_items):
        
        current_time = int(time.time() * 1000)
      
        if symbol not in self.cache:
            self.cache[symbol] = [] #self.cache.setdefault(symbol, []).extend(new_items)
            
        count_new_items = len(new_items)
        print(f"[{self.cls_name}][Info] {symbol}:  new_items {new_items}") 
       
        #new_items = self.filter_new_items(self.cache[symbol], new_items)
        # with self.lock:
        #     cache_copy = list(self.cache.get(symbol, []))
        # new_items = self.filter_new_items(cache_copy, new_items)

        with self.lock:  # 👈 scriere protejată
            if self.append_mode:    # history mode (trade-uri) - # Pentru PriceOrders / Price / (Price)Trade , păstrăm toată lista de elemente   
                #if isinstance(new_items, dict):
                #    new_items = [new_items]
                #elif not isinstance(new_items, list):
                #    new_items = [new_items]                    
                cache_copy = list(self.cache.get(symbol, []))
                new_items = self.filter_new_items(cache_copy, new_items)
                print(f"[{self.cls_name}][Info] {symbol}:  Din {count_new_items} pastrez doar {len(new_items)}") 
                new_items = [item for item in new_items if item is not None]
                if not new_items:
                    return
                self.cache[symbol].extend(new_items)
            else: # snapshot mode (trenduri)  
                self.cache[symbol] = new_items if isinstance(new_items, list) else [new_items]             #self.cache[symbol] = new_items[0]
              
            self.fetchtime_time_per_symbol[symbol] = current_time

        print(f"[{self.cls_name}][Info] {symbol}: Adăugate {len(new_items)} items noi.")

    def query_remote_and_update_cache(self):
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self.__rebuild_fetchtime_times()
        
        for symbol in list(self.symbols):

            startTime = self.fetchtime_time_per_symbol.get(symbol, self.fallback_time_default)
            new_items = self.get_remote_items(symbol=symbol, startTime=startTime)
            if not new_items:
                print(f"[{self.cls_name}][Info] {symbol}:  No remote items starting with {u.timestampToTime(startTime)} ")
                continue
            
            self.update_cache_per_symbol(symbol, new_items)

    def on_items_update(self, symbol, items):
        print(f"[{self.cls_name}][Info] {symbol}: WS Items updated to {items}")
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self.__rebuild_fetchtime_times()
        self.update_cache_per_symbol(symbol, items)
        
    def periodic_sync(self, sync_ts=None, save_state=True):
        if sync_ts is not None:
            self.sync_ts = sync_ts
        self.save_state = save_state  # actualizează save_state indiferent

        if self.thread is not None and self.thread.is_alive():
            return self.thread  # thread deja pornit, returnează-l

        def run():
            while True:
                print(f"\n[{self.cls_name}] Sync started at {time.strftime('%Y-%m-%d %H:%M:%S')} for {self.symbols}")
                if _should_poll_for_manager(self.cls_name):
                    self.query_remote_and_update_cache()
                else:
                    print(f"[{self.cls_name}] Skip polling (WS-only mode active, WS healthy).")
                print(f"[{self.cls_name}] save state is {self.save_state}.")
                self.save_state_to_file_if_enabled()
                print(f"[{self.cls_name}] Sync completed for {self.symbols}")
                time.sleep(self.sync_ts)

        self.thread = threading.Thread(target=run, name=self.cls_name, daemon=True)
        self.thread.daemon = True  # Asigură că acest thread nu blochează închiderea procesului
        self.thread.start()
        return self.thread
    
    def enable_save_state_to_file(self):
        self.save_state = True



# ###### 
# ###### Implemetarile specifice pentru cache
# ###### 

class CacheTradeManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols, filename, api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)

    def _is_valid_trade(self, trade):
        required_keys = ['symbol', 'id', 'orderId', 'price', 'qty', 'time', 'isBuyer']
        return all(k in trade for k in required_keys)

    def rebuild_fetchtime_times(self):
        return None
        
    def get_remote_items(self, symbol, startTime):
        import importlib
        apitrades = importlib.import_module("bapi_trades")
        
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
    def __init__(self, sync_ts, symbols, filename, api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)
        
    def _is_valid_trade(self, trade):
       required_keys = ['orderId', 'price', 'quantity', 'timestamp', 'side']    
       return all(k in trade for k in required_keys)
     
    def get_all_symbols_from_cache(self):
        with self.lock:
            return list(self.cache.keys())
            
    def rebuild_fetchtime_times(self):
        return None
        
    def get_remote_items(self, symbol, startTime):
        #import bapi_trades as apitrades
        import bapi_allorders as apiorders
        
        current_time = int(time.time() * 1000)
        #backdays = int((current_time - startTime) / (24 * 60 * 60 * 1000))
               
        #new_trades = api.client.get_my_trades(symbol=symbol, startTime=startTime, limit=1000)
        #new_trades = apitrades.get_my_trades(order_type = None, symbol=symbol, backdays=backdays, limit=1000)
        new_orders = apiorders.get_filled_orders(order_type = None, symbol=symbol, startTime=startTime)
               
        existing_ids = set(str(t["orderId"]) for t in self.cache.get(symbol, []) if "orderId" in t)

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

    # def rebuild_fetchtime_times(self):
        # if not self.cache:
            # return {}
        # last_times = {symbol: max(entry[0] for entry in self.cache if entry) for symbol in self.symbols}
        # return last_times

    def rebuild_fetchtime_times(self):
        if not self.cache:
            return {}
        last_times = {}
        for symbol in self.symbols:
            entries = self.cache.get(symbol, [])
            if entries:
                last_times[symbol] = max(entry[0] for entry in entries)
        return last_times

    def get_remote_items(self, symbol, startTime):
        try:
            price = get_current_price_manager().get_price_value(symbol)
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] get_price_value {symbol}: {e}")
            return []

        if price is None:
            return []

        timestamp = int(time.time())  # timestamp UTC în secunde
        # Conversie în local
        local_dt = datetime.fromtimestamp(timestamp)  # local time
        local_ts_ms = int(local_dt.timestamp() * 1000)

        price_entry = [local_ts_ms, price]

        return [price_entry]

    def get_all_symbols_from_cache(self):
        return self.symbols


class Cache24PriceManager(CacheManagerInterface):
    """Colectează prețuri la granularitate maximă pe ultimele KEEP_HOURS ore.

    Nu face polling și nu se abonează direct la WS.
    Primește fiecare update de preț prin on_price_update() de la
    CacheCurrentPriceManager (subscribe_price).
    get_remote_items e folosit doar la init (load inițial din fișier lipsă).
    """
    KEEP_HOURS = 24   # configurabil per instanță dacă e nevoie

    def __init__(self, sync_ts, symbols, filename, api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)

    # ── Callback de la CacheCurrentPriceManager ───────────────────────────────

    def on_price_update(self, symbol: str, ts_ms: int, price: float):
        """Apelat de CacheCurrentPriceManager la fiecare preț nou (WS sau HTTP)."""
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self._CacheManagerInterface__rebuild_fetchtime_times()
        self.update_cache_per_symbol(symbol, [[ts_ms, price]])
        self._trim_old_data(symbol)

    def _trim_old_data(self, symbol):
        cutoff_ms = int((time.time() - self.KEEP_HOURS * 3600) * 1000)
        with self.lock:
            entries = self.cache.get(symbol)
            if entries:
                self.cache[symbol] = [e for e in entries if e[0] >= cutoff_ms]

    # ── CacheManagerInterface ─────────────────────────────────────────────────

    def rebuild_fetchtime_times(self):
        if not self.cache:
            return {}
        last_times = {}
        for symbol in self.symbols:
            entries = self.cache.get(symbol, [])
            if entries:
                last_times[symbol] = max(entry[0] for entry in entries)
        return last_times

    def get_remote_items(self, symbol, startTime):
        """Folosit doar la init când fișierul lipsește."""
        try:
            price = get_current_price_manager().get_price_value(symbol)
            if price is None:
                return []
            return [[int(time.time() * 1000), price]]
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] get_price_value {symbol}: {e}")
            return []

    def get_all_symbols_from_cache(self):
        return self.symbols

    def periodic_sync(self, sync_ts=None, save_state=True):
        """Doar salvează starea periodic. Prețurile vin exclusiv prin on_price_update."""
        if sync_ts is not None:
            self.sync_ts = sync_ts
        self.save_state = save_state

        if self.thread is not None and self.thread.is_alive():
            return self.thread

        def run():
            while True:
                self.save_state_to_file_if_enabled()
                time.sleep(self.sync_ts)

        self.thread = threading.Thread(target=run, name=self.cls_name, daemon=True)
        self.thread.daemon = True
        self.thread.start()
        return self.thread


class CachePriceTrendManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols, filename, api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=False)

    #def get_all_symbols_from_cache(self):
    #    return [t.get("symbol") for t in self.cache if "symbol" in t]

    def get_all_symbols_from_cache(self):
        with self.lock:
            return list(self.cache.keys())
        
    # def rebuild_fetchtime_times(self):
        # """
        # Deducem timpul ultimei înregistrări per simbol din self.cache
        # """
        # last_times = defaultdict(int)
        # for price_trend in self.cache:
            # symbol = price_trend.get("symbol")
            # ts = price_trend.get("timestamp", 0) * 1000
            # if ts > last_times[symbol]:
                # last_times[symbol] = ts

        # # offset de siguranță (-60 secunde)
        # for symbol in last_times:
            # last_times[symbol] = max(0, last_times[symbol] - 60_000)

        # return dict(last_times)
        
    def rebuild_fetchtime_times(self):
        last_times = defaultdict(int)
        for symbol, items in self.cache.items():
            for item in items:
                ts = item.get("timestamp", 0) * 1000
                if ts > last_times[symbol]:
                    last_times[symbol] = ts
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

        trend = data.get(symbol) 
        if trend is None: return []
        return [data[symbol]]
        

class CacheAssetValueManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols, filename, api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)

    def rebuild_fetchtime_times(self):
        last_times = {}
        for symbol, items in self.cache.items():
            if not items:
                continue
            max_ts_sec = max(int(item.get("timestamp", 0)) for item in items if isinstance(item, dict))
            if max_ts_sec > 0:
                last_times[symbol] = max(0, (max_ts_sec * 1000) - 60_000)
        return last_times

    def get_remote_items(self, symbol, startTime):
        try:
            total_usdt = self.api_client.get_total_assets_value_usdt(use_cache=False)
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] Nu pot interoga valoarea totala: {e}")
            return []

        if total_usdt is None or total_usdt <= 0:
            print(f"[{self.cls_name}][Eroare] Valoarea totala este None sau <= 0: {total_usdt}")
            return []
            
        now_sec = int(time.time())
        snapshot = {
            "timestamp": now_sec,
            "datetime_local": datetime.now().isoformat(timespec="seconds"),
            "total_value_usdt": round(float(total_usdt), 8),
        }
        return [snapshot]


# ######
# ###### CacheCurrentPriceManager — preț curent per simbol, WS-primary + HTTP fallback
# ######

class CacheCurrentPriceManager(CacheManagerInterface):
    """
    Menține CEL MAI RECENT preț per simbol, cu timestamp în ms.
    Drop-in replacement pentru bapi.get_current_price().

    Sursa primară   : WebSocket (BinanceWebSocketManager) via subscribe().
    Fallback timer  : polling HTTP la fiecare SYNC_TS secunde, DOAR când WS
                      e tăcut mai mult de WS_TIMEOUT_SEC.
    Staleness check : get_price() forțează HTTP imediat dacă prețul e mai
                      vechi de STALE_THRESHOLD_MS (indiferent de WS).

    Cache semantics : append_mode=False — se păstrează doar ultima intrare
                      per simbol: cache[symbol] = [[timestamp_ms, price]]
    Fișier          : cache_currentprice.json  (un singur fișier, toți simbolii)
    """

    WS_TIMEOUT_SEC    = 15      # WS considerat mort după 15s fără niciun event
    STALE_THRESHOLD_MS = 5_000  # get_price() forțează HTTP dacă prețul e > 5s vechi

    def __init__(self, sync_ts, symbols, filename, ws_manager=None, api_client=api):
        self._ws_manager        = ws_manager
        self._ws_last_event_ts  = 0.0      # setat înainte de super() !
        self._price_subscribers = []       # idem
        super().__init__(sync_ts, symbols, filename, append_mode=False, api_client=api_client)
        if ws_manager is not None:
            ws_manager.subscribe(self)

    # ── WS health ────────────────────────────────────────────────────────────

    def _ws_is_healthy(self):
        return (time.time() - self._ws_last_event_ts) < self.WS_TIMEOUT_SEC

    # ── Price subscriber pattern ──────────────────────────────────────────────

    def subscribe_price(self, subscriber) -> None:
        """Abonează un obiect care implementează on_price_update(symbol, ts_ms, price)."""
        with self.lock:
            if subscriber not in self._price_subscribers:
                self._price_subscribers.append(subscriber)

    def unsubscribe_price(self, subscriber) -> None:
        with self.lock:
            if subscriber in self._price_subscribers:
                self._price_subscribers.remove(subscriber)

    def _notify_price_subscribers(self, symbol: str, ts_ms: int, price: float) -> None:
        with self.lock:
            subs = list(self._price_subscribers)
        for sub in subs:
            try:
                sub.on_price_update(symbol, ts_ms, price)
            except Exception as e:
                print(f"[{self.cls_name}] Eroare notificare subscriber: {e}")

    # ── WS callback (suprascrie metoda din interfață) ─────────────────────────

    def on_items_update(self, symbol: str, items):
        self._ws_last_event_ts = time.time()
        price = items[0] if items else None
        if price is None:
            return
        ts_ms = int(time.time() * 1000)
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self._CacheManagerInterface__rebuild_fetchtime_times()
        self.update_cache_per_symbol(symbol, [[ts_ms, price]])
        self._notify_price_subscribers(symbol, ts_ms, price)

    # ── CacheManagerInterface — metode abstracte ──────────────────────────────

    def get_remote_items(self, symbol, startTime):
        """Fetch preț curent via bapi.get_current_price."""
        try:
            price = self.api_client.get_current_price(symbol=symbol)
            if price is None:
                return []
            ts_ms = int(time.time() * 1000)
            return [[ts_ms, price]]
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] HTTP fetch {symbol}: {e}")
            return []

    def rebuild_fetchtime_times(self):
        last_times = {}
        for symbol in self.symbols:
            entries = self.cache.get(symbol, [])
            if entries:
                last_times[symbol] = entries[0][0]   # snapshot: un singur entry
        return last_times

    def get_all_symbols_from_cache(self):
        return self.symbols

    # ── Periodic sync — polling numai când WS e mort ──────────────────────────

    def periodic_sync(self, sync_ts=None, save_state=True):
        if sync_ts is not None:
            self.sync_ts = sync_ts
        self.save_state = save_state

        if self.thread is not None and self.thread.is_alive():
            return self.thread

        def run():
            time.sleep(self.sync_ts)   # prima iterație după un interval, nu imediat
            while True:
                if not self._ws_is_healthy():
                    print(f"[{self.cls_name}] WS inactiv – fallback polling {self.symbols}")
                    self.query_remote_and_update_cache()
                else:
                    print(f"[{self.cls_name}] WS activ – skip polling {self.symbols}")
                self.save_state_to_file_if_enabled()
                time.sleep(self.sync_ts)

        self.thread = threading.Thread(target=run, name=self.cls_name, daemon=True)
        self.thread.daemon = True
        self.thread.start()
        return self.thread

    # ── Public API ────────────────────────────────────────────────────────────

    def get_price(self, symbol: str):
        """
        Returnează [timestamp_ms, price].
        Forțează HTTP fetch dacă intrarea lipsește sau e mai veche de STALE_THRESHOLD_MS.
        """
        with self.lock:
            entries = self.cache.get(symbol)
        last_ts = entries[0][0] if entries else 0
        now_ms  = int(time.time() * 1000)
        if not entries or (now_ms - last_ts) > self.STALE_THRESHOLD_MS:
            age = now_ms - last_ts if entries else -1
            print(f"[{self.cls_name}] {symbol} stale ({age}ms) – HTTP fetch forțat")
            new = self.get_remote_items(symbol, None)
            if new:
                self.update_cache_per_symbol(symbol, new)
                self._notify_price_subscribers(symbol, new[0][0], new[0][1])
                self.save_state_to_file_if_enabled()
            with self.lock:
                entries = self.cache.get(symbol)
        return entries[0] if entries else None

    def get_price_value(self, symbol: str) -> float:
        """Returnează doar prețul ca float. Drop-in pentru bapi.get_current_price()."""
        entry = self.get_price(symbol)
        return entry[1] if entry else None


# ── Singleton ─────────────────────────────────────────────────────────────────

_current_price_instance: Optional[CacheCurrentPriceManager] = None
_current_price_lock = threading.Lock()

def get_current_price_manager(ws_manager=None, symbols=None) -> CacheCurrentPriceManager:
    """Returnează (și creează dacă e nevoie) singleton-ul CacheCurrentPriceManager."""
    global _current_price_instance
    if _current_price_instance is not None:
        return _current_price_instance
    with _current_price_lock:
        if _current_price_instance is not None:
            return _current_price_instance
        _syms = symbols if symbols is not None else sym.symbols
        _current_price_instance = CacheCurrentPriceManager(
            sync_ts  = 30,
            symbols  = _syms,
            filename = "cache_currentprice.json",
            ws_manager  = ws_manager,
            api_client  = api,
        )
    return _current_price_instance


# ######
# ###### GLOBAL VARIABLE FOR CACHE #######
# ######
     
ORDER_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute     
TRADE_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute
PRICE_SYNC_INTERVAL_SEC = 7 * 60   # 7 minute
PRICE24_SYNC_INTERVAL_SEC = 30         # fallback polling cand WS e inactiv
CURRENTPRICE_SYNC_INTERVAL_SEC = 30   # idem pentru CacheCurrentPriceManager
PRICETREND_SYNC_INTERVAL_SEC = 10 * 60   # 10 minute
ASSETVALUE_SYNC_INTERVAL_SEC = 10 * 60  # 10 minutes 
# TODO: set this to 60 * 60  # 1 hour

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
        "Price24": {
            "class": Cache24PriceManager,
            "filename": None,  # dict per simbol
            "sync_ts": lambda: PRICE24_SYNC_INTERVAL_SEC,
        },
        "CurrentPrice": {
            "class": CacheCurrentPriceManager,
            "filename": "cache_currentprice.json",  # un singur fișier, toți simbolii
            "sync_ts": lambda: CURRENTPRICE_SYNC_INTERVAL_SEC,
        },
        "PriceTrend": {
            "class": CachePriceTrendManager,
            "filename": "cache_price_trend.json",
            "sync_ts": lambda: PRICETREND_SYNC_INTERVAL_SEC,
        },
        "AssetValue": {
            "class": CacheAssetValueManager,
            "filename": "cache_asset_value.json",
            "sync_ts": lambda: ASSETVALUE_SYNC_INTERVAL_SEC,
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
            extra_kwargs = {
                key: value for key, value in config.items()
                if key not in {"class", "filename", "sync_ts"}
            }
            if symbols is None:
                symbols = ["TOTAL"] if name == "AssetValue" else sym.symbols

            if name in ("Price", "Price24"):
                prefix = "cache_price_" if name == "Price" else "cache_24price_"
                cls._instances[name] = {
                    s: manager_class(
                        sync_ts=sync_ts,
                        filename=f"{prefix}{s}.json",
                        symbols=[s],
                        api_client=api,
                        **extra_kwargs,
                    )
                    for s in symbols
                }
            else:
                cls._instances[name] = manager_class(
                    sync_ts=sync_ts,
                    filename=config["filename"],
                    symbols=symbols,
                    api_client=api,
                    **extra_kwargs,
                )

        return cls._instances[name]
        
def get_cache_manager(name, symbols=None):
    return CacheFactory.get(name, symbols)


# ######
# ###### Real-time Binance user stream -> cache actions
# ######
_ws_bridge = None
_ws_bridge_lock = threading.Lock()
_ws_event_stats = defaultdict(int)
import asyncio, websockets
from keys.apikeys import api_key_ws

class BinanceUserDataStreamBridge:
    def __init__(self, event_handler, keepalive_sec=30 * 60):
        self.event_handler = event_handler
        self.keepalive_sec = keepalive_sec
        self._started = False
        self._watchdog_thread = None
        self._signing_key = u._load_ed25519_signing_key()

    def start(self):
        if self._started:
            return
        self._started = True
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
        self._watchdog_thread.daemon = True  # Asigură că acest thread nu blochează închiderea procesului
        self._watchdog_thread.start()
        thread = threading.Thread(target=self._run_loop, daemon=True)
        thread.start()

    def _run_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._listen_forever())

    async def _listen_forever(self):
        if self._signing_key is None:
            _mark_ws_available(False)
            _mark_ws_unhealthy()
            print("[cacheManager][WS] Cheia Ed25519 lipseste, fallback polling.")
            return

        try:
            import websockets as ws_module
        except ImportError:
            _mark_ws_available(False)
            _mark_ws_unhealthy()
            print("[cacheManager][WS] ImportError!")
            return

        #_mark_ws_event_received()
        _mark_ws_available(True)
        last_keepalive = time.time()
        reconnect_delay = 1
        last_ping = time.time()

        while True:
            try:
                url = "wss://ws-api.binance.com:443/ws-api/v3"
                async with ws_module.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    # Login
                    timestamp = int(time.time() * 1000)
                    params_str = f"apiKey={api_key_ws}&timestamp={timestamp}"
                    signature = u._sign_ed25519(self._signing_key, params_str)

                    await ws.send(json.dumps({
                        "id": "login",
                        "method": "session.logon",
                        "params": {
                            "apiKey": api_key_ws,
                            "timestamp": timestamp,
                            "signature": signature
                        }
                    }))

                    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if resp.get("status") != 200:
                        print(f"[cacheManager][WS] Login failed: {resp}")
                        await asyncio.sleep(reconnect_delay)
                        reconnect_delay = min(60, reconnect_delay * 2)
                        continue

                    print("[cacheManager][WS] ✅ Login OK!")

                    # Subscribe
                    await ws.send(json.dumps({
                        "id": "sub",
                        "method": "userDataStream.subscribe"
                    }))

                    _mark_ws_event_received()
                    reconnect_delay = 1
                    last_keepalive = time.time()

                    while True:
                        # Keepalive
                        if time.time() - last_keepalive >= self.keepalive_sec:
                            timestamp = int(time.time() * 1000)
                            params_str = f"apiKey={api_key_ws}&timestamp={timestamp}"
                            signature = u._sign_ed25519(self._signing_key, params_str)
                            await ws.send(json.dumps({
                                "id": "keepalive",
                                "method": "session.logon",
                                "params": {
                                    "apiKey": api_key_ws,
                                    "timestamp": timestamp,
                                    "signature": signature
                                }
                            }))
                            last_keepalive = time.time()

                        # Ping propriu la fiecare WS_LOSS_TIMEOUT_SEC/2 s ca să știm că conexiunea e vie
                        if time.time() - last_ping >= WS_LOSS_TIMEOUT_SEC / 2:
                            print("ping sending ...")
                            await ws.send(json.dumps({"id": "ping", "method": "ping"}))
                            last_ping = time.time()

                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        except asyncio.TimeoutError:
                            # Normal — nu au venit events, continuăm loop-ul
                            print("[cacheManager][WS] Heartbeat (no events in 30s)")
                            #_mark_ws_event_received()  # resetăm watchdog-ul
                            continue

                        event = json.loads(raw)
                        print(f"[WS RAW] {json.dumps(event)[:200]}")
                        # Skip răspuns la ping sau răspunsuri la comenzi (au "id") 
                        if "id" in event:
                            if event["id"]=="ping":
                                print("ping received, reset watchdog!")
                                _mark_ws_event_received()  # resetăm watchdog-ul
                            else: 
                                print(f"[WS] Răspuns comandă ignorat: id={event.get('id')} status={event.get('status')}")
                                continue
                        
                        # ── Nou WS API învelește evenimentul în "event" ──
                        if "event" in event:
                            event = event["event"]
                            
                        _mark_ws_event_received()
                        self.event_handler(event)

            except Exception as e:
                _mark_ws_unhealthy()
                print(f"[cacheManager][WS] Eroare: {e}. Reconnect în {reconnect_delay}s...")
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(60, reconnect_delay * 2)

    def _watchdog_loop(self):
        while True:
            now = time.time()
            with _ws_health_lock:
                age = now - _ws_last_event_ts if _ws_last_event_ts else float("inf")
                ws_available = _ws_available
                ws_healthy = _ws_is_healthy
            if ws_available and ws_healthy and age > WS_LOSS_TIMEOUT_SEC:
                _mark_ws_unhealthy()
                print(f"[cacheManager][WARNING] Fără evenimente WS de {int(age)}s. Fallback polling.")
            time.sleep(5)
#end of BinanceUserDataStreamBridge class

def _upsert_order_from_execution_report(event):
    symbol = event.get("s")
    if not symbol:
        return

    order_cache = get_cache_manager("Order")
    order_item = {
        "orderId": event.get("i"),
        "price": float(event.get("L") or event.get("p") or 0),
        "quantity": float(event.get("l") or event.get("q") or 0),
        "timestamp": int(event.get("T") or event.get("E") or int(time.time() * 1000)),
        "side": event.get("S"),
        "status": event.get("X"),
        "symbol": symbol,
        "eventType": event.get("x"),
    }

    with order_cache.lock:
        bucket = order_cache.cache.setdefault(symbol, [])
        existing_idx = next(
            (idx for idx, item in enumerate(bucket) if str(item.get("orderId")) == str(order_item["orderId"])),
            None
        )
        if existing_idx is not None:
            bucket[existing_idx].update(order_item)
        else:
            bucket.append(order_item)
        order_cache.fetchtime_time_per_symbol[symbol] = int(time.time() * 1000)


def _append_trade_from_execution_report(event):
    if event.get("x") != "TRADE":
        return
    symbol = event.get("s")
    if not symbol:
        return

    trade_cache = get_cache_manager("Trade")
    trade_id = str(event.get("t") or f"{event.get('i')}-{event.get('T')}")
    trade_item = {
        "symbol": symbol,
        "id": trade_id,
        "orderId": event.get("i"),
        "price": event.get("L") or event.get("p"),
        "qty": event.get("l") or event.get("q"),
        "time": int(event.get("T") or event.get("E") or int(time.time() * 1000)),
        "isBuyer": str(event.get("S", "")).upper() == "BUY",
    }

    with trade_cache.lock:
        bucket = trade_cache.cache.setdefault(symbol, [])
        if not any(str(item.get("id")) == trade_id for item in bucket):
            bucket.append(trade_item)
            trade_cache.fetchtime_time_per_symbol[symbol] = int(time.time() * 1000)


def _refresh_asset_value_from_ws_event():
    asset_cache = get_cache_manager("AssetValue", symbols=["TOTAL"])
    asset_cache.update_cache_per_symbol("TOTAL")


def _persist_ws_updated_caches(event_type):
    if event_type == "executionReport":
        get_cache_manager("Order").save_state_to_file_if_enabled()
        get_cache_manager("Trade").save_state_to_file_if_enabled()
    elif event_type in ("balanceUpdate", "outboundAccountPosition"):
        get_cache_manager("AssetValue", symbols=["TOTAL"]).save_state_to_file_if_enabled()


def _handle_binance_ws_event(event):
    print("cacheManager handler call from binance ....")
    event_type = event.get("e")
    if not event_type:
        return

    _ws_event_stats[event_type] += 1

    if event_type == "executionReport":
        if WS_EVENT_LOG_ENABLED:
            print(
                "[cacheManager][WS] executionReport "
                f"symbol={event.get('s')} orderId={event.get('i')} "
                f"status={event.get('X')} execType={event.get('x')} side={event.get('S')}"
            )
        order_cache = get_cache_manager("Order")
        order_cache.query_remote_and_update_cache()

       # _upsert_order_from_execution_report(event)
       # _append_trade_from_execution_report(event)
       # _persist_ws_updated_caches(event_type)
        return

    if event_type in ("balanceUpdate", "outboundAccountPosition"):
        if WS_EVENT_LOG_ENABLED:
            print(
                f"[cacheManager][WS] {event_type} event received"
            )
        #_refresh_asset_value_from_ws_event()
        #_persist_ws_updated_caches(event_type)
        return

def enable_real_ws_event_sync():
    global _ws_bridge
    import sys
    if sys.modules.get("_cacheManager_initialized"):
        # Deja pornit dintr-un import anterior
        if _ws_bridge is not None:
            return _ws_bridge
    with _ws_bridge_lock:
        if _ws_bridge is not None:
            return _ws_bridge
        _ws_bridge = BinanceUserDataStreamBridge(event_handler=_handle_binance_ws_event)
        _ws_bridge.start()
        return _ws_bridge
        
def _initialize_once():
    import sys
    if sys.modules.get("_cacheManager_initialized"):
        print("[cacheManager] Already initialized, skip.")
        return
    sys.modules["_cacheManager_initialized"] = True
    enable_real_ws_event_sync()
    print("⚙️ cacheManager importat! (real WS events + API polling mode)")

_initialize_once()

               
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



###########################################
# """
# BinanceUserDataStreamBridge — fixed version
# ============================================

# Fixes aplicate față de varianta originală:

#   [F1] loop.close() adăugat după run_until_complete — previne leak
#   [F2] Lock în start() — previne race condition la porniri simultane
#   [F3] daemon=True redundant eliminat
#   [F4] stop() implementat + _stop_event pentru watchdog + run_loop
#   [F5] reconnect_delay resetat corect doar la sesiune stabilă
#   [F6] Keepalive: ping via {"method": "ping"}, session.logon doar pentru re-auth
#   [F7] last_ping resetat la fiecare reconectare nouă
#   [F8] _initialize_once() nu mai rulează la import — apelat explicit

# Functionalitate originala pastrata:
#   - _upsert_order_from_execution_report
#   - _append_trade_from_execution_report
#   - _refresh_asset_value_from_ws_event
#   - _persist_ws_updated_caches
#   - _handle_binance_ws_event cu toate ramurile
# """

# import asyncio
# import json
# import threading
# import time
# import websockets

# from collections import defaultdict
# from typing import Callable, Optional

# from keys.apikeys import api_key_ws
# import utils as u   # _load_ed25519_signing_key, _sign_ed25519

# # ─── Constante ────────────────────────────────────────────────────────────────

# WS_URL               = "wss://ws-api.binance.com:443/ws-api/v3"
# WS_RECV_TIMEOUT_SEC  = 30
# WS_LOSS_TIMEOUT_SEC  = 120
# WS_KEEPALIVE_SEC     = 30 * 60
# WS_PING_INTERVAL_SEC = WS_LOSS_TIMEOUT_SEC // 2
# WS_EVENT_LOG_ENABLED = True


# # ─── Health state ─────────────────────────────────────────────────────────────

# _ws_health_lock   = threading.Lock()
# _ws_last_event_ts: Optional[float] = None
# _ws_available     = False
# _ws_is_healthy    = False


# def _mark_ws_available(val: bool):
#     global _ws_available
#     with _ws_health_lock:
#         _ws_available = val


# def _mark_ws_healthy():
#     global _ws_is_healthy
#     with _ws_health_lock:
#         _ws_is_healthy = True


# def _mark_ws_unhealthy():
#     global _ws_is_healthy
#     with _ws_health_lock:
#         _ws_is_healthy = False


# def _mark_ws_event_received():
#     global _ws_last_event_ts
#     with _ws_health_lock:
#         _ws_last_event_ts = time.time()
#         _ws_is_healthy    = True


# # ─── Cache helpers (functionalitate originala) ────────────────────────────────

# def _upsert_order_from_execution_report(event: dict):
#     symbol = event.get("s")
#     if not symbol:
#         return

#     order_cache = get_cache_manager("Order")
#     order_item = {
#         "orderId":   event.get("i"),
#         "price":     float(event.get("L") or event.get("p") or 0),
#         "quantity":  float(event.get("l") or event.get("q") or 0),
#         "timestamp": int(event.get("T") or event.get("E") or int(time.time() * 1000)),
#         "side":      event.get("S"),
#         "status":    event.get("X"),
#         "symbol":    symbol,
#         "eventType": event.get("x"),
#     }

#     with order_cache.lock:
#         bucket = order_cache.cache.setdefault(symbol, [])
#         existing_idx = next(
#             (idx for idx, item in enumerate(bucket)
#              if str(item.get("orderId")) == str(order_item["orderId"])),
#             None,
#         )
#         if existing_idx is not None:
#             bucket[existing_idx].update(order_item)
#         else:
#             bucket.append(order_item)
#         order_cache.fetchtime_time_per_symbol[symbol] = int(time.time() * 1000)


# def _append_trade_from_execution_report(event: dict):
#     if event.get("x") != "TRADE":
#         return
#     symbol = event.get("s")
#     if not symbol:
#         return

#     trade_cache = get_cache_manager("Trade")
#     trade_id    = str(event.get("t") or f"{event.get('i')}-{event.get('T')}")
#     trade_item  = {
#         "symbol":  symbol,
#         "id":      trade_id,
#         "orderId": event.get("i"),
#         "price":   event.get("L") or event.get("p"),
#         "qty":     event.get("l") or event.get("q"),
#         "time":    int(event.get("T") or event.get("E") or int(time.time() * 1000)),
#         "isBuyer": str(event.get("S", "")).upper() == "BUY",
#     }

#     with trade_cache.lock:
#         bucket = trade_cache.cache.setdefault(symbol, [])
#         if not any(str(item.get("id")) == trade_id for item in bucket):
#             bucket.append(trade_item)
#             trade_cache.fetchtime_time_per_symbol[symbol] = int(time.time() * 1000)


# def _refresh_asset_value_from_ws_event():
#     asset_cache = get_cache_manager("AssetValue", symbols=["TOTAL"])
#     asset_cache.update_cache_per_symbol("TOTAL")


# def _persist_ws_updated_caches(event_type: str):
#     if event_type == "executionReport":
#         get_cache_manager("Order").save_state_to_file_if_enabled()
#         get_cache_manager("Trade").save_state_to_file_if_enabled()
#     elif event_type in ("balanceUpdate", "outboundAccountPosition"):
#         get_cache_manager("AssetValue", symbols=["TOTAL"]).save_state_to_file_if_enabled()


# # ─── Event stats + handler ────────────────────────────────────────────────────

# _ws_event_stats: dict = defaultdict(int)


# def _handle_binance_ws_event(event: dict):
#     print("[WS] handler call...")
#     event_type = event.get("e")
#     if not event_type:
#         return

#     _ws_event_stats[event_type] += 1

#     if event_type == "executionReport":
#         if WS_EVENT_LOG_ENABLED:
#             print(
#                 f"[WS] executionReport "
#                 f"symbol={event.get('s')} orderId={event.get('i')} "
#                 f"status={event.get('X')} execType={event.get('x')} side={event.get('S')}"
#             )
#         # Varianta directa din WS event (fara re-fetch):
#         _upsert_order_from_execution_report(event)
#         _append_trade_from_execution_report(event)
#         _persist_ws_updated_caches(event_type)
#         # Alternativ, daca preferi re-fetch complet:
#         # get_cache_manager("Order").query_remote_and_update_cache()
#         return

#     if event_type in ("balanceUpdate", "outboundAccountPosition"):
#         if WS_EVENT_LOG_ENABLED:
#             print(f"[WS] {event_type} received")
#         _refresh_asset_value_from_ws_event()
#         _persist_ws_updated_caches(event_type)
#         return


# # ─── Bridge class ─────────────────────────────────────────────────────────────

# class BinanceUserDataStreamBridge:
#     """
#     Conectare la Binance WebSocket API (ws-api.binance.com) pentru user data events.

#     Flow per sesiune:
#         1. websockets.connect
#         2. session.logon  (Ed25519 auth)
#         3. userDataStream.subscribe
#         4. recv loop cu ping la WS_PING_INTERVAL_SEC
#            și session.logon refresh la WS_KEEPALIVE_SEC
#         5. La orice eroare → reconnect cu exponential backoff

#     Lifecycle:
#         bridge = BinanceUserDataStreamBridge(event_handler=fn)
#         bridge.start()
#         ...
#         bridge.stop()
#     """

#     def __init__(self, event_handler: Callable, keepalive_sec: int = WS_KEEPALIVE_SEC):
#         self.event_handler = event_handler
#         self.keepalive_sec = keepalive_sec
#         self._signing_key  = u._load_ed25519_signing_key()

#         self._start_lock      = threading.Lock()   # [F2]
#         self._started         = False
#         self._stop_event      = threading.Event()  # [F4]
#         self._run_thread:      Optional[threading.Thread] = None
#         self._watchdog_thread: Optional[threading.Thread] = None

#     # ─── Public API ───────────────────────────────────────────────────────────

#     def start(self):
#         """Pornește bridge-ul. Apeluri simultane sunt safe."""
#         with self._start_lock:   # [F2]
#             if self._started:
#                 return
#             self._started = True

#         self._stop_event.clear()

#         self._watchdog_thread = threading.Thread(
#             target=self._watchdog_loop,
#             name="WS-Watchdog",
#             daemon=True,   # [F3] o singura data
#         )
#         self._watchdog_thread.start()

#         self._run_thread = threading.Thread(
#             target=self._run_loop,
#             name="WS-RunLoop",
#             daemon=True,
#         )
#         self._run_thread.start()

#     def stop(self, timeout: float = 5.0):
#         """[F4] Shutdown graceful."""
#         self._stop_event.set()
#         if self._run_thread and self._run_thread.is_alive():
#             self._run_thread.join(timeout=timeout)
#         if self._watchdog_thread and self._watchdog_thread.is_alive():
#             self._watchdog_thread.join(timeout=timeout)
#         self._started = False

#     # ─── Thread: run loop ─────────────────────────────────────────────────────

#     def _run_loop(self):
#         """[F1] loop.close() garantat via finally."""
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)
#         try:
#             loop.run_until_complete(self._listen_forever())
#         except Exception as e:
#             print(f"[WS] run_loop crashed: {e}")
#         finally:
#             loop.close()   # [F1]

#     # ─── Async core ───────────────────────────────────────────────────────────

#     async def _listen_forever(self):
#         if self._signing_key is None:
#             _mark_ws_available(False)
#             _mark_ws_unhealthy()
#             print("[WS] Cheie Ed25519 lipsește, fallback polling.")
#             return

#         _mark_ws_available(True)
#         reconnect_delay = 1

#         while not self._stop_event.is_set():
#             try:
#                 await self._session()
#                 break  # stop_event setat, ieșire normală

#             except Exception as e:
#                 _mark_ws_unhealthy()
#                 print(f"[WS] Eroare sesiune: {e}. Reconnect în {reconnect_delay}s...")
#                 await self._interruptible_sleep(reconnect_delay)
#                 reconnect_delay = min(60, reconnect_delay * 2)
#                 # [F5] reconnect_delay NU e resetat aici — doar după sesiune stabilă

#         print("[WS] listen_forever stopped")

#     async def _session(self):
#         """
#         O sesiune completă. Aruncă excepție la orice eroare,
#         _listen_forever face retry cu backoff.
#         """
#         async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:

#             # 1. Login
#             await self._do_logon(ws, request_id="login")

#             # 2. Subscribe
#             await ws.send(json.dumps({
#                 "id":     "sub",
#                 "method": "userDataStream.subscribe",
#             }))

#             print("[WS] ✅ Login + Subscribe OK!")
#             _mark_ws_event_received()

#             # [F5] Sesiunea e stabilă → resetăm backoff-ul din _listen_forever
#             # prin faptul că nu aruncăm excepție; la return normal backoff-ul
#             # se resetează la 1 la următoarea iterație din while

#             # [F7] Timpii resetați la fiecare sesiune nouă
#             last_keepalive = time.time()
#             last_ping      = time.time()

#             while not self._stop_event.is_set():

#                 # Keepalive: re-autentificare sesiune la fiecare WS_KEEPALIVE_SEC
#                 if time.time() - last_keepalive >= self.keepalive_sec:
#                     await self._do_logon(ws, request_id="keepalive")
#                     last_keepalive = time.time()

#                 # [F6] Ping propriu — method: ping, NU session.logon
#                 if time.time() - last_ping >= WS_PING_INTERVAL_SEC:
#                     await ws.send(json.dumps({"id": "ping", "method": "ping"}))
#                     print("[WS] Ping sent")
#                     last_ping = time.time()

#                 try:
#                     raw = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT_SEC)
#                 except asyncio.TimeoutError:
#                     print(f"[WS] Heartbeat (no events in {WS_RECV_TIMEOUT_SEC}s)")
#                     continue

#                 await self._handle_raw(raw)

#     async def _do_logon(self, ws, request_id: str = "login"):
#         """Trimite session.logon și verifică răspunsul."""
#         timestamp  = int(time.time() * 1000)
#         params_str = f"apiKey={api_key_ws}&timestamp={timestamp}"
#         signature  = u._sign_ed25519(self._signing_key, params_str)

#         await ws.send(json.dumps({
#             "id":     request_id,
#             "method": "session.logon",
#             "params": {
#                 "apiKey":    api_key_ws,
#                 "timestamp": timestamp,
#                 "signature": signature,
#             },
#         }))

#         resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
#         if resp.get("status") != 200:
#             raise RuntimeError(f"[WS] session.logon failed (id={request_id}): {resp}")

#         print(f"[WS] session.logon OK (id={request_id})")

#     async def _handle_raw(self, raw: str):
#         """Parsează și rutează un mesaj primit."""
#         try:
#             event = json.loads(raw)
#         except json.JSONDecodeError as e:
#             print(f"[WS] JSON decode error: {e}")
#             return

#         print(f"[WS RAW] {json.dumps(event)[:200]}")

#         # Răspunsuri la comenzi (au "id")
#         if "id" in event:
#             if event["id"] == "ping":
#                 print("[WS] Pong received, watchdog reset")
#                 _mark_ws_event_received()
#             else:
#                 print(f"[WS] Command ack: id={event.get('id')} status={event.get('status')}")
#             return

#         # Noul WS API învelește evenimentul în "event"
#         if "event" in event:
#             event = event["event"]

#         _mark_ws_event_received()
#         self.event_handler(event)

#     # ─── Thread: watchdog ─────────────────────────────────────────────────────

#     def _watchdog_loop(self):
#         """[F4] Iese curat când stop_event e setat."""
#         while not self._stop_event.is_set():
#             now = time.time()
#             with _ws_health_lock:
#                 age          = now - _ws_last_event_ts if _ws_last_event_ts else float("inf")
#                 ws_available = _ws_available
#                 ws_healthy   = _ws_is_healthy

#             if ws_available and ws_healthy and age > WS_LOSS_TIMEOUT_SEC:
#                 _mark_ws_unhealthy()
#                 print(f"[WS][WARNING] Fără evenimente de {int(age)}s. Fallback polling.")

#             self._stop_event.wait(timeout=5)   # [F4] sleep interruptibil

#         print("[WS] Watchdog stopped")

#     async def _interruptible_sleep(self, delay: float, step: float = 0.2):
#         elapsed = 0.0
#         while elapsed < delay and not self._stop_event.is_set():
#             await asyncio.sleep(min(step, delay - elapsed))
#             elapsed += step


# # ─── Singleton ────────────────────────────────────────────────────────────────

# _ws_bridge:      Optional[BinanceUserDataStreamBridge] = None
# _ws_bridge_lock = threading.Lock()


# def enable_real_ws_event_sync() -> BinanceUserDataStreamBridge:
#     global _ws_bridge
#     with _ws_bridge_lock:
#         if _ws_bridge is not None:
#             return _ws_bridge
#         _ws_bridge = BinanceUserDataStreamBridge(
#             event_handler=_handle_binance_ws_event,
#         )
#         _ws_bridge.start()
#         return _ws_bridge


# def disable_real_ws_event_sync():
#     global _ws_bridge
#     with _ws_bridge_lock:
#         if _ws_bridge is not None:
#             _ws_bridge.stop()
#             _ws_bridge = None


# # ─── [F8] Init explicit — NU la import ───────────────────────────────────────
# #
# # Apelează enable_real_ws_event_sync() acolo unde pornești aplicația:
# #
# #   from binance_user_stream_bridge import enable_real_ws_event_sync
# #   enable_real_ws_event_sync() """