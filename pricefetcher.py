# price_fetcher_managers.py
import json
import time
import threading
from datetime import datetime
from typing import Dict, Optional, List
from abc import ABC, abstractmethod

# Importă modulele tale existente
import log
import utils as u
import symbols as sym
import bapi as api

# Importă clasele de bază din cacheManager
from cacheManager import CacheManagerInterface, CacheFactory, _should_poll_for_manager

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
    
    @property
    @abstractmethod
    def platform_name(self) -> str:
        pass


class BinancePricePlatform(PricePlatformInterface):
    """Platformă pentru prețuri Binance"""
    
    def __init__(self, api_client=None):
        self.api_client = api_client or api
        self._supported_symbols = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT", "DOGEUSDT"]
    
    @property
    def platform_name(self) -> str:
        return "Binance"
    
    def supports_symbol(self, symbol: str) -> bool:
        return symbol in self._supported_symbols
    
    def get_price(self, symbol: str) -> Optional[float]:
        try:
            # Folosește API-ul tău existent
            price = self.api_client.get_current_price(symbol=symbol)
            return float(price)
        except Exception as e:
            print(f"[BinancePlatform] Eroare {symbol}: {e}")
            return None


class HyperliquidPricePlatform(PricePlatformInterface):
    """Platformă pentru prețuri Hyperliquid"""
    
    def __init__(self):
        self._url = "https://api.hyperliquid.xyz/info"
        self._supported_symbols = ["HYPE", "PURR", "BTC", "ETH", "SOL"]
    
    @property
    def platform_name(self) -> str:
        return "Hyperliquid"
    
    def supports_symbol(self, symbol: str) -> bool:
        return symbol in self._supported_symbols
    
    def get_price(self, symbol: str) -> Optional[float]:
        try:
            import requests
            payload = {"type": "allMids"}
            headers = {"Content-Type": "application/json"}
            
            response = requests.post(self._url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            # data e dict: {"BTC": "116845.5", "HYPE": "40.7915"}
            if symbol not in data:
                print(f"[HyperliquidPlatform] Simbol {symbol} negăsit")
                return None
            
            return float(data[symbol])
        except Exception as e:
            print(f"[HyperliquidPlatform] Eroare {symbol}: {e}")
            return None


class CoinMarketCapPricePlatform(PricePlatformInterface):
    """Platformă pentru prețuri CoinMarketCap (necesită API Key)"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get('CMC_API_KEY')
        self._base_url = "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest"
        # Mapare simboluri CMC
        self._symbol_map = {
            "BTC": "BTC",
            "ETH": "ETH", 
            "HYPE": "HYPE",
            "SOL": "SOL",
            "BNB": "BNB",
            "ADA": "ADA",
            "DOGE": "DOGE"
        }
    
    @property
    def platform_name(self) -> str:
        return "CoinMarketCap"
    
    def supports_symbol(self, symbol: str) -> bool:
        return symbol in self._symbol_map and self.api_key is not None
    
    def get_price(self, symbol: str) -> Optional[float]:
        if not self.api_key:
            return None
            
        try:
            import requests
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
            
            price = data['data'][symbol]['quote']['USD']['price']
            return float(price)
        except Exception as e:
            print(f"[CMCPlatform] Eroare {symbol}: {e}")
            return None


# ============================================
# Price Platform Factory
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
    
    def get_price(self, symbol: str) -> Dict:
        """
        Obține prețul folosind prima platformă care suportă simbolul
        
        Returns:
            Dict cu symbol, price, platform, timestamp
        """
        for platform in self._platforms:
            if platform.supports_symbol(symbol):
                price = platform.get_price(symbol)
                if price is not None:
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
    
    def discover_capabilities(self) -> Dict:
        """Descoperă ce simboluri suportă fiecare platformă"""
        capabilities = {}
        for platform in self._platforms:
            # Accesăm lista internă de simboluri
            if hasattr(platform, '_supported_symbols'):
                capabilities[platform.platform_name] = platform._supported_symbols
            else:
                capabilities[platform.platform_name] = "Auto-detect"
        return capabilities


# ============================================
# Enhanced CachePriceManager cu suport multi-platformă
# ============================================

class EnhancedCachePriceManager(CacheManagerInterface):
    """
    Versiune extinsă a CachePriceManager care folosește multiple platforme
    pentru a obține prețuri la diferite simboluri
    """
    
    def __init__(self, sync_ts, symbols, filename, api_client=api, cmc_api_key: Optional[str] = None):
        # Creează factory-ul de platforme
        self.price_factory = PricePlatformFactory(cmc_api_key=cmc_api_key)
        
        # Salvează simbolurile originale (în formatul dorit de utilizator)
        self.original_symbols = symbols
        
        # Mapează simboluri între formatul intern și cel al platformelor
        self.symbol_mapping = self._build_symbol_mapping(symbols)
        
        # Apelează constructorul părinte
        super().__init__(sync_ts, symbols, filename, append_mode=True, api_client=api_client)
    
    def _build_symbol_mapping(self, symbols: List[str]) -> Dict[str, str]:
        """
        Construiește un mapping între simbolurile cerute și cele suportate de platforme.
        De exemplu: "BTC" -> "BTCUSDT" pentru Binance, dar "BTC" -> "BTC" pentru Hyperliquid
        """
        mapping = {}
        for sym in symbols:
            # Pentru simboluri cu USDT (ex: BTCUSDT), le păstrăm așa pentru Binance
            if sym.endswith('USDT'):
                base = sym.replace('USDT', '')
                mapping[sym] = {
                    "Binance": sym,           # BTCUSDT
                    "Hyperliquid": base,      # BTC
                    "CoinMarketCap": base     # BTC
                }
            else:
                # Pentru simboluri fără USDT (ex: HYPE)
                mapping[sym] = {
                    "Binance": f"{sym}USDT" if sym in ["BTC", "ETH", "SOL"] else None,
                    "Hyperliquid": sym,
                    "CoinMarketCap": sym
                }
        return mapping
    
    def rebuild_fetchtime_times(self):
        """Reconstruiește timestamp-urile ultimelor prețuri salvate"""
        if not self.cache:
            return {}
        last_times = {}
        for symbol in self.symbols:
            entries = self.cache.get(symbol, [])
            if entries:
                # entries sunt de forma [timestamp_ms, price]
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
                # Ultimul entry este cel mai recent (append mode)
                return entries[-1][1]  # [timestamp_ms, price]
        return None
    
    def get_price_history(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Obține istoricul prețurilor pentru un simbol"""
        with self.lock:
            entries = self.cache.get(symbol, [])[-limit:]
            return [
                {
                    "timestamp": entry[0],
                    "timestamp_readable": u.timestampToTime(entry[0] // 1000) if hasattr(u, 'timestampToTime') else entry[0],
                    "price": entry[1]
                }
                for entry in entries
            ]


# ============================================
# Integrare în CacheFactory existent
# ============================================

# Configurație pentru tipul nou de cache
PRICE_MULTI_SYNC_INTERVAL_SEC = 5 * 60  # 5 minute - fix ce ai cerut!

# Extindem CacheFactory pentru a include EnhancedCachePriceManager
def register_enhanced_price_manager(cmc_api_key: Optional[str] = None):
    """
    Înregistrează noul manager de prețuri în CacheFactory.
    Rulează această funcție la startup.
    """
    
    # Verifică dacă există deja atributul _CONFIG în CacheFactory
    if not hasattr(CacheFactory, '_CONFIG'):
        CacheFactory._CONFIG = {}
    
    # Adaugă noul tip de cache
    CacheFactory._CONFIG["PriceMulti"] = {
        "class": EnhancedCachePriceManager,
        "filename": "cache_prices_multi.json",
        "sync_ts": lambda: PRICE_MULTI_SYNC_INTERVAL_SEC,
        "cmc_api_key": cmc_api_key  # Parametru opțional
    }
    
    print("[EnhancedPrice] Manager înregistrat în CacheFactory ca 'PriceMulti'")


# ============================================
# Funcție helper pentru a crea și porni noul manager
# ============================================

def create_price_monitor(symbols: List[str], cmc_api_key: Optional[str] = None):
    """
    Creează și pornește monitorul de prețuri multi-platformă.
    
    Args:
        symbols: Lista de simboluri de monitorizat (ex: ["BTC", "ETH", "HYPE", "BTCUSDT"])
        cmc_api_key: Cheia API CoinMarketCap (opțional)
    
    Returns:
        Instanța EnhancedCachePriceManager cu thread-ul de sincronizare pornit
    """
    # Înregistrează managerul în factory
    register_enhanced_price_manager(cmc_api_key)
    
    # Creează instanța folosind factory-ul existent
    price_manager = CacheFactory.get("PriceMulti", symbols=symbols)
    
    # Pornește sincronizarea periodică
    thread = price_manager.periodic_sync(sync_ts=PRICE_MULTI_SYNC_INTERVAL_SEC, save_state=True)
    
    print(f"[PriceMonitor] Pornit pentru {len(symbols)} simboluri, sync la {PRICE_MULTI_SYNC_INTERVAL_SEC}s")
    
    return price_manager


# ============================================
# Exemplu de utilizare și test
# ============================================

if __name__ == "__main__":
    # Configurație - pune aici cheia ta CMC dacă ai
    CMC_API_KEY = None  # sau os.environ.get('CMC_API_KEY')
    
    # Simboluri de monitorizat
    watchlist = [
        "BTC",      # Bitcoin
        "ETH",      # Ethereum  
        "HYPE",     # Hyperliquid (nu e pe Binance)
        "SOL",      # Solana
        "BTCUSDT",  # Varianta cu USDT pentru Binance
    ]
    
    print("=" * 60)
    print("🚀 Pornire monitor prețuri crypto (multi-platformă)")
    print(f"📊 Simboluri: {watchlist}")
    print(f"⏱️  Interval salvare: {PRICE_MULTI_SYNC_INTERVAL_SEC} secunde")
    print("=" * 60)
    
    # Creează și pornește monitorul
    monitor = create_price_monitor(watchlist, cmc_api_key=CMC_API_KEY)
    
    # Așteaptă câteva cicluri pentru test
    try:
        import time
        for i in range(6):  # rulează 6 cicluri (30 minute la 5 min interval)
            time.sleep(PRICE_MULTI_SYNC_INTERVAL_SEC)
            
            # Afișează ultimele prețuri salvate
            print(f"\n--- Status la {datetime.now().strftime('%H:%M:%S')} ---")
            for symbol in watchlist:
                price = monitor.get_latest_price(symbol)
                if price:
                    print(f"  {symbol}: ${price:.4f}")
                else:
                    print(f"  {symbol}: în așteptare...")
            
            # Arată și istoricul pentru primul simbol
            if i == 0:
                history = monitor.get_price_history(watchlist[0], limit=3)
                if history:
                    print(f"\n📜 Istoric {watchlist[0]}:")
                    for h in history:
                        print(f"    {h['timestamp_readable']} -> ${h['price']:.4f}")
    
    except KeyboardInterrupt:
        print("\n🛑 Oprire manuală...")
    finally:
        print("👋 Monitor oprit")