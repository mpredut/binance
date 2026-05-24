# pricefetcher.py
import json
import time
import threading
import requests
import os
from datetime import datetime
from typing import Dict, Optional, List, Set, Tuple
from abc import ABC, abstractmethod
from collections import defaultdict

# Importă modulele tale existente
import log
import utils as u
import symbols as sym  # Importă symbols.py existent
import bapi as api

# Importă clasele de bază din cacheManager
from cacheManager import CacheManagerInterface, CacheFactory, _should_poll_for_manager


# ============================================
# Configurare globală
# ============================================

PRICE_HISTORY_RETENTION_DAYS = 7  # Păstrează prețuri doar pentru ultimele 7 zile
MAX_PRICE_HISTORY_PER_SYMBOL = 2000  # Maxim 2000 de intrări per simbol (siguranță)

# Folosește USDC pentru piața europeană (nu USDT)
QUOTE_CURRENCY = "USDC"
FALLBACK_QUOTE = "USDT"  # fallback dacă USDC nu e disponibil

# Simboluri default de monitorizat (se vor adăuga la cele din sym.symbols)
# WATCHLIST
DEFAULT_SYMBOLS = ["BTC", "ETH", "HYPE", "SOL", "BNB", "ADA", "DOGE", "XRP"]


# ============================================
# Platforme de preț (Strategy Pattern)
# ============================================

class PricePlatformInterface(ABC):
    """Interfață pentru toate platformele de preț"""
    
    @abstractmethod
    def get_price(self, symbol: str) -> Optional[float]:
        """Returnează prețul curent sau None dacă e eroare"""
        pass
    
    @abstractmethod
    def supports_symbol(self, symbol: str) -> bool:
        """Verifică dacă platforma suportă simbolul"""
        pass
    
    @abstractmethod
    def get_available_symbols(self) -> Set[str]:
        """Returnează toate simbolurile disponibile pe platformă"""
        pass
    
    @property
    @abstractmethod
    def platform_name(self) -> str:
        pass
    
    def refresh_symbols(self):
        """Reîmprospătează lista de simboluri (opțional)"""
        pass


class BinancePricePlatform(PricePlatformInterface):
    """Platformă pentru prețuri Binance (prioritate USDC, fallback USDT)"""
    
    def __init__(self, api_client=None):
        self.api_client = api_client or api
        self._supported_symbols: Set[str] = set()
        self._usdc_pairs: Set[str] = set()   # Perechi USDC
        self._usdt_pairs: Set[str] = set()   # Perechi USDT (fallback)
        self._symbol_mapping: Dict[str, str] = {}  # mapare "BTC" -> "BTCUSDC" sau "BTCUSDT"
        self._last_refresh = 0
        self._refresh_interval = 3600  # 1 oră
        self._load_symbols()
    
    @property
    def platform_name(self) -> str:
        return "Binance"
    
    def _load_symbols(self):
        """Încarcă toate perechile disponibile pe Binance"""
        try:
            response = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=10)
            response.raise_for_status()
            data = response.json()
            
            self._usdc_pairs.clear()
            self._usdt_pairs.clear()
            self._supported_symbols.clear()
            self._symbol_mapping.clear()
            
            for symbol_info in data.get("symbols", []):
                symbol = symbol_info.get("symbol")
                base_asset = symbol_info.get("baseAsset")
                quote_asset = symbol_info.get("quoteAsset")
                status = symbol_info.get("status")
                
                if status != "TRADING":
                    continue
                
                self._supported_symbols.add(symbol)
                
                # Înregistrează perechile USDC și USDT separat
                if quote_asset == "USDC":
                    self._usdc_pairs.add(symbol)
                    # Prioritizează USDC pentru mapping
                    if base_asset not in self._symbol_mapping:
                        self._symbol_mapping[base_asset] = symbol
                    self._symbol_mapping[symbol] = symbol
                
                elif quote_asset == "USDT":
                    self._usdt_pairs.add(symbol)
                    # USDT e fallback (doar dacă nu există deja USDC)
                    if base_asset not in self._symbol_mapping:
                        self._symbol_mapping[base_asset] = symbol
                    self._symbol_mapping[symbol] = symbol
            
            print(f"[BinancePlatform] USDC: {len(self._usdc_pairs)} perechi, USDT: {len(self._usdt_pairs)} perechi")
            self._last_refresh = time.time()
            
        except Exception as e:
            print(f"[BinancePlatform] Eroare la încărcare: {e}")
            self._fallback_symbols()
    
    def _fallback_symbols(self):
        """Fallback la simboluri comune în caz de eroare"""
        self._usdc_pairs = {"BTCUSDC", "ETHUSDC", "BNBUSDC"}
        self._usdt_pairs = {"BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT", "XRPUSDT"}
        self._supported_symbols = self._usdc_pairs | self._usdt_pairs
        self._symbol_mapping = {
            "BTC": "BTCUSDC", "ETH": "ETHUSDC", "BNB": "BNBUSDC",
            "SOL": "SOLUSDT", "ADA": "ADAUSDT", "DOGE": "DOGEUSDT", "XRP": "XRPUSDT"
        }
    
    def refresh_symbols(self):
        if time.time() - self._last_refresh > self._refresh_interval:
            self._load_symbols()
    
    def get_available_symbols(self) -> Set[str]:
        self.refresh_symbols()
        return self._supported_symbols.copy()
    
    def supports_symbol(self, symbol: str) -> bool:
        self.refresh_symbols()
        
        # Verificare directă
        if symbol in self._supported_symbols:
            return True
        
        # Verificare în mapping
        if symbol in self._symbol_mapping:
            return True
        
        # Încearcă să construiască perechea
        for quote in ["USDC", "USDT"]:
            pair = f"{symbol}{quote}"
            if pair in self._supported_symbols:
                return True
        
        return False
    
    def get_price(self, symbol: str) -> Optional[float]:
        try:
            # Găsește perechea corectă
            trading_pair = None
            
            # 1. Verifică mapping-ul direct
            if symbol in self._symbol_mapping:
                trading_pair = self._symbol_mapping[symbol]
            
            # 2. Încearcă mai întâi USDC (pentru Europa)
            elif f"{symbol}USDC" in self._supported_symbols:
                trading_pair = f"{symbol}USDC"
            
            # 3. Fallback la USDT
            elif f"{symbol}USDT" in self._supported_symbols:
                trading_pair = f"{symbol}USDT"
            
            # 4. Verifică dacă simbolul e deja o pereche validă
            elif symbol in self._supported_symbols:
                trading_pair = symbol
            
            if not trading_pair:
                print(f"[BinancePlatform] Nu am găsit pereche pentru {symbol}")
                return None
            
            # Folosește API-ul client existent
            price = self.api_client.get_current_price(symbol=trading_pair)
            print(f"[BinancePlatform] {symbol} -> {trading_pair} = ${price}")
            return float(price)
            
        except Exception as e:
            print(f"[BinancePlatform] Eroare {symbol}: {e}")
            return None


class HyperliquidPricePlatform(PricePlatformInterface):
    """Platformă pentru prețuri Hyperliquid"""
    
    def __init__(self):
        self._url = "https://api.hyperliquid.xyz/info"
        self._supported_symbols: Set[str] = set()
        self._all_mids: Dict[str, float] = {}
        self._last_refresh = 0
        self._refresh_interval = 300  # 5 minute
        self._load_symbols()
    
    @property
    def platform_name(self) -> str:
        return "Hyperliquid"
    
    def _load_symbols(self):
        """Încarcă toate simbolurile disponibile pe Hyperliquid"""
        try:
            payload = {"type": "allMids"}
            headers = {"Content-Type": "application/json"}
            
            response = requests.post(self._url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            
            self._all_mids = response.json()
            self._supported_symbols = set(self._all_mids.keys())
            
            print(f"[HyperliquidPlatform] Încărcate {len(self._supported_symbols)} simboluri")
            self._last_refresh = time.time()
            
        except Exception as e:
            print(f"[HyperliquidPlatform] Eroare la încărcare: {e}")
            # Fallback la simboluri cunoscute
            self._supported_symbols = {"HYPE", "PURR", "BTC", "ETH", "SOL", "USDC"}
    
    def refresh_symbols(self):
        if time.time() - self._last_refresh > self._refresh_interval:
            self._load_symbols()
    
    def get_available_symbols(self) -> Set[str]:
        self.refresh_symbols()
        return self._supported_symbols.copy()
    
    def supports_symbol(self, symbol: str) -> bool:
        self.refresh_symbols()
        
        # Hyperliquid folosește simboluri simple (BTC, nu BTCUSDC)
        return symbol in self._supported_symbols
    
    def get_price(self, symbol: str) -> Optional[float]:
        try:
            self.refresh_symbols()
            
            if symbol not in self._all_mids:
                print(f"[HyperliquidPlatform] Simbol {symbol} negăsit")
                return None
            
            return float(self._all_mids[symbol])
            
        except Exception as e:
            print(f"[HyperliquidPlatform] Eroare {symbol}: {e}")
            return None


class CoinMarketCapPricePlatform(PricePlatformInterface):
    """Platformă pentru prețuri CoinMarketCap (necesită API Key)"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get('CMC_API_KEY')
        self._base_url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest"
        self._supported_symbols: Set[str] = set()
        self._all_listings: Dict[str, Dict] = {}
        self._last_refresh = 0
        self._refresh_interval = 3600  # 1 oră (respectă rate limits)
        
        if self.api_key:
            self._load_symbols()
        else:
            print("[CMCPlatform] Fără API Key - platformă dezactivată")
    
    @property
    def platform_name(self) -> str:
        return "CoinMarketCap"
    
    def _load_symbols(self):
        """Încarcă top 5000 criptomonede disponibile pe CMC"""
        if not self.api_key:
            return
        
        try:
            headers = {
                'X-CMC_PRO_API_KEY': self.api_key,
                'Accept': 'application/json'
            }
            params = {
                'limit': 5000,
                'convert': 'USD'
            }
            
            response = requests.get(
                "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
                headers=headers,
                params=params,
                timeout=15
            )
            response.raise_for_status()
            data = response.json()
            
            self._supported_symbols.clear()
            for crypto in data.get('data', []):
                symbol = crypto.get('symbol')
                if symbol:
                    self._supported_symbols.add(symbol)
                    self._all_listings[symbol] = {
                        'id': crypto.get('id'),
                        'name': crypto.get('name'),
                        'slug': crypto.get('slug')
                    }
            
            print(f"[CMCPlatform] Încărcate {len(self._supported_symbols)} simboluri")
            self._last_refresh = time.time()
            
        except Exception as e:
            print(f"[CMCPlatform] Eroare la încărcare: {e}")
    
    def refresh_symbols(self):
        if self.api_key and time.time() - self._last_refresh > self._refresh_interval:
            self._load_symbols()
    
    def get_available_symbols(self) -> Set[str]:
        self.refresh_symbols()
        return self._supported_symbols.copy()
    
    def supports_symbol(self, symbol: str) -> bool:
        if not self.api_key:
            return False
        self.refresh_symbols()
        return symbol in self._supported_symbols
    
    def get_price(self, symbol: str) -> Optional[float]:
        if not self.api_key:
            return None
        
        try:
            self.refresh_symbols()
            
            headers = {
                'X-CMC_PRO_API_KEY': self.api_key,
                'Accept': 'application/json'
            }
            params = {
                'symbol': symbol,
                'convert': 'USD'
            }
            
            response = requests.get(self._base_url, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if 'data' not in data or symbol not in data['data']:
                return None
            
            price = data['data'][symbol]['quote']['USD']['price']
            return float(price)
            
        except Exception as e:
            print(f"[CMCPlatform] Eroare {symbol}: {e}")
            return None


# ============================================
# Price Platform Factory cu descoperire automată
# ============================================

class PricePlatformFactory:
    """Factory care alege automat platforma potrivită pentru fiecare simbol"""
    
    def __init__(self, cmc_api_key: Optional[str] = None):
        self._platforms: List[PricePlatformInterface] = [
            BinancePricePlatform(),
            HyperliquidPricePlatform(),
        ]
        
        # Adaugă CMC doar dacă avem cheie
        if cmc_api_key:
            self._platforms.append(CoinMarketCapPricePlatform(cmc_api_key))
        
        # Cache pentru simboluri suportate
        self._symbol_platform_cache: Dict[str, str] = {}
        
        # Rulează descoperirea completă la startup
        self._discover_all_symbols()
    
    def _discover_all_symbols(self):
        """Descoperă toate simbolurile disponibile pe toate platformele"""
        print("[PriceFactory] 🔍 Descoperire simboluri disponibile...")
        
        all_symbols = {}
        for platform in self._platforms:
            try:
                symbols = platform.get_available_symbols()
                all_symbols[platform.platform_name] = {
                    "count": len(symbols),
                    "sample": list(symbols)[:10]  # primele 10 ca exemplu
                }
                print(f"[PriceFactory]   {platform.platform_name}: {len(symbols)} simboluri")
            except Exception as e:
                print(f"[PriceFactory]   {platform.platform_name}: eroare - {e}")
        
        self._capabilities = all_symbols
    
    def get_price(self, symbol: str) -> Dict:
        """
        Obține prețul folosind prima platformă care suportă simbolul
        
        Returns:
            Dict cu symbol, price, platform, timestamp
        """
        # Verifică cache-ul de simboluri
        if symbol in self._symbol_platform_cache:
            platform_name = self._symbol_platform_cache[symbol]
            for platform in self._platforms:
                if platform.platform_name == platform_name:
                    price = platform.get_price(symbol)
                    if price is not None:
                        return {
                            "symbol": symbol,
                            "price": price,
                            "platform": platform.platform_name,
                            "timestamp": int(time.time())
                        }
        
        # Caută platforma potrivită
        for platform in self._platforms:
            if platform.supports_symbol(symbol):
                price = platform.get_price(symbol)
                if price is not None:
                    # Salvează în cache pentru data viitoare
                    self._symbol_platform_cache[symbol] = platform.platform_name
                    return {
                        "symbol": symbol,
                        "price": price,
                        "platform": platform.platform_name,
                        "timestamp": int(time.time())
                    }
        
        raise Exception(f"Symbol '{symbol}' nu e suportat de nici o platformă")
    
    def get_price_multi(self, symbols: List[str]) -> List[Dict]:
        """Obține prețuri pentru mai multe simboluri"""
        results = []
        for symbol in symbols:
            try:
                results.append(self.get_price(symbol))
            except Exception as e:
                print(f"[PriceFactory] Eroare la {symbol}: {e}")
        return results
    
    def get_supported_symbols(self) -> Set[str]:
        """Returnează toate simbolurile suportate (uniunea platformelor)"""
        all_symbols = set()
        for platform in self._platforms:
            all_symbols.update(platform.get_available_symbols())
        return all_symbols
    
    def get_capabilities(self) -> Dict:
        """Returnează capacitățile fiecărei platforme"""
        return self._capabilities if hasattr(self, '_capabilities') else {}
    
    def check_symbol_support(self, symbol: str) -> Dict:
        """Verifică pe ce platforme este suportat un simbol"""
        support = {}
        for platform in self._platforms:
            support[platform.platform_name] = platform.supports_symbol(symbol)
        return support


# ============================================
# EnhancedCachePriceManager cu suport multi-platformă
# ============================================

class EnhancedCachePriceManager(CacheManagerInterface):
    """
    Versiune extinsă a CachePriceManager care folosește multiple platforme
    pentru a obține prețuri la diferite simboluri
    """
    
    def __init__(self, sync_ts, symbols, filename, api_client=api, cmc_api_key: Optional[str] = None):
        # Creează factory-ul de platforme
        self.price_factory = PricePlatformFactory(cmc_api_key=cmc_api_key)
        
        # Salvează simbolurile originale
        self.original_symbols = symbols
        
        # Apelează constructorul părinte
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)
                
        self.active_symbols = set(symbols)  # Simboluri active curente
        self.symbol_added_time: Dict[str, float] = {}  # Timpul când a fost adăugat fiecare simbol

        self._load_symbol_metadata()
    
        # Afișează statusul suportului pentru fiecare simbol
        self._log_symbol_support()

    def _load_symbol_metadata(self):
        """Încarcă metadata despre simboluri din fișierul de cache"""
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r") as f:
                    data = json.load(f)
                    if "symbol_metadata" in data:
                        self.symbol_added_time = data["symbol_metadata"].get("added_time", {})
            except:
                pass

    def _save_symbol_metadata(self):
        """Salvează metadata despre simboluri împreună cu cache-ul"""
        # Această metodă va fi apelată din save_state_to_file_if_enabled
        pass

    def _log_symbol_support(self):
        """Afișează pe ce platformă e suportat fiecare simbol"""
        print(f"[EnhancedPrice] Verificare suport simboluri:")
        for symbol in self.original_symbols:
            support = self.price_factory.check_symbol_support(symbol)
            supported_platforms = [p for p, s in support.items() if s]
            if supported_platforms:
                print(f"  ✅ {symbol} -> {', '.join(supported_platforms)}")
            else:
                print(f"  ❌ {symbol} -> NICI O PLATFORMĂ!")
    
    def rebuild_fetchtime_times(self):
        """Reconstruiește timestamp-urile ultimelor prețuri salvate"""
        if not self.cache:
            return {}
        last_times = {}
        for symbol in self.symbols:
            entries = self.cache.get(symbol, [])
            if entries:
                last_times[symbol] = max(entry[0] for entry in entries)
        return last_times
    
    def get_remote_items(self, symbol, startTime):
        """
        Obține prețul curent folosind platforma potrivită.
        Returnează o listă cu un singur element [timestamp_ms, price]
        """
        try:
            # Obține prețul folosind factory-ul
            result = self.price_factory.get_price(symbol)
            price = result["price"]
            platform_used = result["platform"]
            
            timestamp = int(time.time())  # secunde
            timestamp_ms = timestamp * 1000
            
            # Folosește print (care e deja configurat în sistemul tău)
            print(f"[EnhancedPrice][{symbol}] ${price:.4f} (sursa: {platform_used})")
            
            # Returnează în formatul așteptat de CacheManagerInterface
            return [[timestamp_ms, price]]
            
        except Exception as e:
            print(f"[EnhancedPrice][Eroare] {symbol}: {e}")
            return []
    
    def get_all_symbols_from_cache(self):
        """Returnează toate simbolurile din cache"""
        with self.lock:
            return list(self.cache.keys())
    
    def get_latest_price(self, symbol: str) -> Optional[float]:
        """Obține ultimul preț salvat în cache pentru un simbol"""
        with self.lock:
            entries = self.cache.get(symbol, [])
            if entries:
                return entries[-1][1]  # [timestamp_ms, price]
        return None
    
    def get_price_history(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Obține istoricul prețurilor pentru un simbol"""
        with self.lock:
            entries = self.cache.get(symbol, [])[-limit:]
            return [
                {
                    "timestamp": entry[0],
                    "timestamp_readable": u.timestampToTime(entry[0] // 1000) if hasattr(u, 'timestampToTime') else datetime.fromtimestamp(entry[0]//1000).isoformat(),
                    "price": entry[1]
                }
                for entry in entries
            ]
        

    def add_symbol(self, symbol: str):
        """
        Adaugă un simbol nou în watchlist pentru monitorizare.
        """
        with self.lock:
            # Verifică dacă există deja
            if symbol in self.active_symbols:
                print(f"[EnhancedPrice] {symbol} deja în watchlist")
                return False
            
            # Adaugă în lista de simboluri
            self.symbols.append(symbol)
            self.original_symbols.append(symbol)
            self.active_symbols.add(symbol)
            
            # Înregistrează timpul adăugării
            self.symbol_added_time[symbol] = time.time()
            
            # Inițializează cache-ul pentru noul simbol
            if symbol not in self.cache:
                self.cache[symbol] = []
            
            # Actualizează fetchtime
            if symbol not in self.fetchtime_time_per_symbol:
                self.fetchtime_time_per_symbol[symbol] = self.fallback_time_default
            
            print(f"[EnhancedPrice] ✅ Simbol adăugat: {symbol} (la {datetime.fromtimestamp(self.symbol_added_time[symbol]).strftime('%Y-%m-%d %H:%M:%S')})")
            
            # Forțează o actualizare imediată pentru noul simbol
            self.update_cache_per_symbol(symbol)
            
            return True

    def cleanup_old_symbols(self, max_age_days: int = 7):
        """
        Elimină simbolurile care au fost adăugate de mai mult de 'max_age_days' zile.
        Rulează automat la cleanup.
        """
        cutoff_time = time.time() - max_age_days * 24 * 3600
        removed_symbols = []
        
        with self.lock:
            for symbol, added_time in list(self.symbol_added_time.items()):
                if added_time < cutoff_time:
                    # Simbolul este mai vechi de 7 zile
                    removed_symbols.append(symbol)
                    self.remove_symbol(symbol, reason=f"(mai vechi de {max_age_days} zile)")
            
            if removed_symbols:
                print(f"[Cleanup] Eliminate {len(removed_symbols)} simboluri vechi: {removed_symbols}")
        
        return removed_symbols


    def remove_symbol(self, symbol: str, reason: str = ""):
        """
        Elimină un simbol din watchlist.
        """
        with self.lock:
            if symbol not in self.active_symbols:
                return False
            
            # Elimină din liste
            if symbol in self.symbols:
                self.symbols.remove(symbol)
            if symbol in self.original_symbols:
                self.original_symbols.remove(symbol)
            
            self.active_symbols.discard(symbol)
            
            # Opțional: păstrează cache-ul sau îl ștergi
            # self.cache.pop(symbol, None)
            # self.fetchtime_time_per_symbol.pop(symbol, None)
            
            print(f"[EnhancedPrice] ❌ Simbol eliminat: {symbol} {reason}")
            return True
        
    def cleanup_old_prices(self, retention_days: int = PRICE_HISTORY_RETENTION_DAYS):
        """
        Șterge prețurile mai vechi de 'retention_days' zile.
        Rulează automat la fiecare salvare.
        """
        cutoff_timestamp = (time.time() - retention_days * 24 * 3600) * 1000  # convertim la ms
        
        with self.lock:
            removed_count = 0
            for symbol in list(self.cache.keys()):
                original_count = len(self.cache[symbol])
                
                # Păstrează doar intrările mai noi decât cutoff
                self.cache[symbol] = [
                    entry for entry in self.cache[symbol]
                    if entry[0] >= cutoff_timestamp  # entry[0] este timestamp_ms
                ]
                
                removed = original_count - len(self.cache[symbol])
                if removed > 0:
                    removed_count += removed
                    print(f"[Cleanup] {symbol}: șterse {removed} intrări vechi (păstrate {len(self.cache[symbol])})")
                
                # Dacă simbolul nu mai are date și nu mai e în watchlist, îl putem șterge
                if not self.cache[symbol] and symbol not in self.active_symbols:
                    del self.cache[symbol]
                    print(f"[Cleanup] {symbol}: șters complet (fără date)")
            
            if removed_count > 0:
                print(f"[Cleanup] Total: {removed_count} intrări șterse")


    def save_state_to_file_if_enabled(self):
        """Suprascrie metoda părintelui pentru a adăuga cleanup și metadata"""
        if not self.save_state:
            return
        
        # Curăță prețurile vechi
        self.cleanup_old_prices()
        
        # Curăță simbolurile vechi (mai vechi de 7 zile)
        self.cleanup_old_symbols(max_age_days=PRICE_HISTORY_RETENTION_DAYS)
        
        try:
            with self.lock:
                tmp_file = self.filename + ".tmp"
                with open(tmp_file, "w") as f:
                    json.dump({
                        "items": self.cache,
                        "fetchtime": self.fetchtime_time_per_symbol,
                        "metadata": {
                            "last_cleanup": time.time(),
                            "retention_days": PRICE_HISTORY_RETENTION_DAYS,
                            "symbols_count": len(self.cache),
                            "total_entries": sum(len(v) for v in self.cache.values())
                        },
                        "symbol_metadata": {
                            "added_time": self.symbol_added_time,
                            "active_symbols": list(self.active_symbols)
                        }
                    }, f, indent=1)
                os.replace(tmp_file, self.filename)
                print(f"[{self.cls_name}][info] Save cache to file {self.filename}")
        except Exception as e:
            print(f"[{self.cls_name}][Eroare] La salvarea fișierului cache: {e}")

# ============================================
# Integrare în CacheFactory existent
# ============================================

# Interval de salvare: 5 minute (fix ce ai cerut)
PRICE_MULTI_SYNC_INTERVAL_SEC = 5 * 60

def register_enhanced_price_manager(cmc_api_key: Optional[str] = None):
    """Înregistrează noul manager de prețuri în CacheFactory"""
    
    if not hasattr(CacheFactory, '_CONFIG'):
        CacheFactory._CONFIG = {}
    
    CacheFactory._CONFIG["PriceMulti"] = {
        "class": EnhancedCachePriceManager,
        "filename": "cache_prices_multi.json",
        "sync_ts": lambda: PRICE_MULTI_SYNC_INTERVAL_SEC,
        "cmc_api_key": cmc_api_key
    }
    
    print("[EnhancedPrice] Manager înregistrat în CacheFactory ca 'PriceMulti'")

def create_price_monitor(cmc_api_key: Optional[str] = None):
    """
    Creează și pornește monitorul de prețuri multi-platformă.
    """
    # Construiește lista completă de simboluri
    all_symbols = []
    
    # Adaugă simbolurile din sym.symbols (dacă există)
    if hasattr(sym, 'symbols'):
        for s in sym.symbols:
            # Dacă simbolul e deja în format USDC, păstrează-l
            if s.endswith('USDC') or s.endswith('USDT'):
                all_symbols.append(s)
            else:
                # Adaugă și versiunea simplă (ex: BTC), platformele vor ști ce să facă
                all_symbols.append(s)
    
    # Adaugă simbolurile default (dacă nu sunt deja)
    for sym_default in DEFAULT_SYMBOLS:
        if sym_default not in all_symbols and f"{sym_default}USDC" not in all_symbols:
            all_symbols.append(sym_default)
    
    # Elimină duplicatele
    all_symbols = list(dict.fromkeys(all_symbols))
    
    print(f"[PriceMonitor] Total simboluri de monitorizat: {len(all_symbols)}")
    print(f"[PriceMonitor] Lista: {all_symbols}")
    
    # Înregistrează managerul în factory
    register_enhanced_price_manager(cmc_api_key)
    
    # Creează instanța
    price_manager = CacheFactory.get("PriceMulti", symbols=all_symbols)
    
    # Pornește sincronizarea periodică
    thread = price_manager.periodic_sync(sync_ts=PRICE_MULTI_SYNC_INTERVAL_SEC, save_state=True)
    
    return price_manager


# ============================================
# Exemplu de utilizare
# ============================================

if __name__ == "__main__":
    # Configurează cheia CMC (pune-o aici sau în variabila de mediu)
    CMC_API_KEY = os.environ.get('CMC_API_KEY', None)
    # Dacă ai cheia, seteaz-o așa:
    # CMC_API_KEY = "cheia_ta_reală"
    
    print("=" * 60)
    print("🚀 Pornire monitor prețuri crypto (multi-platformă)")
    print(f"💰 Monedă de bază pentru Europa: {QUOTE_CURRENCY}")
    print("=" * 60)
    
    # Creează monitorul
    monitor = create_price_monitor(cmc_api_key=CMC_API_KEY)
    
    # Așteaptă câteva secunde pentru prima colectare
    print("\n⏳ Așteptăm prima colectare de prețuri...")
    time.sleep(35)
    
    # Afișează prețurile curente
    print("\n📊 Prețuri curente:")
    for symbol in monitor.original_symbols:
        price = monitor.get_latest_price(symbol)
        if price:
            print(f"  {symbol}: ${price:.4f}")
        else:
            print(f"  {symbol}: în așteptare...")
    
    print("\n✅ Monitor activ. Rulează în fundal (Ctrl+C pentru oprire)")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n🛑 Oprire...")