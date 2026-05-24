# new_coins_discovery.py
import time
import threading
import requests
import pandas as pd
import os  # ← ADĂUGAT
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Set, Callable
from abc import ABC, abstractmethod
from collections import defaultdict

# Importă modulele tale existente
import log
import utils as u

# ============================================
# Configurare globală
# ============================================

# Cheia API CoinMarketCap (înlocuiește cu a ta)
CMC_API_KEY = "4d587781-722b-40a3-83f0-2436d45942f7"

# Configurare descoperire
NEW_COINS_AGE_DAYS = 30
MAX_NEW_COINS_TO_TRACK = 50
REFRESH_INTERVAL_SECONDS = 3600
NEW_COIN_MAX_AGE_DAYS = 7

# Simboluri excluse automat
EXCLUDED_SYMBOLS = {
    "USDT", "USDC", "BUSD", "DAI", "TUSD", "USDP", "GUSD", "FDUSD",
    "WBTC", "WETH", "stETH", "rETH", "LIDO", "STETH",
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "MATIC", "DOT"
}

# ============================================
# Interfața abstractă pentru surse de monede noi
# ============================================

class NewCoinsSource(ABC):
    @abstractmethod
    def get_name(self) -> str:
        pass
    
    @abstractmethod
    def get_new_coins(self, days_back: int = 30) -> List[Dict]:
        pass
    
    @abstractmethod
    def get_supported_symbols(self) -> Set[str]:
        pass
    
    def refresh(self):
        pass
    
    def requires_api_key(self) -> bool:
        return False
    
    def is_available(self) -> bool:
        return True

# ============================================
# Sursa 1: CoinMarketCap
# ============================================

class CoinMarketCapSource(NewCoinsSource):
    def __init__(self, api_key: str = CMC_API_KEY):
        self.api_key = api_key
        self.headers = {'Accepts': 'application/json', 'X-CMC_PRO_API_KEY': self.api_key}
        self._supported_symbols: Set[str] = set()
        self._last_refresh = 0
        self._cache: List[Dict] = []
        if self.api_key:
            self.refresh()
    
    def get_name(self) -> str:
        return "CoinMarketCap"
    
    def requires_api_key(self) -> bool:
        return True
    
    def is_available(self) -> bool:
        return self.api_key is not None and len(self.api_key) > 0
    
    def get_supported_symbols(self) -> Set[str]:
        return self._supported_symbols.copy()
    
    def refresh(self):
        if not self.is_available():
            print("[CMC Source] Cheie API lipsă!")
            return
        try:
            params = {'start': '1', 'limit': '2000', 'convert': 'USD', 'sort': 'date_added', 'sort_dir': 'desc'}
            response = requests.get("https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest", headers=self.headers, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            if 'data' in data:
                self._cache = []
                self._supported_symbols.clear()
                for coin in data['data']:
                    symbol = coin['symbol']
                    if symbol not in EXCLUDED_SYMBOLS:
                        self._supported_symbols.add(symbol)
                        self._cache.append({
                            "symbol": symbol,
                            "name": coin['name'],
                            "added_at": pd.to_datetime(coin['date_added']),
                            "source": self.get_name(),
                            "price": coin['quote']['USD'].get('price', 0),
                            "volume_24h": coin['quote']['USD'].get('volume_24h', 0),
                            "market_cap": coin['quote']['USD'].get('market_cap', 0),
                            "change_24h": coin['quote']['USD'].get('percent_change_24h', 0),
                            "change_7d": coin['quote']['USD'].get('percent_change_7d', 0),
                            "url": f"https://coinmarketcap.com/currencies/{coin['slug']}/"
                        })
                print(f"[CMC Source] Încărcate {len(self._cache)} monede")
                self._last_refresh = time.time()
        except Exception as e:
            print(f"[CMC Source] Eroare: {e}")

    def get_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> List[Dict]:
        if not self._cache:
            self.refresh()
        from datetime import timezone
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)
        new_coins = []
        for coin in self._cache:
            added_at = coin['added_at']
            if added_at and added_at.tzinfo is None:
                added_at = added_at.replace(tzinfo=timezone.utc)
            if added_at and added_at >= cutoff_date:
                new_coins.append(coin)
        return new_coins

# ============================================
# Sursa 2: CoinGecko
# ============================================

class CoinGeckoSource(NewCoinsSource):
    def __init__(self):
        self.base_url = "https://api.coingecko.com/api/v3"
        self._supported_symbols: Set[str] = set()
        self._cache: List[Dict] = []
        self._last_refresh = 0
        self.refresh()
    
    def get_name(self) -> str:
        return "CoinGecko"
    
    def requires_api_key(self) -> bool:
        return False
    
    def is_available(self) -> bool:
        return True
    
    def get_supported_symbols(self) -> Set[str]:
        return self._supported_symbols.copy()
    
    def refresh(self):
        try:
            time.sleep(1.2)
            response = requests.get(f"{self.base_url}/coins/list", timeout=15)
            response.raise_for_status()
            data = response.json()
            self._cache = []
            self._supported_symbols.clear()
            for coin in data:
                symbol = coin.get('symbol', '').upper()
                if symbol and symbol not in EXCLUDED_SYMBOLS:
                    self._supported_symbols.add(symbol)
                    self._cache.append({
                        "symbol": symbol,
                        "name": coin.get('name', symbol),
                        "added_at": None,
                        "source": self.get_name(),
                        "price": None, "volume_24h": None, "market_cap": None,
                        "change_24h": None, "change_7d": None,
                        "url": f"https://www.coingecko.com/en/coins/{coin.get('id', '')}"
                    })
            print(f"[CoinGecko Source] Încărcate {len(self._cache)} monede")
            self._last_refresh = time.time()
        except Exception as e:
            print(f"[CoinGecko Source] Eroare: {e}")
    
    def get_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> List[Dict]:
        if not self._cache:
            self.refresh()
        return self._cache[:50]

# ============================================
# Sursa 3: Binance
# ============================================

class BinanceNewListingsSource(NewCoinsSource):
    def __init__(self):
        self.base_url = "https://api.binance.com/api/v3"
        self._supported_symbols: Set[str] = set()
        self._new_listings: List[Dict] = []
        self._last_refresh = 0
        self._historical_symbols: Set[str] = set()
        self.refresh()
    
    def get_name(self) -> str:
        return "Binance"
    
    def requires_api_key(self) -> bool:
        return False
    
    def is_available(self) -> bool:
        return True
    
    def get_supported_symbols(self) -> Set[str]:
        return self._supported_symbols.copy()
    
    def refresh(self):
        try:
            response = requests.get(f"{self.base_url}/exchangeInfo", timeout=15)
            response.raise_for_status()
            data = response.json()
            current_symbols = set()
            symbol_details = {}
            for symbol_info in data.get('symbols', []):
                symbol = symbol_info.get('symbol')
                base_asset = symbol_info.get('baseAsset')
                quote_asset = symbol_info.get('quoteAsset')
                status = symbol_info.get('status')
                if quote_asset in ['USDT', 'USDC', 'BUSD', 'EUR'] and status == 'TRADING':
                    current_symbols.add(symbol)
                    symbol_details[symbol] = {"symbol": base_asset, "full_symbol": symbol, "quote": quote_asset}
            if self._historical_symbols:
                new_symbols = current_symbols - self._historical_symbols
                if new_symbols:
                    print(f"[Binance Source] 🆕 Monede nou listate: {new_symbols}")
                    for sym in new_symbols:
                        if sym in symbol_details:
                            self._new_listings.append({
                                "symbol": symbol_details[sym]['symbol'],
                                "full_symbol": sym,
                                "name": symbol_details[sym]['symbol'],
                                "added_at": datetime.now(),
                                "source": self.get_name(),
                                "price": None, "volume_24h": None, "market_cap": None,
                                "change_24h": None, "change_7d": None,
                                "url": f"https://www.binance.com/en/trade/{sym}"
                            })
            self._historical_symbols = current_symbols.copy()
            self._supported_symbols = current_symbols
            self._last_refresh = time.time()
            print(f"[Binance Source] {len(current_symbols)} perechi active")
        except Exception as e:
            print(f"[Binance Source] Eroare: {e}")
    
    def get_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> List[Dict]:
        if not self._new_listings:
            return []
        cutoff_date = datetime.now() - timedelta(days=days_back)
        return [item for item in self._new_listings if item['added_at'] and item['added_at'] >= cutoff_date]

# ============================================
# Sursa 4: DexScreener
# ============================================

class DexScreenerSource(NewCoinsSource):
    def __init__(self):
        self.base_url = "https://api.dexscreener.com/latest/dex"
        self._cache: List[Dict] = []
        self._supported_symbols: Set[str] = set()
        self._last_refresh = 0
        self.refresh()
    
    def get_name(self) -> str:
        return "DexScreener"
    
    def requires_api_key(self) -> bool:
        return False
    
    def is_available(self) -> bool:
        return True
    
    def get_supported_symbols(self) -> Set[str]:
        return self._supported_symbols.copy()
    
    def refresh(self):
        try:
            response = requests.get(f"{self.base_url}/token-profiles", timeout=15)
            if response.status_code == 404:
                response = requests.get("https://api.dexscreener.com/token-profiles/latest", timeout=15)
            if response.status_code == 200:
                data = response.json()
                self._cache = []
                self._supported_symbols.clear()
                profiles = data.get('profiles', []) if isinstance(data, dict) else []
                for profile in profiles[:100]:
                    symbol = profile.get('symbol', '').upper()
                    name = profile.get('name', symbol)
                    chain = profile.get('chainId', 'unknown')
                    token_address = profile.get('tokenAddress', '')
                    listed_at = profile.get('listedAt', None)
                    if symbol and len(symbol) <= 10 and symbol.isalnum() and symbol not in EXCLUDED_SYMBOLS:
                        self._supported_symbols.add(symbol)
                        added_time = datetime.fromtimestamp(listed_at / 1000) if listed_at else None
                        self._cache.append({
                            "symbol": symbol, "name": name, "added_at": added_time,
                            "source": self.get_name(), "chain": chain, "token_address": token_address,
                            "price": None, "volume_24h": None, "market_cap": None,
                            "change_24h": None, "change_7d": None,
                            "url": f"https://dexscreener.com/{chain}/{token_address}" if token_address else None
                        })
                print(f"[DexScreener Source] Găsite {len(self._cache)} token-uri")
            else:
                self._cache = []
            self._last_refresh = time.time()
        except Exception as e:
            print(f"[DexScreener Source] Eroare: {e}")
            self._cache = []
    
    def get_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> List[Dict]:
        if not self._cache:
            return []
        cutoff_date = datetime.now() - timedelta(days=days_back)
        return [token for token in self._cache if token['added_at'] and token['added_at'] >= cutoff_date][:50]

# ============================================
# Factory pentru surse de monede noi
# ============================================

class NewCoinsFactory:
    def __init__(self, enabled_sources: Optional[List[str]] = None, cmc_api_key: str = CMC_API_KEY):
        self._all_sources: Dict[str, NewCoinsSource] = {
            "coinmarketcap": CoinMarketCapSource(cmc_api_key),
            "coingecko": CoinGeckoSource(),
            "binance": BinanceNewListingsSource(),
            "dexscreener": DexScreenerSource(),
        }
        if enabled_sources:
            self.sources = {name: self._all_sources[name] for name in enabled_sources if name in self._all_sources}
        else:
            self.sources = self._all_sources
        self.sources = {name: src for name, src in self.sources.items() if src.is_available()}
        print(f"[NewCoinsFactory] 🔧 Surse activate: {list(self.sources.keys())}")
    
    def get_all_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> Dict[str, List[Dict]]:
        results = {}
        for name, source in self.sources.items():
            try:
                results[name] = source.get_new_coins(days_back)
            except Exception as e:
                print(f"[NewCoinsFactory] Eroare la {name}: {e}")
                results[name] = []
        return results
    
    def get_all_new_symbols(self, days_back: int = NEW_COINS_AGE_DAYS) -> Set[str]:
        all_symbols = set()
        for name, source in self.sources.items():
            try:
                for coin in source.get_new_coins(days_back):
                    if 'symbol' in coin:
                        all_symbols.add(coin['symbol'])
            except:
                pass
        return all_symbols
    
    def get_available_sources(self) -> List[str]:
        return list(self.sources.keys())
    
    def refresh_all(self):
        for source in self.sources.values():
            try:
                source.refresh()
            except Exception as e:
                print(f"[NewCoinsFactory] Eroare refresh {source.get_name()}: {e}")

# ============================================
# Monitor principal
# ============================================

class NewCoinsMonitor:
    def __init__(self, price_monitor=None, factory: Optional[NewCoinsFactory] = None):
        self.price_monitor = price_monitor
        self.factory = factory or NewCoinsFactory()
        self.all_new_coins: Dict[str, List[Dict]] = {}
        self.all_symbols: Set[str] = set()
        self.alert_callbacks: List[Callable] = []
        self._running = False
        self._thread = None
        self.refresh()

    def is_valid_symbol(self, symbol: str) -> bool:
        if not symbol:
            return False
        if len(symbol) < 1 or len(symbol) > 10:
            return False
        if not symbol.isalnum():
            return False
        if symbol.isdigit():
            return False
        if symbol == '0' * len(symbol):
            return False
        excluded = {'USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'WBTC', 'WETH', 
                    'BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'DOT', 'MATIC'}
        return symbol not in excluded

    def refresh(self):
        self.all_new_coins = self.factory.get_all_new_coins(NEW_COINS_AGE_DAYS)
        self.all_symbols = set()
        for source_name, coins in self.all_new_coins.items():
            for coin in coins:
                symbol = coin.get('symbol')
                if symbol and self.is_valid_symbol(symbol):
                    self.all_symbols.add(symbol)
                elif symbol:
                    print(f"[NewCoinsMonitor] 🚫 Simbol invalid ignorat: {symbol} (sursa: {source_name})")

    def register_alert_callback(self, callback: Callable):
        self.alert_callbacks.append(callback)

    def _trigger_alerts(self, new_coins: List[Dict], source_name: str, auto_add: bool = True):
        for coin in new_coins:
            source_has_price = (source_name == "CoinMarketCap")
            if auto_add and source_has_price:
                added = self.add_new_coin_to_watchlist(coin)
                auto_added_status = added
            else:
                auto_added_status = False
                if source_name != "CoinMarketCap":
                    print(f"[NewCoinsMonitor] ℹ️ {coin['symbol']} descoperit pe {source_name} - doar informațional (nu are preț în sistem)")
            for callback in self.alert_callbacks:
                try:
                    callback({
                        "type": "new_coin_discovered",
                        "source": source_name,
                        "symbol": coin['symbol'],
                        "name": coin.get('name', coin['symbol']),
                        "added_at": coin.get('added_at'),
                        "price": coin.get('price', 0),
                        "auto_added": auto_added_status,
                        "has_price": source_has_price,
                        "url": coin.get('url', '')
                    })
                except Exception as e:
                    print(f"[NewCoinsMonitor] Eroare callback: {e}")

    def start_monitoring(self, interval_seconds: int = REFRESH_INTERVAL_SECONDS):
        if self._running:
            return
        self._running = True
        def run():
            print(f"[NewCoinsMonitor] 🔄 Monitorizare pornită (refresh la {interval_seconds}s)")
            print(f"[NewCoinsMonitor] 📡 Surse active: {self.factory.get_available_sources()}")
            print(f"[NewCoinsMonitor] ⚠️ DOAR monedele de pe CoinMarketCap vor fi adăugate automat (au preț)")
            while self._running:
                try:
                    old_symbols = self.all_symbols.copy()
                    self.refresh()
                    new_symbols = self.all_symbols - old_symbols
                    if new_symbols:
                        print(f"[NewCoinsMonitor] 🆕 Simboluri noi detectate: {new_symbols}")
                        for source_name, coins in self.all_new_coins.items():
                            new_from_source = [c for c in coins if c['symbol'] in new_symbols]
                            if new_from_source:
                                auto_add = (source_name == "CoinMarketCap")
                                self._trigger_alerts(new_from_source, source_name, auto_add=auto_add)
                except Exception as e:
                    print(f"[NewCoinsMonitor] Eroare: {e}")
                time.sleep(interval_seconds)
        self._thread = threading.Thread(target=run, name="NewCoinsMonitor", daemon=True)
        self._thread.start()

    def stop_monitoring(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("[NewCoinsMonitor] Monitorizare oprită")

    def get_report(self) -> str:
        report = "\n" + "=" * 80 + "\n"
        report += f"🆕 RAPORT MONEDE NOI ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n"
        report += "=" * 80 + "\n"
        for source_name, coins in self.all_new_coins.items():
            if coins:
                report += f"\n📡 SURSĂ: {source_name.upper()} ({len(coins)} monede)\n"
                report += "-" * 40 + "\n"
                for coin in coins[:15]:
                    added_str = coin['added_at'].strftime('%Y-%m-%d') if coin['added_at'] else 'N/A'
                    price_str = f"${coin['price']:.6f}" if coin.get('price') else 'N/A'
                    report += f"   🆕 {coin['symbol']} - {coin.get('name', coin['symbol'])}\n"
                    report += f"      📅 Adăugată: {added_str} | 💰 Preț: {price_str}\n"
                    if coin.get('url'):
                        report += f"      🔗 {coin['url']}\n"
        report += "\n" + "=" * 80 + "\n"
        return report

    def get_summary(self) -> Dict:
        summary = {
            "timestamp": datetime.now().isoformat(),
            "sources": {},
            "total_new_coins": len(self.all_symbols),
            "all_symbols": list(self.all_symbols)[:50]
        }
        for source_name, coins in self.all_new_coins.items():
            summary["sources"][source_name] = {
                "count": len(coins),
                "symbols": [c['symbol'] for c in coins][:20]
            }
        return summary

    def add_new_coin_to_watchlist(self, coin_info: Dict, auto_add_thresholds: Optional[Dict] = None):
        """
        Adaugă o monedă nou descoperită în watchlist-ul de prețuri.
        Filtrele sunt DOAR INFORMATIVE - moneda se adaugă ORICUM.
        """
        if not self.price_monitor:
            print(f"[NewCoinsMonitor] Nu există price_monitor")
            return False
        
        symbol = coin_info['symbol']
        source = coin_info.get('source', 'unknown')
        
        if not self.is_valid_symbol(symbol):
            print(f"[NewCoinsMonitor] ❌ Simbol invalid: {symbol}")
            return False
        
        if hasattr(self.price_monitor, 'original_symbols') and symbol in self.price_monitor.original_symbols:
            print(f"[NewCoinsMonitor] {symbol} deja în watchlist")
            return False
        
        # Praguri pentru verificări INFORMATIVE (nu blochează)
        if auto_add_thresholds is None:
            auto_add_thresholds = {
                "min_volume_24h": 100000,      # Volum minim $100k
                "min_price_usd": 0.000001,     # Preț minim
                "max_price_usd": 100000,       # Preț maxim
                "min_change_24h": -90,         # Scădere minimă acceptată
                "max_change_24h": 1000,        # Creștere maximă acceptată
            }
        
        # Verifică pragurile DOAR PENTRU AFIȘARE (nu blochează)
        volume = coin_info.get('volume_24h', 0)
        price = coin_info.get('price', 0)
        change = coin_info.get('change_24h', 0)
        
        issues = []
        
        if volume and volume < auto_add_thresholds["min_volume_24h"]:
            issues.append(f"⚠️ volum sub prag (${volume:,.0f} < ${auto_add_thresholds['min_volume_24h']:,.0f})")
        
        if price and price < auto_add_thresholds["min_price_usd"]:
            issues.append(f"⚠️ preț sub prag (${price:.8f} < ${auto_add_thresholds['min_price_usd']})")
        
        if price and price > auto_add_thresholds["max_price_usd"]:
            issues.append(f"⚠️ preț peste prag (${price:.2f} > ${auto_add_thresholds['max_price_usd']})")
        
        if change and change < auto_add_thresholds["min_change_24h"]:
            issues.append(f"⚠️ scădere sub prag ({change:.1f}% < {auto_add_thresholds['min_change_24h']}%)")
        
        if change and change > auto_add_thresholds["max_change_24h"]:
            issues.append(f"⚠️ creștere peste prag ({change:.1f}% > {auto_add_thresholds['max_change_24h']}%)")
        
        # Afișează avertismentele (DAR NU BLOCHEAZĂ)
        if issues:
            print(f"[NewCoinsMonitor] 📊 {symbol}: {', '.join(issues)}")
        else:
            print(f"[NewCoinsMonitor] 📊 {symbol} - TOATE PRAGURILE ÎNDEPLINITE ✅")
        
        # Adaugă simbolul în watchlist (ÎNTOTDEAUNA, indiferent de filtre)
        try:
            if hasattr(self.price_monitor, 'add_symbol'):
                self.price_monitor.add_symbol(symbol, preferred_source=source)
                print(f"[NewCoinsMonitor] ✅ {symbol} adăugat în watchlist (preț de pe {source})")
                return True
            else:
                print(f"[NewCoinsMonitor] ⚠️ price_monitor nu are add_symbol()")
                return False
        except Exception as e:
            print(f"[NewCoinsMonitor] Eroare la adăugarea {symbol}: {e}")
            return False

    def should_keep_monitoring(self, symbol: str) -> bool:
        if not hasattr(self.price_monitor, 'symbol_added_time'):
            return True
        added_time = self.price_monitor.symbol_added_time.get(symbol)
        if not added_time:
            return True
        age_days = (time.time() - added_time) / (24 * 3600)
        if age_days > NEW_COIN_MAX_AGE_DAYS:
            return False
        return True

    def cleanup_old_new_coins(self):
        if not self.price_monitor:
            return
        removed = []
        for symbol in list(self.all_symbols):
            if not self.should_keep_monitoring(symbol):
                if hasattr(self.price_monitor, 'remove_symbol'):
                    self.price_monitor.remove_symbol(symbol, reason="monedă nouă prea veche")
                self.all_symbols.discard(symbol)
                removed.append(symbol)
        if removed:
            print(f"[NewCoinsMonitor] Curățate {len(removed)} monede vechi din watchlist")

# ============================================
# Funcții de conveniență
# ============================================

def create_new_coins_monitor(price_monitor=None, enabled_sources=None, cmc_api_key=CMC_API_KEY):
    factory = NewCoinsFactory(enabled_sources=enabled_sources, cmc_api_key=cmc_api_key)
    monitor = NewCoinsMonitor(price_monitor, factory)
    print("[NewCoins] ✅ Monitor monede noi inițializat")
    print(f"[NewCoins] 📡 Surse active: {factory.get_available_sources()}")
    return monitor, factory

# ============================================
# Test
# ============================================

if __name__ == "__main__":
    print("=" * 80)
    print("🆕 TEST MODUL DESCOPERIRE MONEDE NOI")
    print("=" * 80)
    monitor, factory = create_new_coins_monitor()
    print(f"\n📡 Surse active: {factory.get_available_sources()}")
    print(monitor.get_report())
    summary = monitor.get_summary()
    print(f"\n📊 SUMAR: Total simboluri noi: {summary['total_new_coins']}")
    for source, data in summary['sources'].items():
        print(f"   {source}: {data['count']} monede")
    print("\n✅ Test complet")