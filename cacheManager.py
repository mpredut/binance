import bisect
import json
import glob
import os
import contextlib
import time
import datetime
import asyncio
import threading
import importlib
import builtins
import weakref
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from typing import Optional

#my imports
import log
import utils as u
import symbols as sym
from binance_api import bapi as api
# Safe market-data facade import: market_api imports binance_api.bapi without importing
# cacheManager, so it does not close a cycle. The trend chain's current price passes
# through this singleton while trading remains on bapi.
import providers.market_api as _market_api

# Load versioned, non-secret tuning parameters using the same pattern as the other
# component env files. ``botcore.load_dotenv`` never overwrites variables already set
# in the real environment; it only fills missing values.
from botcore import load_dotenv as _load_dotenv
_load_dotenv("cachemanager_config.env")

# Dynamic-window bounds for ``get_instant_trend_for_window``. Values below the minimum
# provide too few samples for a meaningful slope; values above the maximum add unjustified
# cost and exceed the 24-hour history retained by Cache24PriceManager.
CM_DYNAMIC_WINDOW_MIN_SEC = float(os.environ.get("CM_DYNAMIC_WINDOW_MIN_SEC", "14.0"))
CM_DYNAMIC_WINDOW_MAX_SEC = float(os.environ.get("CM_DYNAMIC_WINDOW_MAX_SEC", "21600.0"))

#from log import PRINT_CONTEXT


# disable logs by redefine with dummy
#def print(*args, **kwargs):
#   pass
#log.print = lambda *args, **kwargs: None


@contextlib.contextmanager
def atomic_write(path):
    """Yield a unique temporary file handle and atomically replace ``path`` on success.

    On failure, remove the temporary file and re-raise. Cross-process readers therefore
    see either the old file or the complete new file, never a partial JSON/JSONL file.
    """
    # A process-and-thread-specific temporary name prevents concurrent writers from
    # sharing a temporary file. ``os.replace`` remains atomic and last-writer-wins.
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
    """Atomically write JSON through ``atomic_write`` and propagate failures."""
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
    _live_instances = weakref.WeakSet()
    # ── Periodically enforced retention and rotation policy for append caches ──
    RETENTION_DAYS              = 730              # Remove entries older than about two years.
    MAX_FILE_BYTES             = 1_000_000_000    # Rotate above roughly one GB.
    RETENTION_CHECK_INTERVAL_SEC = 7 * 24 * 3600  # Check weekly.
    ROTATE_KEEP_FRACTION       = 0.10             # Keep the latest ten percent after rotation.
    ROTATE_ARCHIVE_COUNT       = 2                # Do not accumulate one-GB archives indefinitely.
    RESYNC_INTERVAL_SEC        = 10 * 60          # Reconcile memory and disk every ten minutes.
    DEDUP_WINDOW               = 100             # Compare each update only with the latest N items.

    def __init__(self, sync_ts, symbols, filename, append_mode = True, api_client=api,
                 append_persist=False):
        self._live_instances.add(self)
        self.cls_name = self.__class__.__name__

        #self.enable_print = True
        #global PRINT_CONTEXT
        #log.PRINT_CONTEXT = self

        self.sync_ts = sync_ts
        self.symbols = symbols
        self.filename = u.cache_path(filename)   # → subfolderul cachedb/
        self.append_mode = append_mode
        self.api_client = api_client
        # JSONL persistence for append-only Trade and AssetValue caches writes only
        # new lines rather than rewriting the entire file.
        self.append_persist = append_persist
        self._persisted_counts = {}   # Number of items already persisted per symbol.

        self.days_back = 30

        self.cache = {}
        self.fetchtime_time_per_symbol = {}

        self.thread = None
        self._stop_event = threading.Event()
        self.save_state = False
        # When true, sleep one interval before the first synchronization iteration.
        # Preserve values subclasses set before ``super().__init__`` because the base
        # initializer starts the thread.
        if not hasattr(self, "_first_sleep"):
            self._first_sleep = False
        self.lock = threading.RLock()

        # Shared subscriber pattern forwards prices to other managers and PriceWindow.
        # Initialize before periodic_sync because its thread may notify immediately.
        if not hasattr(self, "_price_subscribers"):
            self._price_subscribers = []

        self.fallback_time_default = int(time.time() * 1000) - self.days_back*24*60*60*1000

        # function calls here after all inint vars
        self.load_state()

    # ── Subscriber pattern for forwarding prices ─────────────────────────────

    def subscribe_price(self, subscriber) -> None:
        """Subscribe an object implementing ``on_price_update(symbol, ts_ms, price)``."""
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
        """Allow subclasses to provide custom fetch-time reconstruction.

        Returning None lets ``__rebuild_fetchtime_times`` infer timestamps generically
        from ``time``/``timestamp`` dictionaries or ``[timestamp, ...]`` lists.
        """
        return None
    
    
    def __rebuild_fetchtime_times(self):
        last_times_per_sym = self.rebuild_fetchtime_times()
        if not last_times_per_sym:
            last_times_per_sym = defaultdict(int)
            for symbol, trades in self.cache.items():
                for trade in trades:
                    # Use ``time`` or ``timestamp`` and fall back to zero.
                    #time_ = trade.get("time") or trade.get("timestamp") or 0
                    if isinstance(trade, dict):
                        time_ = trade.get("time") or trade.get("timestamp") or 0
                    elif isinstance(trade, list) and len(trade) > 0:
                        time_ = trade[0]  # ``[timestamp_ms, price]`` format.
                    else:
                        time_ = 0
                    if time_ > last_times_per_sym[symbol]:
                        last_times_per_sym[symbol] = time_
            # Apply a 60-second safety offset.
            for symbol in last_times_per_sym:
                last_times_per_sym[symbol] = max(0, last_times_per_sym[symbol] - 60_000)                
        if not last_times_per_sym:
            # Fall back to the file modification time.
            fallback_time_file = 0
            if os.path.exists(self.filename): # TODO: distinguish an existing file with no data.
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
                            # Convert the legacy list format to a dictionary.
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


    # ── Memory/disk resilience: freshness, overwrite guard, and resync ────────
    def _mem_max_ts(self):
        """Return memory freshness as the latest fetch time in milliseconds."""
        if not self.fetchtime_time_per_symbol:
            return 0
        try:
            return max(self.fetchtime_time_per_symbol.values())
        except Exception:
            return 0

    def _persisted_max_ts(self):
        """Read file freshness cheaply from the ``.meta`` sidecar."""
        try:
            with open(self.filename + ".meta") as mf:
                return json.load(mf).get("max_ts", 0)
        except Exception:
            return 0

    def _write_meta(self):
        """Atomically write a small freshness, fetch-time, and count sidecar."""
        try:
            atomic_write_json(self.filename + ".meta",
                              {"max_ts": self._mem_max_ts(),
                               "saved_at": int(time.time() * 1000),
                               "fetchtime": self.fetchtime_time_per_symbol,
                               "counts": self._persisted_counts})
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] meta {self.filename}: {e}")

    def save_state_to_file_if_enabled(self):
        """Write only when state saving is enabled; readers perform no work."""
        if self.save_state:
            self.save_state_to_file()

    def save_state_to_file(self):
        """Write to disk regardless of ``save_state`` for writers and failover.

        Refuse to overwrite newer data another process has already persisted.
        """
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
        """Reload the cache when another process has written a newer file."""
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
        """Periodically reload newer disk state or persist newer memory state."""
        file_ts = self._persisted_max_ts()
        mem_ts = self._mem_max_ts()
        if file_ts > mem_ts:
            builtins.print(f"[{self.cls_name}][resync] fișier mai nou → reîncarc ({self.filename})")
            self._reload_from_disk()
        elif mem_ts > file_ts and self.save_state:
            self.save_state_to_file_if_enabled()

    # ── JSONL append persistence for append-only caches ──────────────────────
    def _save_jsonl_append(self):
        """Append only items added since the last flush without rewriting the file."""
        try:
            with self.lock:
                with open(self.filename, "a") as f:
                    for symbol, items in self.cache.items():
                        start = self._persisted_counts.get(symbol, 0)
                        if start > len(items):   # The cache was cleared or shortened; resynchronize.
                            start = 0
                        for item in items[start:]:
                            f.write(json.dumps({"s": symbol, "i": item}) + "\n")
                        self._persisted_counts[symbol] = len(items)
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] append JSONL {self.filename}: {e}")

    def _load_jsonl(self):
        """Load every JSONL line into the cache and fetch times from the sidecar."""
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
                            continue   # Skip a partial or corrupt line left by an append crash.
            self._persisted_counts = {s: len(v) for s, v in self.cache.items()}
            metaf = self.filename + ".meta"
            if os.path.exists(metaf):
                try:
                    with open(metaf) as mf:
                        self.fetchtime_time_per_symbol = json.load(mf).get("fetchtime", {})
                except Exception:
                    pass

    def compact_jsonl(self):
        """Rewrite JSONL from memory after full in-memory and on-disk deduplication.

        Perform this expensive complete pass periodically rather than on every update.
        """
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
                    self.cache[symbol] = deduped   # Keep memory deduplicated too.
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
        """Extract an entry timestamp in milliseconds from a dictionary or list."""
        if isinstance(item, dict):
            return item.get("time") or item.get("timestamp") or 0
        if isinstance(item, (list, tuple)) and item:
            return item[0]
        return 0

    def maintain_append_persist(self):
        """Perform weekly maintenance for every append-mode cache.

        First prune entries older than ``RETENTION_DAYS`` using the generic timestamp
        extractor. Then rotate oversized JSONL files through ``_rotate_keep_latest``.
        Full-JSON trade/order/asset caches grow slowly enough that time retention, not
        the one-GB size threshold, is their effective bound. Applying this to every
        append-mode cache prevents unbounded growth outside JSONL classes.
        """
        if not self.append_mode:
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
            if self.append_persist:
                self.compact_jsonl()
            else:
                self.save_state_to_file()   # A full rewrite is normal for these classes.
        # Rotate by size only for JSONL; see the docstring.
        if not self.append_persist:
            return
        try:
            if os.path.exists(self.filename) and os.path.getsize(self.filename) > self.MAX_FILE_BYTES:
                self._rotate_keep_latest()
        except OSError:
            pass

    def _rotate_keep_latest(self):
        """Archive the current file and retain each symbol's latest configured fraction."""
        with self.lock:
            archive = f"{self.filename}.{int(time.time())}.archive"
            try:
                os.replace(self.filename, archive)   # Move complete history into the archive.
            except OSError as e:
                builtins.print(f"[{self.cls_name}][maintain] arhivare eșuată: {e}")
                return
            for symbol, items in self.cache.items():
                keep_n = max(1, int(len(items) * self.ROTATE_KEEP_FRACTION))
                self.cache[symbol] = items[-keep_n:]
            self._persisted_counts = {}
            self.compact_jsonl()   # Rewrite the current file with retained entries only.
            prefix = f"{self.filename}."
            archives = sorted(
                (path for path in glob.glob(prefix + "*.archive")),
                key=lambda path: os.path.getmtime(path), reverse=True)
            for old_archive in archives[self.ROTATE_ARCHIVE_COUNT:]:
                try:
                    os.remove(old_archive)
                except OSError:
                    pass
        builtins.print(f"[{self.cls_name}][maintain] ROTAȚIE: arhivat → {archive}, "
                       f"păstrat ultimele {int(self.ROTATE_KEEP_FRACTION*100)}%")

    @abstractmethod
    def get_remote_items(self, symbol, startTime):
        """Fetch remote items; subclasses must implement this method."""
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

        with self.lock:  # Protected write.
            if self.append_mode:    # History mode retains every PriceOrders/Price/Trade element.
                #if isinstance(new_items, dict):
                #    new_items = [new_items]
                #elif not isinstance(new_items, list):
                #    new_items = [new_items]                    
                # Polling returns overlapping recent data, so deduplicate against only the
                # recent window. This is O(DEDUP_WINDOW) rather than O(entire cache); the
                # periodic compact_jsonl pass performs complete deduplication.
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
        """Persist one symbol to the cache by default.

        Subclasses may override this, for example to record a timestamp and notify
        subscribers through ``CacheCurrentPriceManager._push_price``.
        """
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
        """Decide whether the synchronization loop polls the API.

        Cache24PriceManager is push-only, while CacheCurrentPriceManager polls only when
        WebSocket is unavailable. The default uses the global ``WS_ONLY_MODE`` gate.
        """
        return _should_poll_for_manager(self.cls_name)

    def periodic_sync(self, sync_ts=None, save_state=True):
        if sync_ts is not None:
            self.sync_ts = sync_ts
        self.save_state = save_state  # Always update the state-saving preference.

        if self.thread is not None and self.thread.is_alive():
            return self.thread  # Return the existing thread; it reads sync_ts dynamically.

        self._stop_event.clear()

        def run():
            if self._first_sleep and self._stop_event.wait(self.sync_ts):
                return   # Run the first iteration after one interval, as CurrentPrice requires.
            last_maint = time.time()
            last_resync = time.time()
            while not self._stop_event.is_set():
                try:
                    if self._should_poll():
                        self.query_remote_and_update_cache()
                    self.save_state_to_file_if_enabled()   # Guards against overwriting newer data.
                    # Periodically reconcile memory and disk.
                    if (time.time() - last_resync) > self.RESYNC_INTERVAL_SEC:
                        self.resync_mem_file()
                        last_resync = time.time()
                    # Run weekly retention/rotation for every append-mode cache, including
                    # full-JSON managers that previously received no effective retention.
                    if self.append_mode and (time.time() - last_maint) > self.RETENTION_CHECK_INTERVAL_SEC:
                        self.maintain_append_persist()
                        last_maint = time.time()
                except Exception as _e:   # Transient network/HTTP errors must not kill synchronization.
                    builtins.print(f"[{self.cls_name}] eroare in bucla de sync (continui): {_e}")
                if self._stop_event.wait(self.sync_ts):
                    break

        self.thread = threading.Thread(target=run, name=self.cls_name, daemon=True)
        self.thread.daemon = True  # Do not let this thread block process shutdown.
        self.thread.start()
        return self.thread

    def shutdown(self, timeout=5.0):
        """Deterministically stop the synchronization loop started by ``periodic_sync``."""
        self._stop_event.set()
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            self.thread = None
        return stopped

    @classmethod
    def shutdown_all_instances(cls, timeout=5.0):
        results = [manager.shutdown(timeout=timeout)
                   for manager in list(cls._live_instances)]
        return all(results) if results else True
    
    def enable_save_state_to_file(self):
        self.save_state = True



# ###### 
# ###### Cache-specific implementations
# ###### 

class CacheTradeManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols, filename, api_client=api):
        # Slow growth from real trades makes full rewrites acceptable.
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)

    def _is_valid_trade(self, trade):
        required_keys = ['symbol', 'id', 'orderId', 'price', 'qty', 'time', 'isBuyer']
        return all(k in trade for k in required_keys)

    def get_remote_items(self, symbol, startTime):
        import importlib
        apitrades = importlib.import_module("binance_api.bapi_trades")
        
        current_time = int(time.time() * 1000)
        backdays = int((current_time - startTime) / (24 * 60 * 60 * 1000))
        
        # Paginate through the injected client so periods with more than 1,000 trades
        # are not truncated.
        from binance_api import bapi_allorders as apiorders
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

    def last_opposite_fill_price(self, symbol, order_type):
        """Return the latest opposite fill price without a time limit.

        BUY uses the latest SELL and SELL uses the latest BUY. Read the manager's own
        real WebSocket fill cache without an API call or noise from canceled orders.
        """
        want_buyer = (order_type.upper() == "SELL")   # The opposite of SELL is BUY.
        with self.lock:
            for tr in reversed(self.cache.get(symbol, [])):   # Append order makes the last item newest.
                if tr.get("isBuyer") == want_buyer:
                    return float(tr["price"])
        return None


class CacheOrderManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols, filename, api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)
        
    def _is_valid_trade(self, trade):
       required_keys = ['orderId', 'price', 'quantity', 'timestamp', 'side']
       return all(k in trade for k in required_keys)

    def get_remote_items(self, symbol, startTime):
        #import bapi_trades as apitrades
        from binance_api import bapi_allorders as apiorders
        
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


class CacheSparsePriceManager(CacheManagerInterface):
    """Store sparse seven-minute price samples for two years in JSONL.

    Unlike dense WebSocket-pushed Cache24 managers, this periodically asks for the
    current price. ``Sparse`` describes the stable sampling mechanism rather than the
    mutable retention policy. priceAnalysis is its only consumer.
    """

    def __init__(self, sync_ts, symbols, filename, api_client=api):
        # Continuous append-only seven-minute history uses JSONL append persistence.
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
        # Preserve the observation timestamp supplied by get_price. Using only a cached
        # value with ``time.time`` would record a stale price as freshly observed during
        # a network outage, corrupting the long-term history.
        try:
            entry = get_current_price_manager().get_price(symbol)
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] get_price {symbol}: {e}")
            return []

        if not entry:
            return []
        ts_ms, price = entry
        if price is None:
            return []

        return [[int(ts_ms), price]]

    def get_all_symbols_from_cache(self):
        return self.symbols


class Cache24PriceManager(CacheManagerInterface):
    """Collect maximum-granularity prices over the latest ``KEEP_HOURS``.

    This manager neither polls nor subscribes directly to WebSocket. It receives every
    update from CacheCurrentPriceManager and uses ``get_remote_items`` only during
    initialization when the persisted file is missing.
    """
    KEEP_HOURS = 24   # May be configured per instance.

    def __init__(self, sync_ts, symbols, filename, api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)
        # Remove unrelated symbols loaded from legacy redundant files that stored every coin.
        with self.lock:
            self.cache = {s: v for s, v in self.cache.items() if s in self.symbols}

    # Inherit price subscription and notification methods from CacheManagerInterface.

    # ── CacheCurrentPriceManager callback ────────────────────────────────────

    def on_price_update(self, symbol: str, ts_ms: int, price: float):
        """Receive every new WebSocket or HTTP price from CacheCurrentPriceManager."""
        # CurrentPrice broadcasts every symbol to every subscriber, while this manager is
        # per-symbol. Store and forward only this instance's symbol.
        if symbol not in self.symbols:
            return
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self._CacheManagerInterface__rebuild_fetchtime_times()
        self.update_cache_per_symbol(symbol, [[ts_ms, price]])
        self._trim_old_data(symbol)
        self._notify_price_subscribers(symbol, ts_ms, price)

    def _trim_old_data(self, symbol):
        """Remove entries older than ``KEEP_HOURS`` efficiently on every tick.

        Entries are append-only and time-ordered, so bisect finds the first unexpired
        index in O(log n) without copying when nothing expired. This avoids an O(n)
        list comprehension on every tick as long archives grow.
        """
        cutoff_ms = int((time.time() - self.KEEP_HOURS * 3600) * 1000)
        with self.lock:
            entries = self.cache.get(symbol)
            if not entries:
                return
            idx = bisect.bisect_left(entries, cutoff_ms, key=lambda e: e[0])
            if idx > 0:
                self.cache[symbol] = entries[idx:]

    def get_recent_entries(self, symbol: str, last_seconds: float) -> list:
        """Return ``[timestamp_ms, price]`` entries from the latest interval.

        Time-ordered append-only entries let bisect find the cutoff in O(log n); copying
        costs O(k) for returned entries instead of scanning the entire 24-hour buffer.
        This matters for frequent dynamic-window queries.
        """
        cutoff_ms = int((time.time() - last_seconds) * 1000)
        with self.lock:
            entries = self.cache.get(symbol, [])
            if not entries:
                return []
            idx = bisect.bisect_left(entries, cutoff_ms, key=lambda e: e[0])
            return entries[idx:]

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
        """Fetch initialization data only when the persisted file is missing.

        Preserve the real observation timestamp from ``get_price`` so a cached value
        during an outage is not recorded as falsely fresh.
        """
        try:
            entry = get_current_price_manager().get_price(symbol)
            if not entry or entry[1] is None:
                return []
            return [[int(entry[0]), entry[1]]]
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] get_price {symbol}: {e}")
            return []

    def get_all_symbols_from_cache(self):
        return self.symbols

    def _should_poll(self):
        # Prices arrive exclusively through CurrentPrice's ``on_price_update`` callback.
        # Base periodic_sync only saves, resynchronizes, and maintains; it does not poll.
        return False


class Cache24LongPriceManager(Cache24PriceManager):
    """Six-month archive variant using incremental JSONL persistence.

    This remains separate from the live 24-hour manager and inherits its price-update
    behavior while overriding persistence only. JSONL appends new ticks at constant cost
    instead of rewriting a growing archive every minute.

    The base JSONL writer tracks how many entries it wrote and assumes the list grows only
    at the tail. Trimming from the head would otherwise make retained entries appear new
    and duplicate them, so this class compacts whenever trimming actually removes data.
    """

    LONG_TRIM_INTERVAL_SEC = 24 * 3600

    def __init__(self, sync_ts, symbols, filename, api_client=api):
        # Long archives must not rewrite the entire JSONL whenever a single oldest
        # tick expires.  Initialize the maintenance clock before callbacks can run.
        self._last_long_trim = 0.0
        # Call CacheManagerInterface directly because Cache24PriceManager does not expose
        # append_persist and invokes load_state before this class could change it.
        CacheManagerInterface.__init__(self, sync_ts, symbols, filename,
                                        append_mode=True, api_client=api_client,
                                        append_persist=True)
        # Mirror Cache24PriceManager's cleanup of unrelated symbols from legacy files.
        with self.lock:
            self.cache = {s: v for s, v in self.cache.items() if s in self.symbols}

    def _trim_old_data(self, symbol):
        now = time.monotonic()
        if now - self._last_long_trim < self.LONG_TRIM_INTERVAL_SEC:
            return
        self._last_long_trim = now
        with self.lock:
            before = len(self.cache.get(symbol, []))
        super()._trim_old_data(symbol)   # Use the inherited trimming logic unchanged.
        with self.lock:
            after = len(self.cache.get(symbol, []))
        if after < before:
            removed = before - after
            self._persisted_counts[symbol] = max(0, self._persisted_counts.get(symbol, before) - removed)
            self.compact_jsonl()

    # Inherit get_remote_items from Cache24PriceManager, including real observation timestamps.


class CachePriceLongTrendManager(CacheManagerInterface):
    def __init__(self, sync_ts, symbols, filename, api_client=api):
        super().__init__(sync_ts, symbols, filename, append_mode=False)

    # Inherit get_all_symbols_from_cache from the base class.

    # def rebuild_fetchtime_times(self):
        # """
        # Infer each symbol's latest record time from self.cache.
        # """
        # last_times = defaultdict(int)
        # for price_trend in self.cache:
            # symbol = price_trend.get("symbol")
            # ts = price_trend.get("timestamp", 0) * 1000
            # if ts > last_times[symbol]:
                # last_times[symbol] = ts

        # Apply a negative 60-second safety offset.
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
        # Slow growth at one sample per ten minutes makes full rewrites acceptable.
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)
        changed = False
        with self.lock:
            for items in self.cache.values():
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    if "total_value_usdc" not in item and "total_value_usdt" in item:
                        item["total_value_usdc"] = item.pop("total_value_usdt")
                        changed = True
        if changed:
            self.save_state_to_file()

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
            total_usdc = self.api_client.get_total_assets_value_usdc(use_cache=False)
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] Nu pot interoga valoarea totala: {e}")
            return []

        if not isinstance(total_usdc, (int, float)) or total_usdc <= 0:
            print(f"[{self.cls_name}][Eroare] Valoarea totala invalidă: {total_usdc}")
            return []
            
        now_sec = int(time.time())
        snapshot = {
            "timestamp": now_sec,
            "datetime_local": datetime.now().isoformat(timespec="seconds"),
            "total_value_usdc": round(float(total_usdc), 8),
        }
        return [snapshot]


# ######
# ###### CacheCurrentPriceManager: per-symbol current price, WebSocket-first with HTTP fallback
# ######

class CacheCurrentPriceManager(CacheManagerInterface):
    """Maintain the latest timestamped price per symbol.

    This is a drop-in replacement for ``bapi.get_current_price``. WebSocket is primary;
    HTTP polling runs only after WebSocket has been silent beyond ``WS_TIMEOUT_SEC``.
    ``get_price`` forces HTTP when the cached entry exceeds ``STALE_THRESHOLD_MS``.
    Snapshot semantics retain one ``[[timestamp_ms, price]]`` entry per symbol in a
    shared cache_currentprice.json file.
    """

    WS_TIMEOUT_SEC     = 15      # Treat WebSocket as unavailable after 15 silent seconds.
    STALE_THRESHOLD_MS = 5_000  # Force HTTP when a price is older than five seconds.
    FREQ_WINDOW_SEC    = 60     # Window used to measure update frequency.

    def __init__(self, sync_ts, symbols, filename, ws_manager=None, api_client=api,
                 market_api=None):
        self._ws_manager        = ws_manager
        self._ws_last_event_ts  = 0.0      # Set before the base initializer.
        self._price_subscribers = []       # The base initializer preserves existing values.
        self._update_timestamps: dict = defaultdict(deque)  # Also required before super().
        self._first_sleep       = True     # Let WebSocket connect before HTTP fallback.
        # The injectable market-data facade defaults to the global singleton. Set it before
        # the base initializer because load_state may immediately call get_remote_items.
        self.market_api = market_api or _market_api.api
        super().__init__(sync_ts, symbols, filename, append_mode=False, api_client=api_client)
        if ws_manager is not None:
            ws_manager.subscribe(self)

    # ── WS health ────────────────────────────────────────────────────────────

    def _ws_is_healthy(self):
        return (time.time() - self._ws_last_event_ts) < self.WS_TIMEOUT_SEC

    # Inherit price subscription and notification methods from CacheManagerInterface.

    # ── WebSocket callback overriding the interface method ───────────────────

    def _record_price_timestamp(self, symbol: str) -> None:
        now = time.time()
        dq = self._update_timestamps[symbol]
        dq.append(now)
        cutoff = now - self.FREQ_WINDOW_SEC
        while dq and dq[0] < cutoff:
            dq.popleft()

    def _push_price(self, symbol: str, price: float) -> None:
        """Insert a price and notify subscribers without changing WebSocket health.

        The polling thread and get_price also use this path; only a real WebSocket event
        in ``on_items_update`` updates ``_ws_last_event_ts``.
        """
        ts_ms = int(time.time() * 1000)
        if not self.fetchtime_time_per_symbol:
            self.fetchtime_time_per_symbol = self._CacheManagerInterface__rebuild_fetchtime_times()
        self._record_price_timestamp(symbol)
        self.update_cache_per_symbol(symbol, [[ts_ms, price]])
        self._notify_price_subscribers(symbol, ts_ms, price)

    def on_items_update(self, symbol: str, items):
        """Handle a real WebSocket event and update WebSocket health."""
        self._ws_last_event_ts = time.time()   # Real WebSocket events only.
        price = items[0] if items else None
        if price is None:
            return
        self._push_price(symbol, price)

    # ── CacheManagerInterface abstract methods ───────────────────────────────

    def get_remote_items(self, symbol, startTime):
        """Fetch current price through the market-data facade."""
        try:
            price = self.market_api.get_current_price(symbol=symbol)
            if price is None:
                return []
            ts_ms = int(time.time() * 1000)
            return [[ts_ms, price]]
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] HTTP fetch {symbol}: {e}")
            return []

    def _persist_items(self, symbol, new_items):
        """Record sample-rate timing and notify subscribers through ``_push_price``."""
        self._push_price(symbol, new_items[0][1])

    def rebuild_fetchtime_times(self):
        last_times = {}
        for symbol in self.symbols:
            entries = self.cache.get(symbol, [])
            if entries:
                last_times[symbol] = entries[0][0]   # Snapshot contains one entry.
        return last_times

    def get_all_symbols_from_cache(self):
        return self.symbols

    # ── Periodic synchronization: poll only when WebSocket is unavailable ────

    def _should_poll(self):
        # Fetch through HTTP only as a fallback. The overridden persistence path
        # propagates results through ``_push_price``.
        return not self._ws_is_healthy()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_sample_rate(self, symbol: str, fallback: float = 1.0) -> float:
        """Return the mean update interval, or ``fallback`` when samples are insufficient."""
        dq = self._update_timestamps.get(symbol)
        if not dq or len(dq) < 2:
            return fallback
        return (dq[-1] - dq[0]) / (len(dq) - 1)

    def get_update_frequency(self, symbol: str) -> float:
        """Return updates per second over the latest frequency window."""
        dq = self._update_timestamps.get(symbol)
        if not dq or len(dq) < 2:
            return 0.0
        return len(dq) / self.FREQ_WINDOW_SEC

    def get_price(self, symbol: str):
        """Return ``[timestamp_ms, price]``, forcing HTTP when missing or stale."""
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
        """Return only the float price as a drop-in for ``bapi.get_current_price``."""
        entry = self.get_price(symbol)
        return entry[1] if entry else None

    def attach_ws_manager(self, ws_manager) -> None:
        """Attach BinancePriceStream as the idempotent primary price source."""
        if ws_manager is None:
            return
        self._ws_manager = ws_manager
        ws_manager.subscribe(self)


# ── Singleton ─────────────────────────────────────────────────────────────────

_current_price_instance: Optional[CacheCurrentPriceManager] = None
_current_price_lock = threading.Lock()

def get_current_price_manager(ws_manager=None, symbols=None, sync_ts=None,
                              start_sync=True) -> CacheCurrentPriceManager:
    """Return or lazily create the CacheCurrentPriceManager singleton.

    ``sync_ts=None`` preserves the current interval and uses the configured default on
    first creation. An explicit value updates the live interval read by the thread.
    Internal calls must pass None so they do not override main-process configuration.
    """
    global _current_price_instance
    if _current_price_instance is not None:
        if sync_ts is not None:
            _current_price_instance.sync_ts = sync_ts   # Live update.
        if ws_manager is not None:
            _current_price_instance.attach_ws_manager(ws_manager)
        if start_sync:
            _current_price_instance.periodic_sync(save_state=False)
        return _current_price_instance
    with _current_price_lock:
        if _current_price_instance is not None:
            if sync_ts is not None:
                _current_price_instance.sync_ts = sync_ts
            if ws_manager is not None:
                _current_price_instance.attach_ws_manager(ws_manager)
            if start_sync:
                _current_price_instance.periodic_sync(save_state=False)
            return _current_price_instance
        _syms = symbols if symbols is not None else sym.symbols
        _current_price_instance = CacheCurrentPriceManager(
            sync_ts     = sync_ts if sync_ts is not None else CURRENTPRICE_SYNC_INTERVAL_SEC,
            symbols     = _syms,
            filename    = "cache_currentprice.json",
            ws_manager  = ws_manager,
            api_client  = api,
        )
        if start_sync:
            _current_price_instance.periodic_sync(save_state=False)
    return _current_price_instance


# ######
# ###### CachePriceShortTrendManager: windows, calculated trend, and cross-process cache
# ######

class CachePriceShortTrendManager:
    """Calculate per-symbol instant trends and share snapshots across processes.

    Writers build windows, subscribe to Cache24, and publish fast gradient/epsilon values.
    Readers such as rtrade use only the gate backed by the shared file.
    """
    EPSILON_K         = 1.0     # Informed noise threshold: k * stddev(gradient).
    FAVORABLE_REL_EPS = 1e-5    # Price-relative fallback when epsilon is absent.
    TREND_STALE_SEC   = 15.0    # Do not delay based on older snapshots.
    # Percentage thresholds are scale-invariant and configurable per window or symbol.
    PRICE_CHANGE_THRESHOLD_SMALL = u.calculate_difference_percent(60000, 60000 - 310)
    PRICE_CHANGE_THRESHOLD_BIG   = u.calculate_difference_percent(97000, 95000 - 377)
    FULL_EVAL_INTERVAL_SEC = 3.0   # Cadence for expensive complete metrics.
    FLUSH_INTERVAL_SEC     = 0.5   # Writer-only file flush cadence.
    # Every window is a slice of the same 24-hour Cache24 buffer. The smallest is primary.
    WINDOW_SECONDS = [3.7 * 60, 2.5 * 60 * 60]   # [3.7 min momentum, 2.5 ore trend]
    _live_instances = weakref.WeakSet()

    def __init__(self, symbols, filename="cache_instant_trend.json", writer=False,
                 window_seconds=None, thresholds=None):
        self._live_instances.add(self)
        self.symbols = list(symbols)
        self.filename = u.cache_path(filename)   # → subfolderul cachedb/
        self.writer = writer   # Only the writer process persists the file.
        # Sort N window durations so index zero is primary.
        secs = list(window_seconds) if window_seconds else list(self.WINDOW_SECONDS)
        self.window_seconds = sorted(float(s) for s in secs)
        # Thresholds may be callable, keyed by window, keyed by symbol and window, or None.
        self._threshold_fn = self._build_threshold_fn(thresholds)
        self._mem = {}
        self._lock = threading.RLock()
        self._file_mtime = None
        self._file_cache = None
        # Writer state populated by start_computation: symbol/window to analyzer objects.
        self.windows = {}
        self.analyzers = {}
        self.current_price_mgr = None
        # start_computation supplies raw Cache24 buffers for dynamic windows. Until then,
        # dynamic windows are unavailable and safely return unknown.
        self._cache24_managers = None
        self._computing = False
        self._full_eval_thread = None
        self._flush_thread = None
        self._stop_event = threading.Event()

    # Extreme window durations derived from the sorted list.
    @property
    def window_small_sec(self):
        return self.window_seconds[0]

    @property
    def window_big_sec(self):
        return self.window_seconds[-1]

    def _build_threshold_fn(self, thresholds):
        """Normalize ``thresholds`` to a ``(symbol, seconds) -> percentage`` function."""
        if callable(thresholds):
            return thresholds
        small, big = self.window_seconds[0], self.window_seconds[-1]

        def per_window_default(sec):
            # The smallest window uses SMALL; all others use BIG.
            return self.PRICE_CHANGE_THRESHOLD_SMALL if float(sec) <= small else self.PRICE_CHANGE_THRESHOLD_BIG

        if isinstance(thresholds, dict):
            # Accept either per-symbol/per-window or direct per-window dictionaries.
            per_symbol = all(isinstance(v, dict) for v in thresholds.values()) and len(thresholds) > 0
            if per_symbol:
                tbl = {sym: {float(k): v for k, v in d.items()} for sym, d in thresholds.items()}
                return lambda sym, sec: tbl.get(sym, {}).get(float(sec), per_window_default(sec))
            tbl = {float(k): v for k, v in thresholds.items()}
            return lambda sym, sec: tbl.get(float(sec), per_window_default(sec))

        return lambda sym, sec: per_window_default(sec)

    def threshold_for(self, symbol, seconds):
        """Return the percentage threshold for one symbol window."""
        return self._threshold_fn(symbol, float(seconds))

    # ── Writer: build windows and subscribe to Cache24 ───────────────────────
    def start_computation(self, cache24_managers=None, current_price_mgr=None, run_full_eval=False):
        """Build windows and the fast channel, optionally starting full evaluation."""
        if self._computing:
            if run_full_eval:
                self._start_full_eval_loop()
            return
        self._stop_event.clear()
        import pricewindow as pw
        if cache24_managers is None:
            cache24_managers = get_cache_manager("Price24")
        if current_price_mgr is None:
            current_price_mgr = get_current_price_manager()
        self.current_price_mgr = current_price_mgr
        self._cache24_managers = cache24_managers   # Raw sources for dynamic windows.
        for s in self.symbols:
          try:
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
            c24.subscribe_price(self)                       # tick signal to the fast channel
            print(f"[InstantTrend][{s}] " + " ".join(parts))
          except Exception as _e:
            builtins.print(f"[InstantTrend][{s}] setup esuat ({_e}) — sar peste, Binance neafectat")
        self._computing = True
        self._start_flush_loop()        # Decouple file I/O into a background thread.
        if run_full_eval:
            self._start_full_eval_loop()

    # ── Full metric calculation without trading logic ────────────────────────
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

        # Calculate slopes for every window, keyed by seconds.
        slopes = {}
        primary_pos = None
        for sec in self.window_seconds:
            slope, pos = ans[sec].check_price_change(self._threshold_fn(symbol, sec))
            slopes[sec] = slope
            if sec == primary:
                primary_pos = pos

        # Detailed metrics come only from the smallest primary window.
        pwin, pan = wins[primary], ans[primary]
        gradient, gc, slope_full, gradient_recent = pwin.get_instant_trend()
        # Update memory only; the flush loop writes in the background.
        self._set_mem(
            symbol,
            final_trend=gradient, growth_coefficient=gc,
            slope_full=slope_full, gradient_recent=gradient_recent,
            slope_small=slopes[self.window_seconds[0]],     # Smallest.
            slope_big=slopes[self.window_seconds[-1]],      # Largest.
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
            while not self._stop_event.is_set():
                for s in list(self.symbols):
                    if self._stop_event.is_set():
                        break
                    try:
                        self.evaluate_full(s)
                    except Exception as e:
                        print(f"[CachePriceShortTrendManager] evaluate_full {s}: {e}")
                self._stop_event.wait(self.FULL_EVAL_INTERVAL_SEC)
        self._full_eval_thread = threading.Thread(target=run, name="InstantTrendFullEval", daemon=True)
        self._full_eval_thread.start()

    # ── Fast Cache24 subscriber channel: gradient and epsilon on every tick ───
    def on_price_update(self, symbol, ts_ms, price):
        win = self.get_window(symbol)   # Primary window.
        if win is None:
            return
        try:
            # The fast path computes only a cheap gradient in memory. It writes separate
            # ``_fast`` keys for low-latency gates and never overwrites richer full-evaluation
            # results. Sharing keys previously created a network-timing race between paths.
            g = win.get_recent_gradient()
            eps = win.get_noise_epsilon(self.EPSILON_K)
            self._set_mem(symbol, gradient_recent_fast=g, epsilon=eps,
                          trend_fast=(1 if g > 0 else -1 if g < 0 else 0),
                          current_price=price, ts=time.time())
        except Exception as e:
            print(f"[CachePriceShortTrendManager] on_price_update {symbol}: {e}")

    # ── Calculation API ──────────────────────────────────────────────────────
    def get_window(self, symbol, seconds=None):
        """Return the requested window, or the smallest primary window for None."""
        wins = self.windows.get(symbol) or {}
        return wins.get(float(seconds) if seconds is not None else self.window_seconds[0])

    def get_analyzer(self, symbol, seconds=None):
        """Return the requested analyzer, or the smallest primary analyzer for None."""
        ans = self.analyzers.get(symbol) or {}
        return ans.get(float(seconds) if seconds is not None else self.window_seconds[0])

    def get_instant_trend(self, symbol):
        win = self.get_window(symbol)
        return win.get_instant_trend() if win else None

    # ── Dynamic window ────────────────────────────────────────────────────────
    # Calculate only the requested horizon from Cache24's existing raw buffer. Bisect
    # makes cost proportional to samples in that window rather than the full 24 hours.
    def get_instant_trend_for_window(self, symbol, window_seconds, now=None):
        """Calculate trend on demand for an arbitrary window in seconds.

        Use the shared formula directly on a Cache24 slice without constructing a
        PriceWindow. Return metrics or None when Cache24 is unavailable, the symbol is
        not tracked, or fewer than three samples exist. Clamp requests to configured
        bounds and report the adjusted horizon without raising.
        """
        if self._cache24_managers is None:
            return None
        c24 = self._cache24_managers.get(symbol)
        if c24 is None:
            return None

        req = float(window_seconds)
        clamped = min(max(req, CM_DYNAMIC_WINDOW_MIN_SEC), CM_DYNAMIC_WINDOW_MAX_SEC)
        if clamped != req:
            print(f"[get_instant_trend_for_window] {symbol}: window_seconds={req:.0f} "
                  f"in afara [{CM_DYNAMIC_WINDOW_MIN_SEC:.0f}, {CM_DYNAMIC_WINDOW_MAX_SEC:.0f}] "
                  f"-> clamat la {clamped:.0f}")

        try:
            entries = c24.get_recent_entries(symbol, last_seconds=clamped)
        except Exception as e:
            print(f"[get_instant_trend_for_window] {symbol}: get_recent_entries a esuat ({e})")
            return None
        if len(entries) < 3:
            return None   # Too few samples means unknown, not a noisy assumption.

        now = now if now is not None else time.time()
        newest_ts_sec = entries[-1][0] / 1000.0
        if now - newest_ts_sec > self.TREND_STALE_SEC:
            return None   # Treat an old latest tick as stale, matching fresh_snapshot.

        import numpy as np
        import pricewindow as pw
        prices = [e[1] for e in entries]
        sample_rate = pw.PriceWindow._sample_rate_from_entries(entries)
        final_trend, growth_coefficient, slope_full, gradient_recent = pw.instant_trend_from_prices(
            prices, sample_rate)
        epsilon = float(self.EPSILON_K * np.std(np.gradient(np.array(prices))))

        return dict(final_trend=final_trend, growth_coefficient=growth_coefficient,
                    slope_full=slope_full, gradient_recent=gradient_recent,
                    epsilon=epsilon, n_samples=len(entries), window_seconds=clamped,
                    sample_rate_sec=sample_rate, ts=now)

    def is_trend_up_for_window(self, symbol, window_seconds, now=None):
        """Evaluate monitortrades' upward-trend condition over a configurable window.

        Return False as the same neutral fallback when the dynamic window is unavailable.
        """
        dyn = self.get_instant_trend_for_window(symbol, window_seconds, now=now)
        if dyn is None:
            return False
        slope = dyn["gradient_recent"]
        gradient = dyn["final_trend"]
        return slope > 0 or (slope == 0 and gradient > 0)

    # ── Atomic cross-process JSON store ──────────────────────────────────────
    def _write_file(self):
        try:
            atomic_write_json(self.filename, self._mem)
        except Exception as e:
            print(f"[CachePriceShortTrendManager] scriere {self.filename}: {e}")

    def _read_file(self):
        # Always read this small, infrequently accessed file to guarantee cross-process
        # correctness without relying on coarse mtime resolution.
        try:
            with open(self.filename, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _set_mem(self, symbol, **fields):
        """Merge fields in memory only; the flush loop owns file I/O."""
        with self._lock:
            snap = dict(self._mem.get(symbol) or self._read_file().get(symbol) or {})
            snap.update(fields)
            snap["symbol"] = symbol
            self._mem[symbol] = snap

    def update_snapshot(self, symbol, **fields):
        """Update memory and flush immediately only when this instance is the writer."""
        self._set_mem(symbol, **fields)
        if self.writer:
            with self._lock:
                self._write_file()

    def _start_flush_loop(self):
        """Run a writer-only thread that decouples periodic file I/O from calculation."""
        if not self.writer:
            return
        if self._flush_thread is not None and self._flush_thread.is_alive():
            return
        def run():
            while not self._stop_event.wait(self.FLUSH_INTERVAL_SEC):
                with self._lock:
                    if self._mem:
                        self._write_file()
        self._flush_thread = threading.Thread(target=run, name="InstantTrendFlush", daemon=True)
        self._flush_thread.start()

    def shutdown(self, timeout=5.0):
        """Stop evaluation and flush loops, then unsubscribe Cache24 sources."""
        self._stop_event.set()
        for cache24 in (self._cache24_managers or {}).values():
            unsubscribe = getattr(cache24, "unsubscribe_price", None)
            if unsubscribe is not None:
                unsubscribe(self)
        for thread in (self._full_eval_thread, self._flush_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=timeout)
        stopped = all(thread is None or not thread.is_alive()
                      for thread in (self._full_eval_thread, self._flush_thread))
        if stopped:
            self._full_eval_thread = None
            self._flush_thread = None
            self._computing = False
        return stopped

    @classmethod
    def shutdown_all_instances(cls, timeout=5.0):
        results = [manager.shutdown(timeout=timeout)
                   for manager in list(cls._live_instances)]
        return all(results) if results else True

    def prime_from_file(self):
        """Prime memory from startup data so a reader can calculate fresh trends locally."""
        data = self._read_file()
        with self._lock:
            for symbol, snap in data.items():
                if isinstance(snap, dict):
                    self._mem[symbol] = dict(snap)
        return len(self._mem)

    def get_snapshot(self, symbol):
        """Return local calculated/primed memory, or the file for a pure reader."""
        with self._lock:
            if symbol in self._mem:
                return dict(self._mem[symbol])
        return self._read_file().get(symbol)

    def is_snapshot_fresh(self, symbol=None, max_age_sec=None):
        """Return whether memory or file state is fresh enough to trust the writer."""
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
        """Promote a calculating reader to writer when the previous writer is stale."""
        self.writer = True
        self._start_flush_loop()

    def get_snapshot_resilient(self, symbol, max_age_sec=None,
                               cache24_managers=None, current_price_mgr=None):
        """Read resiliently with lazy failover.

        Use authoritative memory while calculating, otherwise use a fresh shared file.
        If the file is stale, start local calculation once, become writer, and use memory.
        """
        if self._computing:
            return self.get_snapshot(symbol)
        if self.is_snapshot_fresh(symbol, max_age_sec):
            return self._read_file().get(symbol)
        # A stale file triggers autonomous local calculation and writer failover.
        builtins.print(f"[CachePriceShortTrendManager][WARN] fișier stale → "
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

    # ── Opportunistic-delay gate with informed epsilon ───────────────────────
    def _epsilon(self, snap):
        eps = snap.get("epsilon")
        if eps is not None and eps > 0:
            return float(eps)
        price = abs(snap.get("current_price") or 0.0)
        return price * self.FAVORABLE_REL_EPS

    def fresh_snapshot(self, symbol, now=None):
        """Return a symbol snapshot only when it is within ``TREND_STALE_SEC``.

        This is the shared public staleness check used by both trend and wait decisions.
        """
        snap = self.get_snapshot(symbol)
        if snap is None:
            return None
        now = now if now is not None else time.time()
        if now - snap.get("ts", 0) > self.TREND_STALE_SEC:
            return None
        return snap

    def should_wait(self, side, symbol, window_seconds=None, fast=False,
                     use_noise_gate=True, now=None):
        """Decide whether to delay a BUY or SELL intent; True means wait.

        Unknown or stale data returns False and executes immediately, so a failed cache
        cannot block orders. ``window_seconds=None`` uses the precomputed primary window;
        another value calculates a bounded dynamic window where Cache24 is available.
        ``fast`` selects low-latency keys only for the primary window. The optional noise
        gate treats ``|gradient| <= epsilon`` as a reason to wait.
        """
        side = side.upper()

        if window_seconds is not None and float(window_seconds) != self.window_seconds[0]:
            dyn = self.get_instant_trend_for_window(symbol, window_seconds, now=now)
            if dyn is None:
                return False   # Unknown because Cache24 is unavailable, insufficient, or stale.
            g = float(dyn["growth_coefficient"] or 0.0)
            if use_noise_gate and abs(g) <= dyn["epsilon"]:
                return True
            if side == "BUY":
                return g < 0
            if side == "SELL":
                return g > 0
            return False

        snap = self.fresh_snapshot(symbol, now=now)
        if snap is None:
            return False   # Unknown or stale data executes immediately.
        if fast:
            g = snap.get("gradient_recent_fast", snap.get("gradient_recent", 0.0))
        else:
            g = snap.get("growth_coefficient", snap.get("gradient_recent", 0.0))
        g = float(g or 0.0)
        if use_noise_gate:
            eps = self._epsilon(snap)
            if abs(g) <= eps:
                return True
        if side == "BUY":
            return g < 0
        if side == "SELL":
            return g > 0
        return False

    def wait_for_favorable_entry(self, side, symbol, max_wait_sec=10.0,
                                 poll_sec=0.2, sleep_fn=time.sleep, mode="full",
                                 window_seconds=None):
        """Wait while trend favors delay, bounded by ``max_wait_sec``.

        Emit a visual heartbeat roughly once per second and return elapsed seconds.
        Forward ``window_seconds`` to ``should_wait`` for primary or dynamic evaluation.
        """
        deadline = time.time() + max_wait_sec
        waited = 0.0
        next_dot = 1.0
        while time.time() < deadline and self.should_wait(
                side, symbol, window_seconds=window_seconds, fast=(mode == "gradient")):
            sleep_fn(poll_sec)
            waited += poll_sec
            if waited >= next_dot:
                print(".", end="", flush=True)
                next_dot += 1.0
        if waited > 0:
            print()
        return waited


_short_trend_instance = None
_short_trend_lock = threading.Lock()

def get_short_trend_manager(symbols=None, filename="cache_instant_trend.json", writer=False):
    """Return the CachePriceShortTrendManager singleton.

    ``writer=True`` lets the process persist the file; calculators and readers use False.
    """
    global _short_trend_instance
    if _short_trend_instance is not None:
        if writer:
            _short_trend_instance.writer = True   # Idempotent writer promotion.
        return _short_trend_instance
    with _short_trend_lock:
        if _short_trend_instance is not None:
            if writer:
                _short_trend_instance.writer = True
            return _short_trend_instance
        _syms = symbols if symbols is not None else sym.symbols
        _short_trend_instance = CachePriceShortTrendManager(_syms, filename, writer=writer)
    return _short_trend_instance


# ######
# ###### GLOBAL VARIABLE FOR CACHE #######
# ######
     
ORDER_SYNC_INTERVAL_SEC = 0.4 * 60   # 3 minute     
TRADE_SYNC_INTERVAL_SEC = 3 * 60   # 3 minute
PRICE_SYNC_INTERVAL_SEC = 7 * 60   # 7 minute
PRICE24_SYNC_INTERVAL_SEC = 30         # Fallback polling while WebSocket is inactive.
CURRENTPRICE_SYNC_INTERVAL_SEC = 30   # Same policy for CacheCurrentPriceManager.
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
            "class": CacheSparsePriceManager,
            "filename": None,  # Dictionary per symbol.
            "sync_ts": lambda: PRICE_SYNC_INTERVAL_SEC,
        },
        "Price24": {
            "class": Cache24PriceManager,
            "filename": None,  # Dictionary per symbol.
            "sync_ts": lambda: PRICE24_SYNC_INTERVAL_SEC,
        },
        "CurrentPrice": {
            "class": CacheCurrentPriceManager,
            "filename": "cache_currentprice.json",  # One shared file for all symbols.
            "sync_ts": lambda: CURRENTPRICE_SYNC_INTERVAL_SEC,
        },
        "PriceLongTrend": {
            "class": CachePriceLongTrendManager,
            "filename": "cache_price_long_trend.json",
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

        # This name-keyed singleton fixes symbols on first creation. Warn explicitly
        # when a later call requests a different set that will be ignored.
        if name in cls._instances and symbols is not None:
            inst = cls._instances[name]
            existing = set(inst.keys()) if isinstance(inst, dict) else set(getattr(inst, "symbols", []))
            requested = set(symbols)
            if requested != existing:
                missing = requested - existing
                # Use builtins.print so this warning remains visible if module logging
                # replaces print with a no-op.
                builtins.print(
                    f"[CacheFactory][WARN] '{name}' există deja cu simbolurile {sorted(existing)}; "
                    f"cererea pentru {sorted(requested)} e IGNORATĂ"
                    + (f" (lipsesc: {sorted(missing)})" if missing else "")
                    + ". Singleton pe nume — folosește prima instanță.")

        created = name not in cls._instances
        if created:
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
                # Price is append-JSONL history; Price24 is a bounded full-rewrite cache.
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

            managers = (cls._instances[name].values()
                        if isinstance(cls._instances[name], dict)
                        else (cls._instances[name],))
            for manager in managers:
                manager.periodic_sync(sync_ts, False)

        return cls._instances[name]

    @classmethod
    def shutdown_all(cls, timeout=5.0):
        """Stop every factory-owned synchronization loop and clear the registry."""
        managers = []
        for value in cls._instances.values():
            managers.extend(value.values() if isinstance(value, dict) else (value,))
        results = [manager.shutdown(timeout=timeout) for manager in managers
                   if hasattr(manager, "shutdown")]
        cls._instances = {}
        return all(results) if results else True

    @classmethod
    def remove(cls, name, timeout=5.0):
        """Remove a singleton cleanly after stopping all threads it owns."""
        value = cls._instances.pop(name, None)
        if value is None:
            return True
        managers = value.values() if isinstance(value, dict) else (value,)
        results = [manager.shutdown(timeout=timeout) for manager in managers
                   if hasattr(manager, "shutdown")]
        return all(results) if results else True
        
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

    # Cache only executed orders, matching get_filled_orders. NEW or terminal orders
    # without fills are not transactions and would pollute the profit guard with zero prices.
    if event.get("X") not in ("FILLED", "PARTIALLY_FILLED"):
        return

    order_item = {
        "orderId": event.get("i"),
        # L is the latest execution price and p is the order-price fallback. Convert L
        # before fallback because the non-fill string ``0.00000000`` is truthy.
        "price": float(event.get("L") or 0) or float(event.get("p") or 0),
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
    items = asset_cache.get_remote_items("TOTAL", None)   # update_cache_per_symbol expects new_items.
    if items:
        asset_cache.update_cache_per_symbol("TOTAL", items)


def _persist_ws_updated_caches(event_type):
    if event_type == "executionReport":
        get_cache_manager("Order").save_state_to_file_if_enabled()
        get_cache_manager("Trade").save_state_to_file_if_enabled()
    elif event_type in ("balanceUpdate", "outboundAccountPosition"):
        get_cache_manager("AssetValue", symbols=["TOTAL"]).save_state_to_file_if_enabled()


def _refresh_symbol_in_cache(manager, symbol):
    """Refresh only one manager symbol efficiently for a WebSocket event."""
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
        # Derive Order and Trade directly from the WebSocket payload without REST calls.
        # REST remains a rare fallback for missing symbols, while fallback polling covers
        # gaps created by WebSocket disconnections.
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
        # Already started by an earlier import.
        if _ws_bridge is not None:
            return _ws_bridge
    with _ws_bridge_lock:
        if _ws_bridge is not None:
            return _ws_bridge
        from binance_api import bapi_ws
        # bapi_ws owns the stream class; cacheManager wires health callbacks that drive
        # fallback polling through ``_should_poll``.
        _ws_bridge = bapi_ws.BinanceAccountStream(
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

# The user-data WebSocket bridge is opt-in and does not start during import. Processes
# that need real-time execution reports enable it explicitly:
#     import cacheManager as cm
#     cm.enable_real_ws_event_sync()   # or cm._initialize_once()
# Read-only consumers rely on polling and do not start WebSocket.


import concurrent.futures as _futures

# A hard deadline protects non-Binance price polling from DNS/connect/read hangs that
# request read timeouts may not cover. A separate worker and Future timeout keep the
# poller alive and let it recover when the network returns.
_NB_FETCH_DEADLINE_SEC = 15   # Shorter than the 20-second interval, so hangs do not extend a cycle.


def _fetch_price_with_deadline(market_api, symbol, pool, deadline_sec=_NB_FETCH_DEADLINE_SEC):
    """Fetch current price with a hard deadline across DNS, connect, and read stages."""
    fut = pool.submit(market_api.get_current_price, symbol=symbol)
    return fut.result(timeout=deadline_sec)


class _NonBinanceTrendPoller:
    """Own the non-Binance poller's thread and executor lifecycle."""
    def __init__(self, thread, pool, stop_event):
        self.thread = thread
        self.pool = pool
        self.stop_event = stop_event

    def stop(self, timeout=5.0):
        self.stop_event.set()
        if self.thread is not threading.current_thread():
            self.thread.join(timeout=timeout)
        self.pool.shutdown(wait=False, cancel_futures=True)
        return not self.thread.is_alive()


def _start_nonbinance_trend_poller(cpm, symbols, interval_sec=20,
                                   fetch_deadline_sec=_NB_FETCH_DEADLINE_SEC):
    """Feed instant trends for non-WebSocket, non-Binance symbols.

    Poll through the facade and push into the CurrentPrice/Cache24/InstantTrend chain.
    Per-symbol failures do not affect Binance. A hard deadline and at least two workers
    keep one blocked fetch from freezing the poller; repeated failures remain visible.
    """
    pool = _futures.ThreadPoolExecutor(
        max_workers=max(2, len(list(symbols))), thread_name_prefix="NBTrendFetch")
    stop_event = threading.Event()

    def run():
        while not stop_event.is_set():
            for s in list(symbols):
                if stop_event.is_set():
                    break
                try:
                    p = _fetch_price_with_deadline(cpm.market_api, s, pool, fetch_deadline_sec)
                    if p is not None and float(p) > 0:
                        cpm._push_price(s, float(p))
                except _futures.TimeoutError:
                    builtins.print(f"[NB-trend] {s}: fetch BLOCAT >{fetch_deadline_sec}s "
                                   f"(deadline dur — probabil DNS/retea) — sar peste, reincerc ciclul urmator")
                except Exception as _e:
                    builtins.print(f"[NB-trend] {s}: {_e}")
            stop_event.wait(interval_sec)
    t = threading.Thread(target=run, name="NonBinanceTrendPoller", daemon=True)
    t.start()
    return _NonBinanceTrendPoller(t, pool, stop_event)


if __name__ == "__main__":
    _initialize_once()   # The dedicated cache process enables WebSocket and persistence.
    threads = []
    _nb_poller = None
    _trend_mgr = None

    # Add non-Binance instruments that need cached instant trends. Binance symbols still
    # come from sym.symbols through WebSocket; configuration errors fall back to Binance only.
    _nb_syms = []
    try:
        from instruments_config import load_for
        for _inst in load_for("mt").values():
            if _inst.provider_name != "binance" and _inst.symbol not in sym.symbols:
                _nb_syms.append(_inst.symbol)
        _nb_syms = list(dict.fromkeys(_nb_syms))
    except Exception as _e:
        builtins.print(f"[cacheManager] instrumente non-Binance pt trend indisponibile: {_e}")
        _nb_syms = []
    _trend_syms = list(dict.fromkeys(list(sym.symbols) + _nb_syms))
    # Register non-Binance Price24 managers before the generic loop so trend computation
    # can find a raw buffer for every symbol.
    if _nb_syms:
        CacheFactory.get("Price24", symbols=_trend_syms)
        # Extend the persisted CurrentPrice factory instance with non-Binance symbols
        # polled through the facade, keeping cache_currentprice.json coherent.
        CacheFactory.get("CurrentPrice", symbols=_trend_syms)
        builtins.print(f"[cacheManager] instant-trend extins cu non-Binance: {_nb_syms}")
        # LONGTREND_NONBINANCE optionally starts accumulating sparse history and long-trend
        # data. It is disabled by default until enough lookback data exists.
        if os.environ.get("LONGTREND_NONBINANCE", "").strip().lower() == "true":
            CacheFactory.get("Price", symbols=_trend_syms)
            CacheFactory.get("PriceLongTrend", symbols=_trend_syms)
            builtins.print(f"[cacheManager] trend LUNG non-Binance ACTIVAT: {_nb_syms}")

    for name, config in CacheFactory._CONFIG.items():
        cache = get_cache_manager(name)
        interval = config["sync_ts"]()  # Resolve the synchronization interval.

        if isinstance(cache, dict):
            # Price and Price24 are dictionaries keyed by symbol.
            for manager in cache.values():
                threads.append(manager.periodic_sync(interval))
        else:
            threads.append(cache.periodic_sync(interval))

    # Trend chain: market-data WebSocket to CurrentPrice, Cache24, and InstantTrend.
    # Full calculation here keeps the shared snapshot current independently of tradeall.
    try:
        from binance_api import bapi_ws
        _trend_cpm = get_current_price_manager(
            ws_manager=bapi_ws.get_ws_manager(), symbols=_trend_syms, sync_ts=0.8)
        _trend_cache24 = CacheFactory.get("Price24")
        _trend_mgr = get_short_trend_manager(symbols=_trend_syms, writer=True)   # Sole file writer.
        _trend_mgr.start_computation(_trend_cache24, _trend_cpm, run_full_eval=True)
        print("⚙️ cacheManager: calcul trend complet pornit (cache_instant_trend.json).")
        if _nb_syms:   # WebSocket covers Binance only; push other prices manually.
            _nb_poller = _start_nonbinance_trend_poller(_trend_cpm, _nb_syms, interval_sec=20)
    except Exception as e:
        print(f"[cacheManager] Nu pot porni calculul de trend: {e}")

    try:
        for t in threads:
            t.join()
    except KeyboardInterrupt:
        print("Oprit manual.")
    finally:
        print("Cleanup / închidere resurse...")
        if _nb_poller is not None:
            _nb_poller.stop()
        if _trend_mgr is not None:
            _trend_mgr.shutdown()
        CacheFactory.shutdown_all()
        if _current_price_instance is not None:
            _current_price_instance.shutdown()
        if _ws_bridge is not None:
            _ws_bridge.stop()
        from binance_api import bapi_client
        bapi_client.stop_periodic_resync()
