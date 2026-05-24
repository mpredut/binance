#new_coins_discovery.py
import time
import threading
import requests
import pandas as pd
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
NEW_COINS_AGE_DAYS = 30           # O monedă e considerată "nouă" dacă are mai puțin de X zile
MAX_NEW_COINS_TO_TRACK = 50       # Câte monede noi să monitorizeze
REFRESH_INTERVAL_SECONDS = 3600   # Reîmprospătare listă monede noi la fiecare oră

NEW_COIN_MAX_AGE_DAYS = 7  # O monedă nouă e monitorizată maxim 7 zile

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
    """
    Interfață abstractă pentru orice sursă de descoperire a monedelor noi.
    Toate sursele (CMC, CoinGecko, DexScreener, Binance etc.) trebuie să implementeze această clasă.
    """
    
    @abstractmethod
    def get_name(self) -> str:
        """Numele sursei (ex: 'CoinMarketCap', 'CoinGecko', 'Binance')"""
        pass
    
    @abstractmethod
    def get_new_coins(self, days_back: int = 30) -> List[Dict]:
        """
        Returnează lista monedelor noi apărute în ultimele 'days_back' zile.
        
        Fiecare element din listă trebuie să fie un dict cu următoarele câmpuri:
        - symbol: str (simbolul monedei, ex: 'BTC')
        - name: str (numele complet, ex: 'Bitcoin')
        - added_at: datetime (data la care a fost adăugată/descoperită)
        - source: str (numele sursei)
        - price: float (prețul curent, opțional)
        - volume_24h: float (volum 24h, opțional)
        - market_cap: float (market cap, opțional)
        - change_24h: float (variație 24h, opțional)
        - change_7d: float (variație 7d, opțional)
        - url: str (link către monedă, opțional)
        """
        pass
    
    @abstractmethod
    def get_supported_symbols(self) -> Set[str]:
        """Returnează toate simbolurile disponibile pe această sursă"""
        pass
    
    def refresh(self):
        """Reîmprospătează datele (opțional, pentru surse care au cache)"""
        pass
    
    def requires_api_key(self) -> bool:
        """Spune dacă această sursă necesită cheie API"""
        return False
    
    def is_available(self) -> bool:
        """Verifică dacă sursa este disponibilă (ex: cheie API validă)"""
        return True


# ============================================
# Sursa 1: CoinMarketCap (API cu cheie)
# ============================================

class CoinMarketCapSource(NewCoinsSource):
    """Sursă pentru monede noi de pe CoinMarketCap"""
    
    def __init__(self, api_key: str = CMC_API_KEY):
        self.api_key = api_key
        self.headers = {
            'Accepts': 'application/json',
            'X-CMC_PRO_API_KEY': self.api_key,
        }
        self._supported_symbols: Set[str] = set()
        self._last_refresh = 0
        self._cache: List[Dict] = []
        
        # Încarcă date la inițializare
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
        """Reîmprospătează lista de monede de pe CMC"""
        if not self.is_available():
            print("[CMC Source] Cheie API lipsă!")
            return
        
        try:
            # Obține primele 2000 de monede sortate după data adăugării
            params = {
                'start': '1',
                'limit': '2000',
                'convert': 'USD',
                'sort': 'date_added',
                'sort_dir': 'desc'
            }
            
            response = requests.get(
                "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
                headers=self.headers,
                params=params,
                timeout=15
            )
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
                
                print(f"[CMC Source] Încărcate {len(self._cache)} monede, {len(self._supported_symbols)} simboluri unice")
                self._last_refresh = time.time()
            
        except Exception as e:
            print(f"[CMC Source] Eroare: {e}")

    def get_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> List[Dict]:
        """Returnează monedele noi din ultimele 'days_back' zile"""
        if not self._cache:
            self.refresh()
        
        # Folosește datetime UTC cu timezone pentru a evita erorile
        from datetime import timezone
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)
        
        new_coins = []
        for coin in self._cache:
            added_at = coin['added_at']
            # Dacă added_at e naive (fără timezone), adaugă UTC
            if added_at and added_at.tzinfo is None:
                added_at = added_at.replace(tzinfo=timezone.utc)
            if added_at and added_at >= cutoff_date:
                new_coins.append(coin)
        
        print(f"[CMC Source] Găsite {len(new_coins)} monede noi (ultimele {days_back} zile)")
        return new_coins
# ============================================
# Sursa 2: CoinGecko (GRATUIT, fără cheie API)
# ============================================

class CoinGeckoSource(NewCoinsSource):
    """
    Sursă pentru monede noi de pe CoinGecko.
    **NU necesită cheie API** (planul gratuit oferă 50 request-uri/minut)
    """
    
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
        return True  # API-ul public e mereu disponibil
    
    def get_supported_symbols(self) -> Set[str]:
        return self._supported_symbols.copy()
    
    def refresh(self):
        """Reîmprospătează lista de monede de pe CoinGecko"""
        try:
            # Respectă rate limit (50 req/minut = 1 req la 1.2 secunde)
            time.sleep(1.2)
            
            # Obține lista completă de monede
            response = requests.get(
                f"{self.base_url}/coins/list",
                timeout=15
            )
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
                        "added_at": None,  # CoinGecko nu oferă data adăugării în API-ul gratuit
                        "source": self.get_name(),
                        "price": None,  # Va fi completat separat dacă e nevoie
                        "volume_24h": None,
                        "market_cap": None,
                        "change_24h": None,
                        "change_7d": None,
                        "url": f"https://www.coingecko.com/en/coins/{coin.get('id', '')}"
                    })
            
            print(f"[CoinGecko Source] Încărcate {len(self._cache)} monede")
            self._last_refresh = time.time()
            
        except Exception as e:
            print(f"[CoinGecko Source] Eroare: {e}")
    
    def get_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> List[Dict]:
        """
        CoinGecko nu oferă dată de adăugare în API-ul gratuit.
        Vom folosi un artificiu: monedele adăugate recent sunt cele cu ID nou.
        """
        if not self._cache:
            self.refresh()
        
        # CoinGecko nu are date de lansare în API-ul public
        # Returnăm toate monedele (utilizatorul va filtra manual)
        print(f"[CoinGecko Source] {len(self._cache)} monede disponibile (fără dată lansare)")
        return self._cache[:50]  # limităm la 50 pentru performanță


# ============================================
# Sursa 3: Binance (monede nou listate pe exchange)
# ============================================

class BinanceNewListingsSource(NewCoinsSource):
    """
    Sursă pentru monede nou adăugate pe Binance.
    **NU necesită cheie API** (endpoint-uri publice)
    """
    
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
        """
        Detectează monedele nou listate pe Binance comparând
        simbolurile curente cu cele salvate anterior.
        """
        try:
            # Obține toate perechile de tranzacționare
            response = requests.get(
                f"{self.base_url}/exchangeInfo",
                timeout=15
            )
            response.raise_for_status()
            data = response.json()
            
            current_symbols = set()
            symbol_details = {}
            
            for symbol_info in data.get('symbols', []):
                symbol = symbol_info.get('symbol')
                base_asset = symbol_info.get('baseAsset')
                quote_asset = symbol_info.get('quoteAsset')
                status = symbol_info.get('status')
                listed_date = symbol_info.get('listedDate', None)  # Nu există în API-ul public
                
                # Ne interesează doar perechile USDT, USDC, BUSD, EUR
                if quote_asset in ['USDT', 'USDC', 'BUSD', 'EUR'] and status == 'TRADING':
                    current_symbols.add(symbol)
                    symbol_details[symbol] = {
                        "symbol": base_asset,
                        "full_symbol": symbol,
                        "quote": quote_asset,
                        "status": status,
                        "added_at": None  # Binance nu oferă data listării în API-ul public
                    }
            
            # Detectează simbolurile noi (compară cu istoricul)
            if self._historical_symbols:
                new_symbols = current_symbols - self._historical_symbols
                
                if new_symbols:
                    print(f"[Binance Source] 🆕 Monede nou listate: {new_symbols}")
                    
                    for sym in new_symbols:
                        if sym in symbol_details:
                            self._new_listings.append({
                                "symbol": symbol_details[sym]['symbol'],
                                "full_symbol": sym,
                                "name": symbol_details[sym]['symbol'],  # Binance nu dă numele complet
                                "added_at": datetime.now(),  # aproximativ
                                "source": self.get_name(),
                                "price": None,
                                "volume_24h": None,
                                "market_cap": None,
                                "change_24h": None,
                                "change_7d": None,
                                "url": f"https://www.binance.com/en/trade/{sym}"
                            })
            
            # Actualizează istoricul
            self._historical_symbols = current_symbols.copy()
            self._supported_symbols = current_symbols
            self._last_refresh = time.time()
            
            print(f"[Binance Source] {len(current_symbols)} perechi active")
            
        except Exception as e:
            print(f"[Binance Source] Eroare: {e}")
    
    def get_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> List[Dict]:
        """Returnează monedele nou listate pe Binance"""
        if not self._new_listings:
            return []
        
        # Filtrează după dată
        cutoff_date = datetime.now() - timedelta(days=days_back)
        recent = [
            item for item in self._new_listings
            if item['added_at'] and item['added_at'] >= cutoff_date
        ]
        
        return recent


# ============================================
# Sursa 4: DexScreener (monede noi pe DEX-uri, GRATUIT)
# ============================================
# În new_coins_discovery.py, modifică DexScreenerSource

class DexScreenerSource(NewCoinsSource):
    """Sursă pentru monede noi apărute pe DEX-uri"""
    
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
        """Obține cele mai noi token-uri de pe DexScreener"""
        try:
            # Endpoint corect pentru token-profiles (fără /latest/v1)
            response = requests.get(
                f"{self.base_url}/token-profiles",
                timeout=15
            )
            
            if response.status_code == 404:
                # Fallback: încearcă alt endpoint
                response = requests.get(
                    "https://api.dexscreener.com/token-profiles/latest",
                    timeout=15
                )
            
            if response.status_code == 200:
                data = response.json()
                
                self._cache = []
                self._supported_symbols.clear()
                
                # Verifică structura răspunsului
                profiles = data.get('profiles', []) if isinstance(data, dict) else []
                
                for profile in profiles[:100]:  # limităm la 100
                    symbol = profile.get('symbol', '').upper()
                    name = profile.get('name', symbol)
                    chain = profile.get('chainId', 'unknown')
                    token_address = profile.get('tokenAddress', '')
                    listed_at = profile.get('listedAt', None)
                    
                    # Filtrare simboluri valide
                    if symbol and len(symbol) <= 10 and symbol.isalnum() and symbol not in EXCLUDED_SYMBOLS:
                        self._supported_symbols.add(symbol)
                        
                        added_time = None
                        if listed_at:
                            added_time = datetime.fromtimestamp(listed_at / 1000)
                        
                        self._cache.append({
                            "symbol": symbol,
                            "name": name,
                            "added_at": added_time,
                            "source": self.get_name(),
                            "chain": chain,
                            "token_address": token_address,
                            "price": None,
                            "volume_24h": None,
                            "market_cap": None,
                            "change_24h": None,
                            "change_7d": None,
                            "url": f"https://dexscreener.com/{chain}/{token_address}" if token_address else None
                        })
                
                log.print(f"[DexScreener Source] Găsite {len(self._cache)} token-uri")
            else:
                log.print(f"[DexScreener Source] Endpoint indisponibil (status {response.status_code})")
                self._cache = []
            
            self._last_refresh = time.time()
            
        except Exception as e:
            log.print(f"[DexScreener Source] Eroare: {e}")
            self._cache = []
    
    def get_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> List[Dict]:
        """Returnează token-urile noi"""
        if not self._cache:
            return []
        
        cutoff_date = datetime.now() - timedelta(days=days_back)
        
        new_tokens = [
            token for token in self._cache
            if token['added_at'] and token['added_at'] >= cutoff_date
        ]
        
        return new_tokens[:50]  # limităm la 50

# ============================================
# Sursa 5: LunarCrush (monede trending, necesită cheie - opțional)
# ============================================

class LunarCrushSource(NewCoinsSource):
    """
    Sursă pentru monede trending de pe LunarCrush.
    **Necesită cheie API** (se poate obține gratis, limitat)
    """
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get('LUNARCRUSH_API_KEY')
        self.base_url = "https://api.lunarcrush.com/v2"
        self._cache: List[Dict] = []
        self._supported_symbols: Set[str] = set()
        self._last_refresh = 0
    
    def get_name(self) -> str:
        return "LunarCrush"
    
    def requires_api_key(self) -> bool:
        return True
    
    def is_available(self) -> bool:
        return self.api_key is not None
    
    def get_supported_symbols(self) -> Set[str]:
        return self._supported_symbols.copy()
    
    def refresh(self):
        """Obține monedele cu cea mai mare creștere de activitate socială"""
        if not self.is_available():
            print("[LunarCrush Source] Cheie API lipsă")
            return
        
        try:
            params = {
                'key': self.api_key,
                'sort': 'social_score',
                'limit': '50'
            }
            response = requests.get(
                f"{self.base_url}/assets",
                params=params,
                timeout=15
            )
            response.raise_for_status()
            data = response.json()
            
            self._cache = []
            self._supported_symbols.clear()
            
            if 'data' in data:
                for asset in data['data']:
                    symbol = asset.get('symbol', '').upper()
                    if symbol and symbol not in EXCLUDED_SYMBOLS:
                        self._supported_symbols.add(symbol)
                        self._cache.append({
                            "symbol": symbol,
                            "name": asset.get('name', symbol),
                            "added_at": None,
                            "source": self.get_name(),
                            "price": asset.get('price', 0),
                            "volume_24h": asset.get('volume_24h', 0),
                            "market_cap": asset.get('market_cap', 0),
                            "change_24h": asset.get('percent_change_24h', 0),
                            "change_7d": None,
                            "url": f"https://lunarcrush.com/coins/{symbol}"
                        })
            
            print(f"[LunarCrush Source] Încărcate {len(self._cache)} monede trending")
            self._last_refresh = time.time()
            
        except Exception as e:
            print(f"[LunarCrush Source] Eroare: {e}")
    
    def get_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> List[Dict]:
        """Returnează monedele trending (pot fi considerate 'noi' prin popularitate)"""
        if not self._cache:
            self.refresh()
        return self._cache[:30]


# ============================================
# Factory pentru surse de monede noi (DESIGN EXTENSIBIL)
# ============================================

class NewCoinsFactory:
    """
    Factory care gestionează multiple surse de monede noi.
    Poți adăuga oricâte surse noi fără să modifici codul existent.
    """
    
    def __init__(self, enabled_sources: Optional[List[str]] = None, cmc_api_key: str = CMC_API_KEY):
        """
        Args:
            enabled_sources: Lista de nume de surse de activat (None = toate)
            cmc_api_key: Cheia API pentru CoinMarketCap
        """
        # Înregistrează toate sursele disponibile
        self._all_sources: Dict[str, NewCoinsSource] = {
            "coinmarketcap": CoinMarketCapSource(cmc_api_key),
            "coingecko": CoinGeckoSource(),
            "binance": BinanceNewListingsSource(),
            "dexscreener": DexScreenerSource(),
            # "lunarcrush": LunarCrushSource(),  # Opțional, necesită cheie
        }
        
        # Activează doar sursele dorite
        if enabled_sources:
            self.sources = {name: self._all_sources[name] for name in enabled_sources if name in self._all_sources}
        else:
            self.sources = self._all_sources
        
        # Filtrează sursele indisponibile (ex: fără cheie API)
        self.sources = {name: src for name, src in self.sources.items() if src.is_available()}
        
        print(f"[NewCoinsFactory] 🔧 Surse activate: {list(self.sources.keys())}")
    
    def get_all_new_coins(self, days_back: int = NEW_COINS_AGE_DAYS) -> Dict[str, List[Dict]]:
        """Obține monede noi din TOATE sursele activate"""
        results = {}
        for name, source in self.sources.items():
            try:
                results[name] = source.get_new_coins(days_back)
                print(f"[NewCoinsFactory] {name}: {len(results[name])} monede noi")
            except Exception as e:
                print(f"[NewCoinsFactory] Eroare la {name}: {e}")
                results[name] = []
        return results
    
    def get_all_new_symbols(self, days_back: int = NEW_COINS_AGE_DAYS) -> Set[str]:
        """Returnează toate simbolurile noi din toate sursele (uniune)"""
        all_symbols = set()
        for name, source in self.sources.items():
            try:
                coins = source.get_new_coins(days_back)
                for coin in coins:
                    if 'symbol' in coin:
                        all_symbols.add(coin['symbol'])
            except:
                pass
        return all_symbols
    
    def get_source(self, name: str) -> Optional[NewCoinsSource]:
        """Returnează o sursă specifică"""
        return self.sources.get(name)
    
    def get_available_sources(self) -> List[str]:
        """Returnează lista surselor disponibile"""
        return list(self.sources.keys())
    
    def refresh_all(self):
        """Reîmprospătează toate sursele"""
        for source in self.sources.values():
            try:
                source.refresh()
            except Exception as e:
                print(f"[NewCoinsFactory] Eroare refresh {source.get_name()}: {e}")


# ============================================
# Monitor principal (integrează toate sursele)
# ============================================

class NewCoinsMonitor:
    """
    Monitorizează monedele noi din MULTIPLE surse și le integrează
    cu sistemul de prețuri existent.
    """
    
    def __init__(self, price_monitor=None, factory: Optional[NewCoinsFactory] = None):
        """
        Args:
            price_monitor: Instanța EnhancedCachePriceManager (opțional)
            factory: Instanța NewCoinsFactory (opțional)
        """
        self.price_monitor = price_monitor
        self.factory = factory or NewCoinsFactory()
        
        # Cache pentru monede noi
        self.all_new_coins: Dict[str, List[Dict]] = {}
        self.all_symbols: Set[str] = set()
        
        # Callback-uri pentru alerte
        self.alert_callbacks: List[Callable] = []
        
        # Thread pentru refresh periodic
        self._running = False
        self._thread = None
        
        # Primul refresh
        self.refresh()
    
    # În new_coins_discovery.py, în clasa NewCoinsMonitor, adaugă:

    def is_valid_symbol(self, symbol: str) -> bool:
        """
        Verifică dacă un simbol este valid pentru monitorizare.
        """
        if not symbol:
            return False
        
        # Lungime rezonabilă (1-10 caractere)
        if len(symbol) < 1 or len(symbol) > 10:
            return False
        
        # Doar litere și cifre (nu caractere speciale)
        if not symbol.isalnum():
            return False
        
        # Evită simboluri care sunt doar cifre
        if symbol.isdigit():
            return False
        
        # Evită simboluri prea lungi (ex: 01111010011110000110001001110100)
        if len(symbol) > 10:
            return False
        
        # Evită simboluri care conțin doar zerouri
        if symbol == '0' * len(symbol):
            return False
        
        # Simboluri excluse permanent
        excluded = {'USDT', 'USDC', 'BUSD', 'DAI', 'TUSD', 'WBTC', 'WETH', 'BTC', 'ETH', 'BNB', 'SOL', 'XRP', 'ADA', 'DOGE', 'DOT', 'MATIC'}
        if symbol in excluded:
            return False
        
        return True

    def refresh(self):
        """Reîmprospătează toate sursele"""
        self.all_new_coins = self.factory.get_all_new_coins(NEW_COINS_AGE_DAYS)
        self.all_symbols = set()
        for coins in self.all_new_coins.values():
            for coin in coins:
                if 'symbol' in coin:
                    self.all_symbols.add(coin['symbol'])
    
    def register_alert_callback(self, callback: Callable):
        """Înregistrează callback pentru monede noi"""
        self.alert_callbacks.append(callback)
    
    # În aceeași clasă, modifică metoda _trigger_alerts (sau creează una nouă)

    def _trigger_alerts(self, new_coins: List[Dict], source_name: str, auto_add: bool = True):
        """Declanșează callback-urile și adaugă automat monedele noi"""
        for coin in new_coins:
            # Adaugă automat în watchlist dacă e configurat
            if auto_add:
                self.add_new_coin_to_watchlist(coin)
            
            # Trimite și callback-urile existente
            for callback in self.alert_callbacks:
                try:
                    callback({
                        "type": "new_coin_discovered",
                        "source": source_name,
                        "symbol": coin['symbol'],
                        "name": coin.get('name', coin['symbol']),
                        "added_at": coin.get('added_at'),
                        "price": coin.get('price', 0),
                        "auto_added": auto_add,
                        "url": coin.get('url', '')
                    })
                except Exception as e:
                    print(f"[NewCoinsMonitor] Eroare callback: {e}")
    
    def start_monitoring(self, interval_seconds: int = REFRESH_INTERVAL_SECONDS):
        """Pornește monitorizarea continuă"""
        if self._running:
            return
        
        self._running = True
        
        def run():
            print(f"[NewCoinsMonitor] 🔄 Monitorizare pornită (refresh la {interval_seconds}s)")
            print(f"[NewCoinsMonitor] 📡 Surse active: {self.factory.get_available_sources()}")
            
            while self._running:
                try:
                    # Salvează simbolurile vechi
                    old_symbols = self.all_symbols.copy()
                    
                    # Reîmprospătează
                    self.refresh()
                    
                    # Detectează simboluri noi
                    new_symbols = self.all_symbols - old_symbols
                    
                    if new_symbols:
                        print(f"[NewCoinsMonitor] 🆕 Simboluri noi detectate: {new_symbols}")
                        
                        # Trimite alerte pentru fiecare sursă
                        for source_name, coins in self.all_new_coins.items():
                            new_from_source = [c for c in coins if c['symbol'] in new_symbols]
                            if new_from_source:
                                self._trigger_alerts(new_from_source, source_name)
                    
                except Exception as e:
                    print(f"[NewCoinsMonitor] Eroare: {e}")
                
                time.sleep(interval_seconds)
        
        self._thread = threading.Thread(target=run, name="NewCoinsMonitor", daemon=True)
        self._thread.start()
    
    def stop_monitoring(self):
        """Oprește monitorizarea"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("[NewCoinsMonitor] Monitorizare oprită")
    
    def get_report(self) -> str:
        """Generează un raport complet cu monede noi din TOATE sursele"""
        report = "\n" + "=" * 80 + "\n"
        report += f"🆕 RAPORT MONEDE NOI ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})\n"
        report += "=" * 80 + "\n"
        
        for source_name, coins in self.all_new_coins.items():
            if coins:
                report += f"\n📡 SURSĂ: {source_name.upper()} ({len(coins)} monede)\n"
                report += "-" * 40 + "\n"
                
                for coin in coins[:15]:  # primele 15
                    added_str = coin['added_at'].strftime('%Y-%m-%d') if coin['added_at'] else 'N/A'
                    price_str = f"${coin['price']:.6f}" if coin.get('price') else 'N/A'
                    report += f"   🆕 {coin['symbol']} - {coin.get('name', coin['symbol'])}\n"
                    report += f"      📅 Adăugată: {added_str} | 💰 Preț: {price_str}\n"
                    if coin.get('url'):
                        report += f"      🔗 {coin['url']}\n"
        
        report += "\n" + "=" * 80 + "\n"
        return report
    
    def get_summary(self) -> Dict:
        """Returnează un sumar al tuturor surselor"""
        summary = {
            "timestamp": datetime.now().isoformat(),
            "sources": {},
            "total_new_coins": len(self.all_symbols),
            "all_symbols": list(self.all_symbols)[:50]  # primele 50
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
        """
        if not self.price_monitor:
            return False
        
        symbol = coin_info['symbol']
        
        # VALIDARE SIMBOL
        if not self.is_valid_symbol(symbol):
            log.print(f"[NewCoinsMonitor] ❌ Simbol invalid: {symbol} - nu a fost adăugat")
            return False
        
        # Verifică dacă platformele suportă acest simbol
        if hasattr(self.price_monitor, 'price_factory'):
            try:
                # Testează dacă simbolul poate fi obținut
                self.price_monitor.price_factory.get_price(symbol)
            except Exception as e:
                log.print(f"[NewCoinsMonitor] ❌ {symbol} - nu e suportat de nici o platformă: {e}")
                return False
        
        # Verifică dacă e deja monitorizată
        if hasattr(self.price_monitor, 'original_symbols'):
            if symbol in self.price_monitor.original_symbols:
                log.print(f"[NewCoinsMonitor] {symbol} deja în watchlist")
                return False
            
        # Praguri implicite pentru adăugare automată
        if auto_add_thresholds is None:
            auto_add_thresholds = {
                "min_volume_24h": 100000,      # Volum minim $100k
                "min_price_usd": 0.0001,       # Preț minim $0.0001
                "max_price_usd": 1000,         # Preț maxim $1000
                "min_change_24h": -50,         # Nu adăuga dacă a scăzut mai mult de 50%
                "max_change_24h": 500,         # Nu adăuga dacă a crescut mai mult de 500%
            }
        
        # Verifică pragurile (dacă avem date)
        volume = coin_info.get('volume_24h', 0)
        price = coin_info.get('price', 0)
        change = coin_info.get('change_24h', 0)
        
        if volume and volume < auto_add_thresholds["min_volume_24h"]:
            print(f"[NewCoinsMonitor] {symbol} - volum prea mic (${volume:,.0f})")
            return False
        
        if price and price < auto_add_thresholds["min_price_usd"]:
            print(f"[NewCoinsMonitor] {symbol} - preț prea mic (${price})")
            return False
        
        if price and price > auto_add_thresholds["max_price_usd"]:
            print(f"[NewCoinsMonitor] {symbol} - preț prea mare (${price})")
            return False
        
        if change and change < auto_add_thresholds["min_change_24h"]:
            print(f"[NewCoinsMonitor] {symbol} - scădere prea mare ({change}%)")
            return False
        
        if change and change > auto_add_thresholds["max_change_24h"]:
            print(f"[NewCoinsMonitor] {symbol} - creștere suspectă ({change}%)")
            return False
        
        # Adaugă simbolul în watchlist-ul price_monitor
        # Notă: EnhancedCachePriceManager are nevoie de o metodă add_symbol()
        # Vom adăuga această metodă mai jos în price_fetcher_managers.py
        try:
            if hasattr(self.price_monitor, 'add_symbol'):
                self.price_monitor.add_symbol(symbol)
                print(f"[NewCoinsMonitor] ✅ {symbol} adăugat în watchlist!")
                return True
            else:
                # Fallback: loghează și returnează False
                print(f"[NewCoinsMonitor] ⚠️ price_monitor nu are add_symbol() - {symbol} nu a fost adăugat")
                return False
        except Exception as e:
            print(f"[NewCoinsMonitor] Eroare la adăugarea {symbol}: {e}")
            return False

    def should_keep_monitoring(self, symbol: str) -> bool:
        """
        Verifică dacă o monedă nouă ar trebui să rămână în watchlist.
        Returnează False dacă a trecut mai mult de NEW_COIN_MAX_AGE_DAYS de la descoperire.
        """
        if not hasattr(self.price_monitor, 'symbol_added_time'):
            return True  # Dacă nu avem timestamp, păstrăm
        
        added_time = self.price_monitor.symbol_added_time.get(symbol)
        if not added_time:
            return True
        
        age_days = (time.time() - added_time) / (24 * 3600)
        
        if age_days > NEW_COIN_MAX_AGE_DAYS:
            print(f"[NewCoinsMonitor] {symbol} - monitorizat de {age_days:.1f} zile, scoatem din watchlist")
            return False
        
        return True

    def cleanup_old_new_coins(self):
        """
        Elimină monedele vechi din watchlist.
        Rulează periodic.
        """
        if not self.price_monitor:
            return
        
        removed = []
        for symbol in list(self.all_symbols):
            if not self.should_keep_monitoring(symbol):
                self.price_monitor.remove_symbol(symbol, reason="monedă nouă prea veche")
                self.all_symbols.discard(symbol)
                removed.append(symbol)
        
        if removed:
            print(f"[NewCoinsMonitor] Curățate {len(removed)} monede vechi din watchlist")
            
# ============================================
# Funcții de conveniență
# ============================================

def create_new_coins_monitor(price_monitor=None, enabled_sources=None, cmc_api_key=CMC_API_KEY):
    """
    Creează și pornește monitorul pentru monede noi.
    
    Args:
        price_monitor: Instanța EnhancedCachePriceManager
        enabled_sources: Listă de surse de activat (None = toate)
        cmc_api_key: Cheia API pentru CoinMarketCap
    
    Returns:
        Instanța NewCoinsMonitor
    """
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
    print("🆕 TEST MODUL DESCOPERIRE MONEDE NOI (MULTIPLE SURSE)")
    print("=" * 80)
    
    # Creează monitorul cu toate sursele
    monitor, factory = create_new_coins_monitor()
    
    # Afișează sursele active
    print(f"\n📡 Surse active: {factory.get_available_sources()}")
    
    # Pornește monitorizarea (opțional, pentru test)
    # monitor.start_monitoring(interval_seconds=60)
    
    # Afișează raportul complet
    print(monitor.get_report())
    
    # Afișează sumarul
    summary = monitor.get_summary()
    print(f"\n📊 SUMAR:")
    print(f"   Total simboluri noi: {summary['total_new_coins']}")
    for source, data in summary['sources'].items():
        print(f"   {source}: {data['count']} monede")
    
    print("\n✅ Test complet")