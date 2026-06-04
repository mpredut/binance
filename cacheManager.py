import json
import os
import contextlib
import cachepaths
import time
import datetime
import asyncio
import threading
import importlib
import builtins
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from typing import Optional

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


@contextlib.contextmanager
def atomic_write(path):
    """Context-manager pentru scriere atomică: dă un file handle pe un tmp UNIC,
    iar la ieșirea cu succes face os.replace(tmp, path) (rename atomic). La eroare
    șterge tmp-ul și re-ridică. Un cititor cross-process vede ori fișierul vechi,
    ori cel nou complet — niciodată unul parțial. Folosit pt JSON și JSONL."""
    # tmp UNIC (pid+thread) → scrieri concurente (alt thread/proces) nu se calcă pe
    # același fișier temporar; os.replace rămâne atomic (last-writer-wins).
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    f = open(tmp, "w")
    try:
        yield f
        f.close()
        os.replace(tmp, path)
    except BaseException:
        f.close()
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, obj, indent=None):
    """Scriere atomică a unui JSON (vezi atomic_write). Ridică excepția la eroare."""
    with atomic_write(path) as f:
        json.dump(obj, f, indent=indent)

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
    builtins.print("UNHAPPY -:( WS marcat ca UNHEALTHY")
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
    # ── Politică retenție/rotație pentru cache-uri append (verificată periodic) ──
    RETENTION_DAYS              = 730              # ~2 ani: șterge intrările mai vechi
    MAX_FILE_BYTES             = 1_000_000_000    # ~1 GB: peste asta → rotație
    RETENTION_CHECK_INTERVAL_SEC = 7 * 24 * 3600  # verificare săptămânală
    ROTATE_KEEP_FRACTION       = 0.10             # la rotație păstrăm ultimele 10%
    RESYNC_INTERVAL_SEC        = 10 * 60          # reconciliere mem↔fișier la 10 min
    DEDUP_WINDOW               = 100             # dedup per-update doar față de ultimele N items

    def __init__(self, sync_ts, symbols, filename, append_mode = True, api_client=api,
                 append_persist=False):
        self.cls_name = self.__class__.__name__

        #self.enable_print = True
        #global PRINT_CONTEXT
        #log.PRINT_CONTEXT = self

        self.sync_ts = sync_ts
        self.symbols = symbols
        self.filename = cachepaths.cache_path(filename)   # → subfolderul cachedb/
        self.append_mode = append_mode
        self.api_client = api_client
        # Persistență prin APPEND (JSONL) — pentru cache-uri pur-append (Trade,
        # AssetValue): scriem doar liniile NOI, nu rescriem tot fișierul.
        self.append_persist = append_persist
        self._persisted_counts = {}   # symbol → câte items sunt deja pe disc

        self.days_back = 30

        self.cache = {}
        self.fetchtime_time_per_symbol = {}

        self.thread = None
        self.save_state = False
        # dacă True, bucla de sync doarme un interval înainte de prima iterație.
        # Respectă valoarea pre-setată de subclase ÎNAINTE de super().__init__
        # (thread-ul pornește în super → trebuie setat din timp).
        if not hasattr(self, "_first_sleep"):
            self._first_sleep = False
        self.lock = threading.RLock()

        # Subscriber pattern comun — clasele derivate forward prețuri către
        # alți manageri / PriceWindow prin _notify_price_subscribers().
        # Init înainte de periodic_sync (thread-ul poate notifica imediat).
        if not hasattr(self, "_price_subscribers"):
            self._price_subscribers = []

        self.fallback_time_default = int(time.time() * 1000) - self.days_back*24*60*60*1000

        # function calls here after all inint vars
        self.load_state()
        self.periodic_sync(sync_ts, False)

    # ── Subscriber pattern (forward prețuri) ──────────────────────────────────

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
                print(f"[{self.cls_name}] Eroare notificare subscriber {sub}: {e}")
    

    #def get_all_symbols_from_cache(self):
    #    return list(set(t.get("symbol") for t in self.cache if "symbol" in t))
    def get_all_symbols_from_cache(self):
        with self.lock:
            return list(self.cache.keys())       
        
    def rebuild_fetchtime_times(self):
        """Default: None → __rebuild_fetchtime_times deduce generic din timestamp-urile
        item-urilor (dict 'time'/'timestamp' sau listă [ts, ...]). Subclasele pot
        suprascrie dacă au o logică specifică."""
        return None
    
    
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
        if self.append_persist:
            self._load_jsonl()
            if not self.cache:
                self.query_remote_and_update_cache()
                self.save_state_to_file_if_enabled()
            return
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


    # ── Reziliență mem↔fișier: freshness + guard + resync ─────────────────────
    def _mem_max_ts(self):
        """Freshness memoriei: cel mai recent fetchtime (ms)."""
        if not self.fetchtime_time_per_symbol:
            return 0
        try:
            return max(self.fetchtime_time_per_symbol.values())
        except Exception:
            return 0

    def _persisted_max_ts(self):
        """Freshness fișierului din sidecar .meta (citire ieftină)."""
        try:
            with open(self.filename + ".meta") as mf:
                return json.load(mf).get("max_ts", 0)
        except Exception:
            return 0

    def _write_meta(self):
        """Sidecar mic cu freshness (max_ts) + fetchtime + counts. Atomic."""
        try:
            atomic_write_json(self.filename + ".meta",
                              {"max_ts": self._mem_max_ts(),
                               "saved_at": int(time.time() * 1000),
                               "fetchtime": self.fetchtime_time_per_symbol,
                               "counts": self._persisted_counts})
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] meta {self.filename}: {e}")

    def save_state_to_file_if_enabled(self):
        """Scrie DOAR dacă save_state e activat (writer). No-op pentru readeri."""
        if self.save_state:
            self.save_state_to_file()

    def save_state_to_file(self):
        """Scrie EFECTIV pe disc (indiferent de save_state) — pentru writer/failover.
        Are guard: NU suprascrie date mai NOI din fișier (alt proces între timp)."""
        if self._persisted_max_ts() > self._mem_max_ts():
            builtins.print(f"[{self.cls_name}][resync] fișier mai nou decât memoria → "
                           f"refuz suprascrierea cu date vechi ({self.filename})")
            return
        if self.append_persist:
            self._save_jsonl_append()
            self._write_meta()
            return
        try:
            with self.lock:
                atomic_write_json(self.filename,
                                  {"items": self.cache,
                                   "fetchtime": self.fetchtime_time_per_symbol},
                                  indent=1)
                print(f"[{self.cls_name}][info] Save cache to file {self.filename}")
            self._write_meta()
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] La salvarea fișierului cache {self.filename} / .tmp : {e}")

    def _reload_from_disk(self):
        """Reîncarcă cache-ul din fișier (când fișierul e mai nou — alt proces)."""
        if self.append_persist:
            self._load_jsonl()
            return
        if os.path.exists(self.filename):
            try:
                with open(self.filename) as f:
                    data = json.load(f)
                with self.lock:
                    items = data.get("items", {})
                    if isinstance(items, dict):
                        self.cache = items
                    self.fetchtime_time_per_symbol = data.get("fetchtime", {})
            except Exception as e:
                print(f"[{self.cls_name}][Eroare] reload {self.filename}: {e}")

    def resync_mem_file(self):
        """Reconciliere periodică: fișier mai nou → reîncarc; memorie mai nouă → scriu."""
        file_ts = self._persisted_max_ts()
        mem_ts = self._mem_max_ts()
        if file_ts > mem_ts:
            builtins.print(f"[{self.cls_name}][resync] fișier mai nou → reîncarc ({self.filename})")
            self._reload_from_disk()
        elif mem_ts > file_ts and self.save_state:
            self.save_state_to_file_if_enabled()

    # ── Persistență APPEND (JSONL) pentru cache-uri pur-append ────────────────
    def _save_jsonl_append(self):
        """Scrie DOAR items-urile noi (delta de la ultimul flush), prin append.
        Nu rescrie tot fișierul. (meta o scrie save_state_to_file_if_enabled)."""
        try:
            with self.lock:
                with open(self.filename, "a") as f:
                    for symbol, items in self.cache.items():
                        start = self._persisted_counts.get(symbol, 0)
                        if start > len(items):   # cache a fost golit/scurtat → resync
                            start = 0
                        for item in items[start:]:
                            f.write(json.dumps({"s": symbol, "i": item}) + "\n")
                        self._persisted_counts[symbol] = len(items)
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] append JSONL {self.filename}: {e}")

    def _load_jsonl(self):
        """Încarcă fișierul JSONL (toate liniile) → cache. fetchtime din sidecar."""
        with self.lock:
            self.cache = {}
            if os.path.exists(self.filename):
                with open(self.filename, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                            self.cache.setdefault(rec["s"], []).append(rec["i"])
                        except Exception:
                            continue   # linie parțială/coruptă (crash la append) — o sărim
            self._persisted_counts = {s: len(v) for s, v in self.cache.items()}
            metaf = self.filename + ".meta"
            if os.path.exists(metaf):
                try:
                    with open(metaf) as mf:
                        self.fetchtime_time_per_symbol = json.load(mf).get("fetchtime", {})
                except Exception:
                    pass

    def compact_jsonl(self):
        """Rescrie fișierul JSONL din memorie + ELIMINĂ DUPLICATELE (în memorie și pe
        disc). Dedup complet aici (periodic) — ca să nu plătim costul la fiecare update."""
        if not self.append_persist:
            return
        try:
            with self.lock:
                for symbol, items in list(self.cache.items()):
                    seen = set()
                    deduped = []
                    for item in items:
                        k = json.dumps(item, sort_keys=True)
                        if k not in seen:
                            seen.add(k)
                            deduped.append(item)
                    self.cache[symbol] = deduped   # dedup și în memorie
                with atomic_write(self.filename) as f:
                    for symbol, items in self.cache.items():
                        for item in items:
                            f.write(json.dumps({"s": symbol, "i": item}) + "\n")
                self._persisted_counts = {s: len(v) for s, v in self.cache.items()}
            self._write_meta()
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] compact JSONL {self.filename}: {e}")

    @staticmethod
    def _entry_timestamp_ms(item):
        """Timestamp (ms) al unei intrări — dict (time/timestamp) sau listă [ts, val]."""
        if isinstance(item, dict):
            return item.get("time") or item.get("timestamp") or 0
        if isinstance(item, (list, tuple)) and item:
            return item[0]
        return 0

    def maintain_append_persist(self):
        """Mentenanță periodică (săptămânal) pentru cache-uri append:
          1. PRUNE: șterge intrările mai vechi de RETENTION_DAYS.
          2. ROTAȚIE: dacă fișierul > MAX_FILE_BYTES → arhivează (alt nume) și
             păstrează doar ultimele ROTATE_KEEP_FRACTION în fișierul curent."""
        if not self.append_persist:
            return
        # 1) prune time-based
        cutoff_ms = int((time.time() - self.RETENTION_DAYS * 24 * 3600) * 1000)
        changed = False
        with self.lock:
            for symbol, items in list(self.cache.items()):
                kept = [it for it in items if self._entry_timestamp_ms(it) >= cutoff_ms]
                if len(kept) != len(items):
                    self.cache[symbol] = kept
                    changed = True
        if changed:
            builtins.print(f"[{self.cls_name}][maintain] prune >{self.RETENTION_DAYS}z din {self.filename}")
            self.compact_jsonl()
        # 2) rotație size-based
        try:
            if os.path.exists(self.filename) and os.path.getsize(self.filename) > self.MAX_FILE_BYTES:
                self._rotate_keep_latest()
        except OSError:
            pass

    def _rotate_keep_latest(self):
        """Arhivează fișierul curent și păstrează doar ultimele ROTATE_KEEP_FRACTION
        înregistrări (per simbol) în fișierul cu numele curent."""
        with self.lock:
            archive = f"{self.filename}.{int(time.time())}.archive"
            try:
                os.replace(self.filename, archive)   # mută istoricul complet în arhivă
            except OSError as e:
                builtins.print(f"[{self.cls_name}][maintain] arhivare eșuată: {e}")
                return
            for symbol, items in self.cache.items():
                keep_n = max(1, int(len(items) * self.ROTATE_KEEP_FRACTION))
                self.cache[symbol] = items[-keep_n:]
            self._persisted_counts = {}
            self.compact_jsonl()   # rescrie fișierul curent doar cu ce-am păstrat
        builtins.print(f"[{self.cls_name}][maintain] ROTAȚIE: arhivat → {archive}, "
                       f"păstrat ultimele {int(self.ROTATE_KEEP_FRACTION*100)}%")

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
                # Dedup DOAR față de fereastra recentă (polling re-aduce date suprapuse
                # recente). Ieftin O(DEDUP_WINDOW) în loc de O(tot cache-ul) per update.
                # Dedup-ul complet se face periodic în compact_jsonl.
                cache_copy = list(self.cache.get(symbol, []))[-self.DEDUP_WINDOW:]
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

    def _persist_items(self, symbol, new_items):
        """Pasul de persistare per simbol. Default: scrie în cache.
        Subclasele pot suprascrie (ex. CacheCurrentPriceManager → _push_price
        ca să înregistreze timestamp-ul și să notifice subscriberii)."""
        self.update_cache_per_symbol(symbol, new_items)

    def query_remote_and_update_cache(self):
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self.__rebuild_fetchtime_times()

        for symbol in list(self.symbols):
            startTime = self.fetchtime_time_per_symbol.get(symbol, self.fallback_time_default)
            new_items = self.get_remote_items(symbol=symbol, startTime=startTime)
            if not new_items:
                print(f"[{self.cls_name}][Info] {symbol}:  No remote items starting with {u.timestampToTime(startTime)} ")
                continue

            self._persist_items(symbol, new_items)

    def on_items_update(self, symbol, items):
        print(f"[{self.cls_name}][Info] {symbol}: WS Items updated to {items}")
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self.__rebuild_fetchtime_times()
        self.update_cache_per_symbol(symbol, items)
        
    def _should_poll(self):
        """Decide dacă bucla de sync face poll la API. Suprascriabil de subclase:
          - Cache24PriceManager → False (push-based: prețurile vin prin on_price_update)
          - CacheCurrentPriceManager → doar când WS e mort (fallback)
        Default: gating-ul global WS_ONLY_MODE."""
        return _should_poll_for_manager(self.cls_name)

    def periodic_sync(self, sync_ts=None, save_state=True):
        if sync_ts is not None:
            self.sync_ts = sync_ts
        self.save_state = save_state  # actualizează save_state indiferent

        if self.thread is not None and self.thread.is_alive():
            return self.thread  # thread deja pornit, returnează-l (citește sync_ts dinamic)

        def run():
            if self._first_sleep:
                time.sleep(self.sync_ts)   # prima iterație după un interval (ex. CurrentPrice)
            last_maint = time.time()
            last_resync = time.time()
            while True:
                if self._should_poll():
                    self.query_remote_and_update_cache()
                self.save_state_to_file_if_enabled()   # are guard anti-suprascriere date vechi
                # Reconciliere mem↔fișier periodică (la 10 min)
                if (time.time() - last_resync) > self.RESYNC_INTERVAL_SEC:
                    self.resync_mem_file()
                    last_resync = time.time()
                # Mentenanță retenție/rotație (append): săptămânal
                if self.append_persist and (time.time() - last_maint) > self.RETENTION_CHECK_INTERVAL_SEC:
                    self.maintain_append_persist()
                    last_maint = time.time()
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
        # creștere LENTĂ (doar la trade real) → full-rewrite e ok
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)

    def _is_valid_trade(self, trade):
        required_keys = ['symbol', 'id', 'orderId', 'price', 'qty', 'time', 'isBuyer']
        return all(k in trade for k in required_keys)

    def get_remote_items(self, symbol, startTime):
        import importlib
        apitrades = importlib.import_module("bapi_trades")
        
        current_time = int(time.time() * 1000)
        backdays = int((current_time - startTime) / (24 * 60 * 60 * 1000))
        
        # clientul INJECTAT (self.api_client), paginat → nu trunchiem la 1000 când
        # perioada are mai multe trade-uri.
        import bapi_allorders as apiorders
        new_trades = apiorders.paginate_my_trades(self.api_client.client, symbol, startTime, limit=1000)
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
        # pur-append, creștere CONTINUĂ (istoric preț la 7 min) → append JSONL
        super().__init__(sync_ts, symbols, filename, append_mode=True,
                         api_client=api, append_persist=True)

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

    # subscribe_price / unsubscribe_price / _notify_price_subscribers — moștenite
    # din CacheManagerInterface.

    # ── Callback de la CacheCurrentPriceManager ───────────────────────────────

    def on_price_update(self, symbol: str, ts_ms: int, price: float):
        """Apelat de CacheCurrentPriceManager la fiecare preț nou (WS sau HTTP)."""
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self._CacheManagerInterface__rebuild_fetchtime_times()
        self.update_cache_per_symbol(symbol, [[ts_ms, price]])
        self._trim_old_data(symbol)
        self._notify_price_subscribers(symbol, ts_ms, price)

    def _trim_old_data(self, symbol):
        cutoff_ms = int((time.time() - self.KEEP_HOURS * 3600) * 1000)
        with self.lock:
            entries = self.cache.get(symbol)
            if entries:
                self.cache[symbol] = [e for e in entries if e[0] >= cutoff_ms]

    def get_recent_entries(self, symbol: str, last_seconds: float) -> list:
        """Returnează intrările [ts_ms, price] din ultimele `last_seconds` secunde."""
        cutoff_ms = int((time.time() - last_seconds) * 1000)
        with self.lock:
            entries = self.cache.get(symbol, [])
            return [e for e in entries if e[0] >= cutoff_ms]

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

    def _should_poll(self):
        # Push-based: prețurile vin EXCLUSIV prin on_price_update (de la CurrentPrice).
        # Folosește base periodic_sync (doar save + resync + maintain), fără poll.
        return False


class CachePriceTrendManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols, filename, api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=False)

    # get_all_symbols_from_cache → moștenit din base (list(self.cache.keys()))

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
        # creștere lentă (1 / 10 min) → full-rewrite e ok
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

        if not isinstance(total_usdt, (int, float)) or total_usdt <= 0:
            print(f"[{self.cls_name}][Eroare] Valoarea totala invalidă: {total_usdt}")
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

    WS_TIMEOUT_SEC     = 15      # WS considerat mort după 15s fără niciun event
    STALE_THRESHOLD_MS = 5_000  # get_price() forțează HTTP dacă prețul e > 5s vechi
    FREQ_WINDOW_SEC    = 60     # fereastra de măsurare a frecvenței update-urilor

    def __init__(self, sync_ts, symbols, filename, ws_manager=None, api_client=api):
        self._ws_manager        = ws_manager
        self._ws_last_event_ts  = 0.0      # setat înainte de super() !
        self._price_subscribers = []       # idem (base __init__ respectă hasattr)
        self._update_timestamps: dict = defaultdict(deque)  # idem — înainte de super()
        self._first_sleep       = True     # nu face fallback HTTP imediat (lasă WS să se conecteze)
        super().__init__(sync_ts, symbols, filename, append_mode=False, api_client=api_client)
        if ws_manager is not None:
            ws_manager.subscribe(self)

    # ── WS health ────────────────────────────────────────────────────────────

    def _ws_is_healthy(self):
        return (time.time() - self._ws_last_event_ts) < self.WS_TIMEOUT_SEC

    # subscribe_price / unsubscribe_price / _notify_price_subscribers — moștenite
    # din CacheManagerInterface.

    # ── WS callback (suprascrie metoda din interfață) ─────────────────────────

    def _record_price_timestamp(self, symbol: str) -> None:
        now = time.time()
        dq = self._update_timestamps[symbol]
        dq.append(now)
        cutoff = now - self.FREQ_WINDOW_SEC
        while dq and dq[0] < cutoff:
            dq.popleft()

    def _push_price(self, symbol: str, price: float) -> None:
        """Injectează un preț în cache și notifică subscriberii.
        Nu atinge _ws_last_event_ts — folosit de polling thread și get_price.
        _ws_last_event_ts e actualizat DOAR de on_items_update (eveniment WS real)."""
        ts_ms = int(time.time() * 1000)
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self._CacheManagerInterface__rebuild_fetchtime_times()
        self._record_price_timestamp(symbol)
        self.update_cache_per_symbol(symbol, [[ts_ms, price]])
        self._notify_price_subscribers(symbol, ts_ms, price)

    def on_items_update(self, symbol: str, items):
        """Callback pentru evenimente WS reale — actualizează și health-ul WS."""
        self._ws_last_event_ts = time.time()   # doar evenimentele WS reale
        price = items[0] if items else None
        if price is None:
            return
        self._push_price(symbol, price)

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

    def _persist_items(self, symbol, new_items):
        """Override: în loc de scriere simplă în cache, folosim _push_price ca
        să înregistrăm timestamp-ul (pt sample_rate) și să notificăm subscriberii.
        new_items = [[ts_ms, price]]."""
        self._push_price(symbol, new_items[0][1])

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

    def _should_poll(self):
        # Fetch HTTP DOAR ca fallback, când WS e mort. query_remote_and_update_cache
        # → _persist_items (override) propagă prin chain via _push_price.
        return not self._ws_is_healthy()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_sample_rate(self, symbol: str, fallback: float = 1.0) -> float:
        """Intervalul mediu în secunde între update-uri în ultimele FREQ_WINDOW_SEC secunde.
        Returnează `fallback` dacă nu există suficiente măsurători."""
        dq = self._update_timestamps.get(symbol)
        if not dq or len(dq) < 2:
            return fallback
        return (dq[-1] - dq[0]) / (len(dq) - 1)

    def get_update_frequency(self, symbol: str) -> float:
        """Update-uri/secundă în ultimele FREQ_WINDOW_SEC secunde."""
        dq = self._update_timestamps.get(symbol)
        if not dq or len(dq) < 2:
            return 0.0
        return len(dq) / self.FREQ_WINDOW_SEC

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
                self._push_price(symbol, new[0][1])
                self.save_state_to_file_if_enabled()
            with self.lock:
                entries = self.cache.get(symbol)
        return entries[0] if entries else None

    def get_price_value(self, symbol: str) -> float:
        """Returnează doar prețul ca float. Drop-in pentru bapi.get_current_price()."""
        entry = self.get_price(symbol)
        return entry[1] if entry else None

    def attach_ws_manager(self, ws_manager) -> None:
        """Conectează un BinanceWebSocketManager ca sursă primară de preț.
        ws_manager.subscribe(self) → on_items_update(symbol, [price]) la fiecare tick.
        Idempotent (ws_manager.subscribe deduplichează)."""
        if ws_manager is None:
            return
        self._ws_manager = ws_manager
        ws_manager.subscribe(self)


# ── Singleton ─────────────────────────────────────────────────────────────────

_current_price_instance: Optional[CacheCurrentPriceManager] = None
_current_price_lock = threading.Lock()

def get_current_price_manager(ws_manager=None, symbols=None, sync_ts=None) -> CacheCurrentPriceManager:
    """Returnează (și creează dacă e nevoie) singleton-ul CacheCurrentPriceManager.

    sync_ts : intervalul de polling în secunde.
              - None (default): NU modifică sync_ts-ul existent. La prima creare
                folosește CURRENTPRICE_SYNC_INTERVAL_SEC.
              - valoare explicită: setează/actualizează live (thread-ul îl
                citește dinamic la fiecare iterație).
              IMPORTANT: apelurile interne (ex. Cache24PriceManager.get_remote_items)
              trebuie să folosească None ca să nu suprascrie configurarea din main.
    """
    global _current_price_instance
    if _current_price_instance is not None:
        if sync_ts is not None:
            _current_price_instance.sync_ts = sync_ts   # actualizare live
        if ws_manager is not None:
            _current_price_instance.attach_ws_manager(ws_manager)
        return _current_price_instance
    with _current_price_lock:
        if _current_price_instance is not None:
            if sync_ts is not None:
                _current_price_instance.sync_ts = sync_ts
            if ws_manager is not None:
                _current_price_instance.attach_ws_manager(ws_manager)
            return _current_price_instance
        _syms = symbols if symbols is not None else sym.symbols
        _current_price_instance = CacheCurrentPriceManager(
            sync_ts     = sync_ts if sync_ts is not None else CURRENTPRICE_SYNC_INTERVAL_SEC,
            symbols     = _syms,
            filename    = "cache_currentprice.json",
            ws_manager  = ws_manager,
            api_client  = api,
        )
    return _current_price_instance


# ######
# ###### CacheInstantTrendManager — ferestre + trend calculat + cache cross-process
# ######

class CacheInstantTrendManager:
    """Deține ferestrele de preț per simbol, calculează trendul instant și
    cache-uiește snapshot-ul într-un fișier partajat ÎNTRE PROCESE.

    Writer (tradeall): start_computation() construiește ferestrele, se abonează
      la Cache24 și la fiecare tick publică gradientul + epsilon (canal rapid).
    Reader (rtrade, bapi_placeorder): folosește doar gate-ul, citind din fișier.

    API de calcul : get_instant_trend(symbol), get_window/get_analyzer(symbol)
    API store     : update_snapshot, get_snapshot, get_all_snapshots
    API gate      : is_favorable_to_wait(side, symbol), wait_for_favorable_entry(...)
    """
    EPSILON_K         = 1.0     # k * stddev(gradient) — prag de zgomot informat
    FAVORABLE_REL_EPS = 1e-5    # fallback relativ la preț dacă lipsește epsilon
    TREND_STALE_SEC   = 15.0    # snapshot mai vechi → nu mai întârziem
    # Praguri (PROCENT) pentru check_price_change — per fereastră (small/big), ca în tradeall.
    # Sunt procente, deci scale-invariante ca formulă; pot fi suprascrise per-simbol prin
    # parametrul `thresholds` (vezi __init__), fiindcă volatilitatea diferă (BTC vs TAO).
    PRICE_CHANGE_THRESHOLD_SMALL = u.calculate_difference_percent(60000, 60000 - 310)
    PRICE_CHANGE_THRESHOLD_BIG   = u.calculate_difference_percent(97000, 95000 - 377)
    FULL_EVAL_INTERVAL_SEC = 3.0   # cadența calculului GREU (metrici complete)
    FLUSH_INTERVAL_SEC     = 0.5   # cadența scrierii pe fișier (doar writer-ul)
    # Durate ferestre (secunde) — o LISTĂ de timpi. TOATE sunt slice-uri din ACELAȘI
    # Cache24 (24h), deci ≤ 24h. Numărul de sample-uri e calculat dinamic din rata reală.
    # Cea mai mică fereastră = "primary" (canalul rapid gradient_recent + get_instant_trend).
    WINDOW_SECONDS = [3.7 * 60, 2.5 * 60 * 60]   # [3.7 min momentum, 2.5 ore trend]

    def __init__(self, symbols, filename="cache_instant_trend.json", writer=False,
                 window_seconds=None, thresholds=None):
        self.symbols = list(symbols)
        self.filename = cachepaths.cache_path(filename)   # → subfolderul cachedb/
        self.writer = writer   # doar writer-ul scrie fișierul (ex. procesul cacheManager.py)
        # Listă de N timpi (secunde), sortată crescător → window_seconds[0] = primary.
        secs = list(window_seconds) if window_seconds else list(self.WINDOW_SECONDS)
        self.window_seconds = sorted(float(s) for s in secs)
        # Praguri check_price_change — per FEREASTRĂ și (opțional) per SIMBOL. `thresholds`:
        #   - callable(symbol, seconds) -> procent           (cel mai general)
        #   - dict {seconds: procent}                         (per fereastră, toate simbolurile)
        #   - dict {symbol: {seconds: procent}}               (per simbol + fereastră)
        #   - None → default: small→PRICE_CHANGE_THRESHOLD_SMALL, restul→..._BIG
        self._threshold_fn = self._build_threshold_fn(thresholds)
        self._mem = {}
        self._lock = threading.RLock()
        self._file_mtime = None
        self._file_cache = None
        # stare writer (populate de start_computation): dict[symbol][secunde] -> PriceWindow / WindowAnalyzer
        self.windows = {}
        self.analyzers = {}
        self.current_price_mgr = None
        self._computing = False
        self._full_eval_thread = None
        self._flush_thread = None

    # durate ferestrelor extreme (cea mai mică / cea mai mare), derivate din listă
    @property
    def window_small_sec(self):
        return self.window_seconds[0]

    @property
    def window_big_sec(self):
        return self.window_seconds[-1]

    def _build_threshold_fn(self, thresholds):
        """Normalizează `thresholds` la o funcție (symbol, seconds) -> procent."""
        if callable(thresholds):
            return thresholds
        small, big = self.window_seconds[0], self.window_seconds[-1]

        def per_window_default(sec):
            # default clasic: cea mai mică fereastră → SMALL, restul → BIG
            return self.PRICE_CHANGE_THRESHOLD_SMALL if float(sec) <= small else self.PRICE_CHANGE_THRESHOLD_BIG

        if isinstance(thresholds, dict):
            # dict per-simbol {symbol: {sec: pct}} sau per-fereastră {sec: pct}
            per_symbol = all(isinstance(v, dict) for v in thresholds.values()) and len(thresholds) > 0
            if per_symbol:
                tbl = {sym: {float(k): v for k, v in d.items()} for sym, d in thresholds.items()}
                return lambda sym, sec: tbl.get(sym, {}).get(float(sec), per_window_default(sec))
            tbl = {float(k): v for k, v in thresholds.items()}
            return lambda sym, sec: tbl.get(float(sec), per_window_default(sec))

        return lambda sym, sec: per_window_default(sec)

    def threshold_for(self, symbol, seconds):
        """Pragul (procent) pentru o fereastră a unui simbol."""
        return self._threshold_fn(symbol, float(seconds))

    # ── Writer: construiește ferestre + abonare la Cache24 ────────────────────
    def start_computation(self, cache24_managers=None, current_price_mgr=None, run_full_eval=False):
        """Construiește ferestrele + canalul rapid. Dacă run_full_eval=True,
        pornește și bucla de calcul COMPLET (slope_small/big, pos, etc.) care
        scrie snapshot-ul complet — folosit de procesul cacheManager.py ca
        fișierul să fie menținut independent de tradeall."""
        if self._computing:
            if run_full_eval:
                self._start_full_eval_loop()
            return
        import pricewindow as pw
        if cache24_managers is None:
            cache24_managers = get_cache_manager("Price24")
        if current_price_mgr is None:
            current_price_mgr = get_current_price_manager()
        self.current_price_mgr = current_price_mgr
        for s in self.symbols:
            c24 = cache24_managers[s]
            current_price_mgr.subscribe_price(c24)          # CurrentPrice → Cache24
            self.windows[s]   = {}
            self.analyzers[s] = {}
            parts = []
            for sec in self.window_seconds:
                w = pw.PriceWindow.from_cache24(s, sec, c24)
                self.windows[s][sec]   = w
                self.analyzers[s][sec] = pw.WindowAnalyzer(w)
                parts.append(f"{sec:.0f}s: {len(w.prices)} (rate={w.sample_rate_sec:.2f}s)")
            c24.subscribe_price(self)                       # semnal de tick → canal rapid
            print(f"[InstantTrend][{s}] " + " ".join(parts))
        self._computing = True
        self._start_flush_loop()        # I/O decuplat (scrie fișierul în fundal)
        if run_full_eval:
            self._start_full_eval_loop()

    # ── Calcul COMPLET (metrici) — scrie snapshot complet, FĂRĂ logică de trading ──
    def evaluate_full(self, symbol):
        wins = self.windows.get(symbol)
        ans  = self.analyzers.get(symbol)
        if not wins or not ans:
            return None
        primary = self.window_seconds[0]
        if primary not in wins:
            return None
        current_price = None
        if self.current_price_mgr is not None:
            current_price = self.current_price_mgr.get_price_value(symbol)

        # slope pentru fiecare fereastră din listă (N ferestre), keyed pe secunde
        slopes = {}
        primary_pos = None
        for sec in self.window_seconds:
            slope, pos = ans[sec].check_price_change(self._threshold_fn(symbol, sec))
            slopes[sec] = slope
            if sec == primary:
                primary_pos = pos

        # metrici detaliate doar din fereastra PRIMARY (cea mai mică)
        pwin, pan = wins[primary], ans[primary]
        gradient, gc, slope_full, gradient_recent = pwin.get_instant_trend()
        # DOAR memorie (fără I/O); _flush_loop scrie fișierul în fundal.
        self._set_mem(
            symbol,
            final_trend=gradient, growth_coefficient=gc,
            slope_full=slope_full, gradient_recent=gradient_recent,
            slope_small=slopes[self.window_seconds[0]],     # cea mai mică
            slope_big=slopes[self.window_seconds[-1]],      # cea mai mare
            slopes={f"{int(s)}": v for s, v in slopes.items()},
            slope_max_min=pan.calculate_slope_max_min(),
            pos=primary_pos, epsilon=pwin.get_noise_epsilon(self.EPSILON_K),
            current_price=(current_price if current_price is not None else 0.0),
            ts=time.time(),
        )

    def _start_full_eval_loop(self):
        if self._full_eval_thread is not None and self._full_eval_thread.is_alive():
            return
        def run():
            while True:
                for s in list(self.symbols):
                    try:
                        self.evaluate_full(s)
                    except Exception as e:
                        print(f"[CacheInstantTrendManager] evaluate_full {s}: {e}")
                time.sleep(self.FULL_EVAL_INTERVAL_SEC)
        self._full_eval_thread = threading.Thread(target=run, name="InstantTrendFullEval", daemon=True)
        self._full_eval_thread.start()

    # ── Subscriber Cache24 — canal RAPID (gradient + epsilon la fiecare tick) ──
    def on_price_update(self, symbol, ts_ms, price):
        win = self.get_window(symbol)   # fereastra PRIMARY
        if win is None:
            return
        try:
            # Calea RAPIDĂ: doar gradient ieftin + memorie (zero I/O, zero calcul greu).
            g = win.get_recent_gradient()
            eps = win.get_noise_epsilon(self.EPSILON_K)
            self._set_mem(symbol, gradient_recent=g, epsilon=eps,
                          final_trend=(1 if g > 0 else -1 if g < 0 else 0),
                          current_price=price, ts=time.time())
        except Exception as e:
            print(f"[CacheInstantTrendManager] on_price_update {symbol}: {e}")

    # ── API de calcul ─────────────────────────────────────────────────────────
    def get_window(self, symbol, seconds=None):
        """Fereastra pentru `seconds` (None → primary = cea mai mică)."""
        wins = self.windows.get(symbol) or {}
        return wins.get(float(seconds) if seconds is not None else self.window_seconds[0])

    def get_analyzer(self, symbol, seconds=None):
        """Analyzer-ul pentru `seconds` (None → primary = cea mai mică)."""
        ans = self.analyzers.get(symbol) or {}
        return ans.get(float(seconds) if seconds is not None else self.window_seconds[0])

    def get_instant_trend(self, symbol):
        win = self.get_window(symbol)
        return win.get_instant_trend() if win else None

    # ── Store cross-process (fișier JSON, atomic) ─────────────────────────────
    def _write_file(self):
        try:
            atomic_write_json(self.filename, self._mem)
        except Exception as e:
            print(f"[CacheInstantTrendManager] scriere {self.filename}: {e}")

    def _read_file(self):
        # Citim mereu (fișier mic, citit ocazional de readeri) — corectitudine
        # cross-process garantată, fără riscul de mtime cu rezoluție grosieră.
        try:
            with open(self.filename, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _set_mem(self, symbol, **fields):
        """Merge câmpuri DOAR în memorie (fără I/O). Folosit de căile rapide
        (on_price_update, evaluate_full); fișierul e scris de _flush_loop."""
        with self._lock:
            snap = dict(self._mem.get(symbol) or self._read_file().get(symbol) or {})
            snap.update(fields)
            snap["symbol"] = symbol
            self._mem[symbol] = snap

    def update_snapshot(self, symbol, **fields):
        """Memorie + flush IMEDIAT pe fișier DOAR dacă e writer. Apelanții externi
        (tradeall non-writer, teste) actualizează memoria fără a scrie fișierul."""
        self._set_mem(symbol, **fields)
        if self.writer:
            with self._lock:
                self._write_file()

    def _start_flush_loop(self):
        """Thread SEPARAT care scrie memoria pe fișier la FLUSH_INTERVAL_SEC.
        Decuplează I/O-ul de căile de calcul. Rulează DOAR la writer."""
        if not self.writer:
            return
        if self._flush_thread is not None and self._flush_thread.is_alive():
            return
        def run():
            while True:
                time.sleep(self.FLUSH_INTERVAL_SEC)
                with self._lock:
                    if self._mem:
                        self._write_file()
        self._flush_thread = threading.Thread(target=run, name="InstantTrendFlush", daemon=True)
        self._flush_thread.start()

    def prime_from_file(self):
        """Încarcă fișierul în memorie (date INIȚIALE la startup). Un reader poate
        apoi apela start_computation() ca să-și calculeze singur trendul instant
        (on_price_update → _mem) și să țină gradientul proaspăt, fără să scrie fișierul."""
        data = self._read_file()
        with self._lock:
            for symbol, snap in data.items():
                if isinstance(snap, dict):
                    self._mem[symbol] = dict(snap)
        return len(self._mem)

    def get_snapshot(self, symbol):
        """_mem dacă procesul calculează/amorsat; altfel fișierul (reader pur)."""
        with self._lock:
            if symbol in self._mem:
                return dict(self._mem[symbol])
        return self._read_file().get(symbol)

    def is_snapshot_fresh(self, symbol=None, max_age_sec=None):
        """True dacă snapshot-ul (din _mem sau fișier) e mai nou de max_age_sec.
        Permite unui reader să detecteze un writer MORT și să comute pe calcul propriu."""
        max_age_sec = max_age_sec if max_age_sec is not None else self.TREND_STALE_SEC
        now = time.time()
        if symbol is not None:
            snap = self.get_snapshot(symbol)
            return bool(snap) and (now - snap.get("ts", 0)) <= max_age_sec
        allt = self.get_all_snapshots()
        if not allt:
            return False
        latest = max((s.get("ts", 0) for s in allt.values()), default=0)
        return (now - latest) <= max_age_sec

    def become_writer(self):
        """Promovează managerul la WRITER (failover: când fișierul e stale fiindcă
        writer-ul a murit, un reader care deja calculează preia scrierea fișierului)."""
        self.writer = True
        self._start_flush_loop()

    def get_snapshot_resilient(self, symbol, max_age_sec=None,
                               cache24_managers=None, current_price_mgr=None):
        """Reader REZILIENT cu failover LAZY:
          • dacă deja calculez → _mem (autoritar)
          • altfel, dacă fișierul e PROASPĂT → îl folosesc (eficient, FĂRĂ recalcul)
          • dacă fișierul e STALE (writer mort) → pornesc calcul propriu O SINGURĂ
            DATĂ (lazy) + devin writer, apoi folosesc _mem.
        Așa rulez autonom DOAR când nu mă pot baza pe fișier."""
        if self._computing:
            return self.get_snapshot(symbol)
        if self.is_snapshot_fresh(symbol, max_age_sec):
            return self._read_file().get(symbol)
        # Fișier prea vechi → failover: preiau calculul (autonom de aici încolo).
        builtins.print(f"[CacheInstantTrendManager][WARN] fișier stale → "
                       f"failover la calcul propriu ({symbol})")
        self.prime_from_file()
        self.start_computation(cache24_managers, current_price_mgr)
        self.become_writer()
        return self.get_snapshot(symbol)

    def get_all_snapshots(self):
        with self._lock:
            if self._mem:
                return {s: dict(v) for s, v in self._mem.items()}
        return dict(self._read_file())

    def clear(self):
        with self._lock:
            self._mem.clear()
            self._file_mtime = None
            self._file_cache = None
            try:
                if os.path.exists(self.filename):
                    os.remove(self.filename)
            except Exception:
                pass

    # ── API gate (întârziere oportunistă + epsilon informat) ──────────────────
    def _epsilon(self, snap):
        eps = snap.get("epsilon")
        if eps is not None and eps > 0:
            return float(eps)
        price = abs(snap.get("current_price") or 0.0)
        return price * self.FAVORABLE_REL_EPS

    def is_favorable_to_wait(self, side, symbol, mode="full", now=None):
        """Zgomot (|g| <= eps) → True (așteptăm claritate). Trend clar:
        BUY așteaptă cât scade, plasează când urcă clar; SELL invers.

        mode:
          'gradient' (default) → folosește gradient_recent (momentum, rapid)
          'full'               → folosește growth_coefficient (scor complet pe
                                  toată fereastra: avg(slope_full, gradient_recent),
                                  actualizat la FULL_EVAL_INTERVAL_SEC)."""
        snap = self.get_snapshot(symbol)
        if snap is None:
            return False
        now = now if now is not None else time.time()
        if now - snap.get("ts", 0) > self.TREND_STALE_SEC:
            return False
        if mode == "full":
            g = snap.get("growth_coefficient", snap.get("gradient_recent", 0.0))
        else:
            g = snap.get("gradient_recent", 0.0)
        eps = self._epsilon(snap)
        if abs(g) <= eps:
            return True
        side = side.upper()
        if side == "BUY":
            return g < 0
        if side == "SELL":
            return g > 0
        return False

    def wait_for_favorable_entry(self, side, symbol, max_wait_sec=3600.0,
                                 poll_sec=0.2, sleep_fn=time.sleep, mode="full"):
        """Blochează cât timp trendul e favorabil, până la max_wait_sec.
        Heartbeat vizual (.) la ~1s. Returnează secundele așteptate."""
        deadline = time.time() + max_wait_sec
        waited = 0.0
        next_dot = 1.0
        while time.time() < deadline and self.is_favorable_to_wait(side, symbol, mode=mode):
            sleep_fn(poll_sec)
            waited += poll_sec
            if waited >= next_dot:
                print(".", end="", flush=True)
                next_dot += 1.0
        if waited > 0:
            print()
        return waited


_instant_trend_instance = None
_instant_trend_lock = threading.Lock()

def get_instant_trend_manager(symbols=None, filename="cache_instant_trend.json", writer=False):
    """Singleton CacheInstantTrendManager.
    writer=True → procesul scrie fișierul (ex. cacheManager.py). Ceilalți (tradeall
    care calculează pt logica lui, sau readerii) folosesc writer=False."""
    global _instant_trend_instance
    if _instant_trend_instance is not None:
        if writer:
            _instant_trend_instance.writer = True   # promovare la writer (idempotent)
        return _instant_trend_instance
    with _instant_trend_lock:
        if _instant_trend_instance is not None:
            if writer:
                _instant_trend_instance.writer = True
            return _instant_trend_instance
        _syms = symbols if symbols is not None else sym.symbols
        _instant_trend_instance = CacheInstantTrendManager(_syms, filename, writer=writer)
    return _instant_trend_instance


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

        # Singleton pe NUME: prima creare fixează simbolurile. Dacă un apel
        # ulterior cere alte simboluri, ele sunt IGNORATE → avertizăm explicit.
        if name in cls._instances and symbols is not None:
            inst = cls._instances[name]
            existing = set(inst.keys()) if isinstance(inst, dict) else set(getattr(inst, "symbols", []))
            requested = set(symbols)
            if requested != existing:
                missing = requested - existing
                # builtins.print: modulul redefinește print ca no-op (loguri dezactivate),
                # dar avertizarea asta trebuie să fie vizibilă.
                builtins.print(
                    f"[CacheFactory][WARN] '{name}' există deja cu simbolurile {sorted(existing)}; "
                    f"cererea pentru {sorted(requested)} e IGNORATĂ"
                    + (f" (lipsesc: {sorted(missing)})" if missing else "")
                    + ". Singleton pe nume — folosește prima instanță.")

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
                # Price = istoric (append JSONL); Price24 = bounded 24h (full-rewrite)
                prefix, ext = ("cache_price_", "jsonl") if name == "Price" else ("cache_24price_", "json")
                cls._instances[name] = {
                    s: manager_class(
                        sync_ts=sync_ts,
                        filename=f"{prefix}{s}.{ext}",
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
    items = asset_cache.get_remote_items("TOTAL", None)   # update_cache_per_symbol cere new_items
    if items:
        asset_cache.update_cache_per_symbol("TOTAL", items)


def _persist_ws_updated_caches(event_type):
    if event_type == "executionReport":
        get_cache_manager("Order").save_state_to_file_if_enabled()
        get_cache_manager("Trade").save_state_to_file_if_enabled()
    elif event_type in ("balanceUpdate", "outboundAccountPosition"):
        get_cache_manager("AssetValue", symbols=["TOTAL"]).save_state_to_file_if_enabled()


def _refresh_symbol_in_cache(manager, symbol):
    """Reîmprospătează DOAR un simbol într-un manager (eficient pe event WS)."""
    try:
        start_time = manager.fetchtime_time_per_symbol.get(symbol, manager.fallback_time_default)
        items = manager.get_remote_items(symbol, start_time)
        if items:
            manager.update_cache_per_symbol(symbol, items)
    except Exception as e:
        print(f"[cacheManager] _refresh_symbol_in_cache {manager.cls_name}/{symbol}: {e}")


def _handle_binance_ws_event(event):
    print("cacheManager handler call from binance ....")
    event_type = event.get("e")
    if not event_type:
        return

    _ws_event_stats[event_type] += 1

    if event_type == "executionReport":
        symbol = event.get("s")
        if WS_EVENT_LOG_ENABLED:
            print(
                "[cacheManager][WS] executionReport "
                f"symbol={symbol} orderId={event.get('i')} "
                f"status={event.get('X')} execType={event.get('x')} side={event.get('S')}"
            )
        # Derivăm Order + Trade DIRECT din payload-ul WS (ZERO apeluri REST) — evită
        # rate-limit-ul Binance pe rafale de fill-uri și e instant. Re-fetch-ul REST
        # rămâne doar ca fallback când lipsește simbolul (caz rar). Golurile de la
        # eventuale deconectări WS sunt acoperite de polling-ul de fallback (WS unhealthy).
        if symbol:
            _upsert_order_from_execution_report(event)
            _append_trade_from_execution_report(event)
        else:
            for cache_name in ("Order", "Trade"):
                get_cache_manager(cache_name).query_remote_and_update_cache()
        get_cache_manager("Order").save_state_to_file_if_enabled()
        get_cache_manager("Trade").save_state_to_file_if_enabled()
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
        import bapi_ws
        # Clasa de stream trăiește în bapi_ws; cacheManager doar cablează callback-urile
        # de health (care driveează fallback-ul de polling via _should_poll).
        _ws_bridge = bapi_ws.BinanceUserDataStream(
            on_event=_handle_binance_ws_event,
            on_available=_mark_ws_available,
            on_healthy=_mark_ws_event_received,
            on_unhealthy=_mark_ws_unhealthy,
            loss_timeout_sec=WS_LOSS_TIMEOUT_SEC,
        )
        _ws_bridge.start()
        return _ws_bridge
        
def _initialize_once():
    import sys
    if sys.modules.get("_cacheManager_initialized"):
        print("[cacheManager] Already initialized, skip.")
        return
    sys.modules["_cacheManager_initialized"] = True
    enable_real_ws_event_sync()
    print("⚙️ cacheManager: WS user-data bridge pornit (execution reports).")

# WS user-data bridge e OPT-IN: NU mai pornește automat la import.
# Procesele care vor execution reports în timp real (ex. procesul dedicat
# cacheManager.py, sau tradeall) apelează explicit:
#     import cacheManager as cm
#     cm.enable_real_ws_event_sync()   # sau cm._initialize_once()
# Readerii (monitortrades, assetguardian, ...) care doar citesc cache-uri
# nu mai pornesc WS — se bazează pe polling.


if __name__ == "__main__":
    _initialize_once()   # procesul dedicat de cache vrea WS + persistă în fișier
    threads = []

    for name, config in CacheFactory._CONFIG.items():
        cache = get_cache_manager(name)
        interval = config["sync_ts"]()  # obținem intervalul de sincronizare

        if isinstance(cache, dict):
            # Price / Price24 → dict per simbol
            for manager in cache.values():
                threads.append(manager.periodic_sync(interval))
        else:
            threads.append(cache.periodic_sync(interval))

    # Lanț de trend: market-data WS → CurrentPrice → Cache24 → InstantTrend.
    # Rulăm CALCULUL COMPLET aici → cache_instant_trend.json e menținut continuu,
    # independent de tradeall (monitortrades/rtrade citesc de aici).
    try:
        import bapi_ws
        _trend_cpm = get_current_price_manager(
            ws_manager=bapi_ws.get_ws_manager(), sync_ts=0.8)
        _trend_cache24 = CacheFactory.get("Price24")
        _trend_mgr = get_instant_trend_manager(writer=True)   # singurul writer al fișierului
        _trend_mgr.start_computation(_trend_cache24, _trend_cpm, run_full_eval=True)
        print("⚙️ cacheManager: calcul trend complet pornit (cache_instant_trend.json).")
    except Exception as e:
        print(f"[cacheManager] Nu pot porni calculul de trend: {e}")

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("Oprit manual.")
    finally:
        print("Cleanup / închidere resurse...")
