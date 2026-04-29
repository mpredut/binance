import json
import os
import time
import datetime
import asyncio
import importlib
import builtins
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
from collections import defaultdict
import threading

#my imports
import log
import utils as u
import symbols as sym
import bapi as api

#from log import PRINT_CONTEXT


# disable logs by redefine with dummy
#def print(*args, **kwargs):
#   pass
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
        self.lock = threading.Lock()
      
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

    def update_cache(self):
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self.__rebuild_fetchtime_times()
        
        for symbol in self.symbols:
            self.update_cache_per_symbol(symbol)


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
                    self.update_cache()
                else:
                    print(f"[{self.cls_name}] Skip polling (WS-only mode active, WS healthy).")
                print(f"[{self.cls_name}] save state is {self.save_state}.")
                if self.save_state:
                    self.save_state_to_file()
                print(f"[{self.cls_name}] Sync completed for {self.symbols}")
                time.sleep(self.sync_ts)

        self.thread = threading.Thread(target=run, daemon=False)
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

        now_sec = int(time.time())
        snapshot = {
            "timestamp": now_sec,
            "datetime_local": datetime.now().isoformat(timespec="seconds"),
            "total_value_usdt": round(float(total_usdt), 8),
        }
        return [snapshot]

            
# ###### 
# ###### GLOBAL VARIABLE FOR CACHE ####### 
# ###### 
     
ORDER_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute     
TRADE_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute
PRICE_SYNC_INTERVAL_SEC = 7 * 60   # 7 minute
PRICETREND_SYNC_INTERVAL_SEC = 10 * 60   # 10 minute
ASSETVALUE_SYNC_INTERVAL_SEC = 60 * 60  # 1 hour

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
            if symbols is None:
                symbols = ["TOTAL"] if name == "AssetValue" else sym.symbols

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
        get_cache_manager("Order").save_state_to_file()
        get_cache_manager("Trade").save_state_to_file()
    elif event_type in ("balanceUpdate", "outboundAccountPosition"):
        get_cache_manager("AssetValue", symbols=["TOTAL"]).save_state_to_file()


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
        order_cache.update_cache()

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
