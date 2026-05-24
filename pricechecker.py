# pricechecker.py
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Callable
from collections import defaultdict

# Import your existing modules
import log
import utils as u

# Threshold configuration (can be adjusted at any time)
PRICE_ALERT_CONFIG = {
    "up_percent": 5.1,      # Trigger an alert when the price rises by 5% from the 24h low
    "down_percent": 7.5,    # Trigger an alert when the price drops by 7.5% from the 24h high
    "lookback_hours": 24,   # Analysis interval (24 hours)
    "cooldown_minutes": 15,  # Do not send the same alert more often than every 15 minutes
}


class PriceAlert:
    """Structure for a price alert."""

    def __init__(self, symbol: str, alert_type: str, current_price: float,
                 reference_price: float, percent_change: float, threshold: float):
        self.symbol = symbol
        self.alert_type = alert_type  # "up" or "down"
        self.current_price = current_price
        self.reference_price = reference_price
        self.percent_change = percent_change
        self.threshold = threshold
        self.timestamp = time.time()

    def __str__(self) -> str:
        direction = "🚀 RISE" if self.alert_type == "up" else "📉 DROP"
        emoji = "🟢" if self.alert_type == "up" else "🔴"
        return (
            f"\n{emoji} {direction} {emoji}\n"
            f"📊 {self.symbol}\n"
            f"💰 Current price: ${self.current_price:.4f}\n"
            f"📈 Reference: ${self.reference_price:.4f} ({'24h low' if self.alert_type == 'up' else '24h high'})\n"
            f"📊 Change: {self.percent_change:+.2f}% (threshold: {self.threshold}%)\n"
            f"⏰ Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

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
    Analyze cached prices and generate alerts when thresholds are exceeded.
    Runs in a separate thread.
    """

    def __init__(self, price_manager, alert_callback: Optional[Callable] = None):
        """
        Args:
            price_manager: The EnhancedCachePriceManager instance.
            alert_callback: Function called when an alert is generated (for example print, email, or telegram delivery).
        """
        self.price_manager = price_manager
        self.alert_callback = alert_callback or self._default_alert_handler
        self.config = PRICE_ALERT_CONFIG.copy()

        # Prevent spam: remember the last alert per symbol and alert type
        self._last_alert_time = defaultdict(float)

        # Thread for continuous analysis
        self._thread = None
        self._running = False

    def _default_alert_handler(self, alert: PriceAlert):
        """Default handler that prints the alert to the console."""
        print("\n" + "=" * 60)
        print(str(alert))
        print("=" * 60)

    def _get_price_history_last_hours(self, symbol: str, hours: int) -> List[Dict]:
        """Retrieve the price history from the last 'hours' hours."""
        history = self.price_manager.get_price_history(symbol, limit=1000)

        if not history:
            return []

        cutoff_time = (time.time() - hours * 3600) * 1000  # milliseconds

        # Filter only entries from the last X hours
        recent_history = [
            entry for entry in history
            if entry["timestamp"] >= cutoff_time
        ]

        return recent_history

    def _calculate_24h_stats(self, symbol: str) -> Dict:
        """
        Calculate the minimum, maximum, and changes for the last 24 hours.

        Returns:
            Dict with:
            - min_price: minimum price in the last 24h
            - max_price: maximum price in the last 24h
            - current_price: current price
            - up_from_min: percentage increase from the minimum
            - down_from_max: percentage decrease from the maximum
            - has_data: True if enough data exists
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

        # Extract prices from history
        prices = [entry["price"] for entry in history]

        min_price = min(prices)
        max_price = max(prices)

        # Calculate percentage changes
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
        """Check whether we can send a new alert (spam prevention)."""
        key = f"{symbol}_{alert_type}"
        last_time = self._last_alert_time.get(key, 0)
        cooldown_seconds = self.config["cooldown_minutes"] * 60

        return (time.time() - last_time) >= cooldown_seconds

    def _record_alert_sent(self, symbol: str, alert_type: str):
        """Record that an alert was sent."""
        key = f"{symbol}_{alert_type}"
        self._last_alert_time[key] = time.time()

    def check_symbol(self, symbol: str) -> List[PriceAlert]:
        """Check a symbol and return a list of alerts (0, 1, or 2)."""
        alerts = []
        stats = self._calculate_24h_stats(symbol)

        if not stats.get("has_data", False):
            print(f"[Checker][{symbol}] {stats.get('error', 'Unknown error')}")
            return alerts

        current_price = stats["current_price"]
        up_percent = stats["up_from_min"]
        down_percent = stats["down_from_max"]

        # Check for price increase (upper threshold)
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

        # Check for price decrease (lower threshold)
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

        print(
            f"[Checker][{symbol}] Price: ${current_price:.4f} | "
            f"↑ {up_percent:+.2f}% (threshold +{self.config['up_percent']}%) | "
            f"↓ {down_percent:+.2f}% (threshold -{self.config['down_percent']}%) | "
            f"Min: ${stats['min_price']:.4f} | Max: ${stats['max_price']:.4f}"
        )

        return alerts

    def check_all_symbols(self) -> List[PriceAlert]:
        """Check all symbols in the watchlist."""
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
                print(f"[Checker][{symbol}] Error: {e}")

        return all_alerts

    def start_monitoring(self, interval_seconds: int = 60):
        """
        Start continuous monitoring in a separate thread.

        Args:
            interval_seconds: How often to check (for example 60 seconds).
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
        """Return the current analyzer status."""
        return {
            "running": self._running,
            "config": self.config,
            "symbols_count": len(self.price_manager.original_symbols if hasattr(self.price_manager, 'original_symbols') else self.price_manager.symbols),
            "last_alerts": dict(self._last_alert_time)
        }


# ============================================
# Convenience function for quick integration
# ============================================


def start_price_alert_system(price_monitor, alert_callback=None, check_interval_seconds=60):
    """
    Start the complete price alert system.

    Args:
        price_monitor: The EnhancedCachePriceManager instance.
        alert_callback: Function called when an alert is generated (optional).
        check_interval_seconds: Interval between checks.

    Returns:
        The PriceChecker instance.
    """
    Checker = PriceChecker(price_monitor, alert_callback=alert_callback)
    Checker.start_monitoring(check_interval_seconds)
    return Checker
