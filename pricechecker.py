# pricechecker.py
import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable
from collections import defaultdict

# Importă modulele tale existente
import log
import utils as u

# Constante pentru praguri (poți modifica oricând)
PRICE_ALERT_CONFIG = {
    "up_percent": 0.1,      # Alertă când prețul crește cu 5% față de minimul 24h
    "down_percent": 7.5,    # Alertă când prețul scade cu 7.5% față de maximul 24h
    "lookback_hours": 24,   # Intervalul de analiză (24 ore)
    "cooldown_minutes": 15,  # Nu trimite aceeași alertă mai des de 15 minute
}


class PriceAlert:
    """Structură pentru o alertă de preț"""
    
    def __init__(self, symbol: str, alert_type: str, current_price: float, 
                 reference_price: float, percent_change: float, threshold: float):
        self.symbol = symbol
        self.alert_type = alert_type  # "up" sau "down"
        self.current_price = current_price
        self.reference_price = reference_price
        self.percent_change = percent_change
        self.threshold = threshold
        self.timestamp = time.time()
    
    def __str__(self) -> str:
        direction = "🚀 RISE" if self.alert_type == "up" else "📉 DROP"
        emoji = "🟢" if self.alert_type == "up" else "🔴"
        return (f"\n{emoji} {direction} {emoji}\n"
                f"📊 {self.symbol}\n"
                f"💰 Current price: ${self.current_price:.4f}\n"
                f"📈 Reference: ${self.reference_price:.4f} ({'24h low' if self.alert_type == 'up' else '24h high'})\n"
                f"📊 Change: {self.percent_change:+.2f}% (threshold: {self.threshold}%)\n"
                f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "alert_type": self.alert_type,
            "current_price": self.current_price,
            "reference_price": self.reference_price,
            "percent_change": self.percent_change,
            "threshold": self.threshold,
            "timestamp": self.timestamp,
            "timestamp_readable": datetime.now().isoformat()
        }


class PriceChecker:
    """
    Analizează prețurile din cache și generează alerte când sunt depășite pragurile.
    Rulează într-un thread separat.
    """
    
    def __init__(self, price_manager, alert_callback: Optional[Callable] = None):
        """
        Args:
            price_manager: Instanța de EnhancedCachePriceManager
            alert_callback: Funcție apelată când apare o alertă (ex: print, trimitere email/telegram)
        """
        self.price_manager = price_manager
        self.alert_callback = alert_callback or self._default_alert_handler
        self.config = PRICE_ALERT_CONFIG.copy()
        
        # Previn spam-ul: ține minte ultima alertă per simbol și tip
        self._last_alert_time = defaultdict(float)
        
        # Thread pentru analiză continuă
        self._thread = None
        self._running = False
    
    def _default_alert_handler(self, alert: PriceAlert):
        """Handler default care afișează alerta în consolă"""
        print("\n" + "=" * 60)
        print(str(alert))
        print("=" * 60)
    # pricechecker.py - versiunea corectată

    def _get_price_history_last_hours(self, symbol: str, hours: int) -> List[Dict]:
        """
        Obține istoricul prețurilor din ultimele 'hours' ore.
        """
        history = self.price_manager.get_price_history(symbol, limit=1000)
        
        if not history:
            return []
        
        cutoff_time = (time.time() - hours * 3600) * 1000  # milisecunde
        
        # Filtrează doar intrările din ultimele X ore
        recent_history = [
            entry for entry in history 
            if entry["timestamp"] >= cutoff_time
        ]
        
        return recent_history
    
    def _calculate_24h_stats(self, symbol: str) -> Dict:
        """
        Calculează minimul, maximul și variațiile pentru ultimele 24 de ore.
        
        Returns:
            Dict cu:
            - min_price: prețul minim în ultimele 24h
            - max_price: prețul maxim în ultimele 24h
            - current_price: prețul curent
            - up_from_min: creșterea procentuală față de minim
            - down_from_max: scăderea procentuală față de maxim
            - has_data: True dacă există suficiente date
        """
        current_price = self.price_manager.get_latest_price(symbol)
        if current_price is None:
            return {"has_data": False, "error": "No current price available"}

        history = self._get_price_history_last_hours(symbol, self.config["lookback_hours"])

        if len(history) < 2:
            return {
                "has_data": False,
                "error": f"Insufficient data: only {len(history)} records in the last {self.config['lookback_hours']}h"
            }
        
        # Extrage prețurile din istoric
        prices = [entry["price"] for entry in history]
        
        min_price = min(prices)
        max_price = max(prices)
        
        # Calculează variațiile procentuale
        up_from_min = ((current_price - min_price) / min_price) * 100 if min_price > 0 else 0
        down_from_max = ((current_price - max_price) / max_price) * 100 if max_price > 0 else 0
        
        return {
            "has_data": True,
            "current_price": current_price,
            "min_price": min_price,
            "max_price": max_price,
            "up_from_min": up_from_min,
            "down_from_max": down_from_max,
            "history_count": len(history),
            "oldest_time": history[0]["timestamp_readable"] if history else None,
            "newest_time": history[-1]["timestamp_readable"] if history else None
        }
    
    def _should_send_alert(self, symbol: str, alert_type: str) -> bool:
        """
        Verifică dacă putem trimite o nouă alertă (prevenim spam-ul)
        """
        key = f"{symbol}_{alert_type}"
        last_time = self._last_alert_time.get(key, 0)
        cooldown_seconds = self.config["cooldown_minutes"] * 60
        
        return (time.time() - last_time) >= cooldown_seconds
    
    def _record_alert_sent(self, symbol: str, alert_type: str):
        """Înregistrează că am trimis o alertă"""
        key = f"{symbol}_{alert_type}"
        self._last_alert_time[key] = time.time()
    
    def check_symbol(self, symbol: str) -> List[PriceAlert]:
        """
        Verifică un simbol și returnează lista de alerte (poate fi 0, 1 sau 2)
        """
        alerts = []
        stats = self._calculate_24h_stats(symbol)
        
        if not stats.get("has_data", False):
            print(f"[Checker][{symbol}] {stats.get('error', 'Unknown error')}")
        
        current_price = stats["current_price"]
        up_percent = stats["up_from_min"]
        down_percent = stats["down_from_max"]
        
        # Verifică creșterea (prag sus)
        if up_percent >= self.config["up_percent"]:
            if self._should_send_alert(symbol, "up"):
                alert = PriceAlert(
                    symbol=symbol,
                    alert_type="up",
                    current_price=current_price,
                    reference_price=stats["min_price"],
                    percent_change=up_percent,
                    threshold=self.config["up_percent"]
                )
                alerts.append(alert)
                self._record_alert_sent(symbol, "up")
        
        # Verifică scăderea (prag jos)
        if down_percent <= -self.config["down_percent"]:
            if self._should_send_alert(symbol, "down"):
                alert = PriceAlert(
                    symbol=symbol,
                    alert_type="down",
                    current_price=current_price,
                    reference_price=stats["max_price"],
                    percent_change=down_percent,
                    threshold=self.config["down_percent"]
                )
                alerts.append(alert)
                self._record_alert_sent(symbol, "down")
        
        print(f"[Checker][{symbol}] Price: ${current_price:.4f} | "
                  f"↑ {up_percent:+.2f}% (threshold +{self.config['up_percent']}%) | "
                  f"↓ {down_percent:+.2f}% (threshold -{self.config['down_percent']}%) | "
                  f"Min: ${stats['min_price']:.4f} | Max: ${stats['max_price']:.4f}")
        
        return alerts
    
    def check_all_symbols(self) -> List[PriceAlert]:
        """
        Verifică toate simbolurile din watchlist
        """
        all_alerts = []
        if hasattr(self.price_manager, 'original_symbols'):
            symbols = list(self.price_manager.original_symbols)
        else:
            symbols = list(self.price_manager.symbols)

        for symbol in symbols:
            try:
                alerts = self.check_symbol(symbol)
                all_alerts.extend(alerts)
            except Exception as e:
                print(f"[Analyzer][{symbol}] Error: {e}")

        return all_alerts
    
    def start_monitoring(self, interval_seconds: int = 60):
        """
        Pornește monitorizarea continuă într-un thread separat.
        
        Args:
            interval_seconds: Cât de des să verifice (ex: 60 secunde)
        """
        if self._running:
            print("[Checker] Already running!")
            return

        self._running = True

        def run():
            print(f"[Checker] Monitoring started - checking every {interval_seconds}s")
            print(f"[Checker] Thresholds: ↑ +{self.config['up_percent']}% | ↓ -{self.config['down_percent']}%")

            while self._running:
                try:
                    alerts = self.check_all_symbols()

                    if alerts:
                        self.alert_callback(alerts)

                except Exception as e:
                    print(f"[Checker] Error in main loop: {e}")

                print(f"[Checker] Waiting {interval_seconds} seconds until next check...")
                for _ in range(interval_seconds):
                    if not self._running:
                        break
                    time.sleep(1)
        
        self._thread = threading.Thread(target=run, name="PriceChecker", daemon=True)
        self._thread.start()
    
    def stop_monitoring(self):
        """Stop monitoring."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        print("[Checker] Monitoring stopped")
    
    def get_status(self) -> dict:
        """Returnează statusul curent al analizorului"""
        return {
            "running": self._running,
            "config": self.config,
            "symbols_count": len(self.price_manager.original_symbols if hasattr(self.price_manager, 'original_symbols') else self.price_manager.symbols),
            "last_alerts": dict(self._last_alert_time)
        }


# ============================================
# Funcție de conveniență pentru integrare rapidă
# ============================================

def start_price_alert_system(price_monitor, alert_callback=None, check_interval_seconds=60):
    """
    Pornește sistemul complet de alertă.
    
    Args:
        price_monitor: Instanța EnhancedCachePriceManager
        alert_callback: Funcție apelată la alertă (opțional)
        check_interval_seconds: Intervalul dintre verificări
        
    Returns:
        Instanța PriceChecker
    """
    Checker = PriceChecker(price_monitor, alert_callback=alert_callback)
    Checker.start_monitoring(check_interval_seconds)
    return Checker
