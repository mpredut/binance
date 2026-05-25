# pricechecker.py
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Callable
from collections import defaultdict
from urllib.parse import quote

# Import your existing modules
import log
import utils as u
from pricefetcher import get_base_symbol

# Threshold configuration (can be adjusted at any time)
PRICE_ALERT_CONFIG = {
    "default": {
        "up_percent": 4.1,    # Trigger an alert when the price rises by 5% from the 24h low
        "down_percent": 7.5,  # Trigger an alert when the price drops by 7.5% from the 24h high
    },
    "dynamic": {
        "up_percent": 12.0,    # Stricter threshold for dynamically added coins
        "down_percent": 25.0,  # Stricter threshold for dynamically added coins
    },
    "lookback_hours": 24,    # Analysis interval (24 hours)
    "cooldown_minutes": 15,  # Do not send the same alert more often than every 15 minutes
}


class PriceAlert:
    """Structure for a price alert."""

    def __init__(self, symbol: str, alert_type: str, current_price: float,
                 reference_price: float, percent_change: float, threshold: float,
                 url: Optional[str] = None, reference_time: Optional[str] = None):
        self.symbol = symbol
        self.alert_type = alert_type  # "up" or "down"
        self.current_price = current_price
        self.reference_price = reference_price
        self.percent_change = percent_change
        self.threshold = threshold
        self.timestamp = time.time()
        self.url = url or ""
        self.reference_time = reference_time

    def __str__(self) -> str:
        direction = "🚀 RISE" if self.alert_type == "up" else "📉 DROP"
        emoji = "🟢" if self.alert_type == "up" else "🔴"
        reference_time = self.reference_time or datetime.fromtimestamp(self.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        url_line = f"🔗 CoinMarketCap: {self.url}\n" if self.url else ""
        return (
            f"\n{emoji} {direction} {emoji}\n"
            f"📊 Coin: {self.symbol}\n"
            f"💰 Current price: ${self.current_price:.4f}\n"
            f"📈 Reference: ${self.reference_price:.4f} ({'24h low' if self.alert_type == 'up' else '24h high'}, at {reference_time})\n"
            f"{url_line}"
            f"📊 Change: {self.percent_change:+.2f}% (threshold: {self.threshold}%)"
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
            "timestamp_readable": datetime.now().isoformat(),
            "reference_time": self.reference_time,
            "url": self.url,
        }


class PriceChecker:
    """
    Analyze cached prices and generate alerts when thresholds are exceeded.
    Runs in a separate thread.
    """

    def __init__(self, cachePriceAll, alert_callback: Optional[Callable] = None):
        """
        Args:
            cachePriceAll: The EnhancedCachePriceManager instance.
            alert_callback: Function called when an alert is generated (for example print, email, or telegram delivery).
        """
        self.cachePriceAll = cachePriceAll
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

    def _build_cmc_url(self, symbol: str) -> str:
        try:
            candidate_symbols = [symbol, get_base_symbol(symbol)]
            for candidate in candidate_symbols:
                if not candidate:
                    continue
                for platform in getattr(getattr(self.cachePriceAll, "price_factory", None), "_platforms", []):
                    if getattr(platform, "platform_name", "") != "CoinMarketCap":
                        continue
                    listings = getattr(platform, "_all_listings", {})
                    metadata = listings.get(candidate) or listings.get(candidate.upper())
                    if metadata and metadata.get("slug"):
                        return f"https://coinmarketcap.com/currencies/{metadata['slug']}/"
            return f"https://coinmarketcap.com/search/?q={quote(symbol)}"
        except Exception:
            return f"https://coinmarketcap.com/search/?q={quote(symbol)}"

    def _get_price_history_last_hours(self, symbol: str, hours: int) -> List[Dict]:
        """Retrieve the price history from the last 'hours' hours."""
        history = self.cachePriceAll.get_price_history(symbol, limit=1000)

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
        current_price = self.cachePriceAll.get_latest_price(symbol)
        if current_price is None:
            return {"has_data": False, "error": "No current price available"}

        history = self._get_price_history_last_hours(symbol, self.config["lookback_hours"])

        if len(history) < 2:
            return {
                "has_data": False,
                "error": f"Insufficient data: only {len(history)} records in the last {self.config['lookback_hours']}h"
            }

        # Extract prices from history
        min_entry = min(history, key=lambda entry: entry["price"])
        max_entry = max(history, key=lambda entry: entry["price"])

        min_price = min_entry["price"]
        max_price = max_entry["price"]

        # Calculate percentage changes
        up_from_min = ((current_price - min_price) / min_price) * 100 if min_price > 0 else 0
        down_from_max = ((current_price - max_price) / max_price) * 100 if max_price > 0 else 0

        return {
            "has_data": True,
            "current_price": current_price,
            "min_price": min_price,
            "min_price_timestamp_readable": min_entry.get("timestamp_readable"),
            "max_price": max_price,
            "max_price_timestamp_readable": max_entry.get("timestamp_readable"),
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

    def _is_dynamic_symbol(self, symbol: str) -> bool:
        symbol_added_time = getattr(self.cachePriceAll, "symbol_added_time", {})
        return bool(symbol_added_time.get(symbol))

    def _get_thresholds_for_symbol(self, symbol: str) -> Dict:
        if self._is_dynamic_symbol(symbol):
            return self.config["dynamic"]
        return self.config["default"]

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
        thresholds = self._get_thresholds_for_symbol(symbol)
        up_threshold = thresholds["up_percent"]
        down_threshold = thresholds["down_percent"]

        # Check for price increase (upper threshold)
        if up_percent >= up_threshold:
            if self._should_send_alert(symbol, "up"):
                alert = PriceAlert(
                    symbol=symbol,
                    alert_type="up",
                    current_price=current_price,
                    reference_price=stats["min_price"],
                    reference_time=stats.get("min_price_timestamp_readable"),
                    percent_change=up_percent,
                    threshold=up_threshold,
                    url=self._build_cmc_url(symbol)
                )
                alerts.append(alert)
                self._record_alert_sent(symbol, "up")

        # Check for price decrease (lower threshold)
        if down_percent <= -down_threshold:
            if self._should_send_alert(symbol, "down"):
                alert = PriceAlert(
                    symbol=symbol,
                    alert_type="down",
                    current_price=current_price,
                    reference_price=stats["max_price"],
                    reference_time=stats.get("max_price_timestamp_readable"),
                    percent_change=down_percent,
                    threshold=down_threshold,
                    url=self._build_cmc_url(symbol)
                )
                alerts.append(alert)
                self._record_alert_sent(symbol, "down")

        print(
            f"[Checker][{symbol}] Price: ${current_price:.4f} | "
            f"↑ {up_percent:+.2f}% (threshold +{up_threshold}%) | "
            f"↓ {down_percent:+.2f}% (threshold -{down_threshold}%) | "
            f"Min: ${stats['min_price']:.4f} | Max: ${stats['max_price']:.4f}"
        )

        return alerts

    def check_all_symbols(self) -> List[PriceAlert]:
        """Check all symbols in the watchlist."""
        all_alerts = []
        if hasattr(self.cachePriceAll, 'original_symbols'):
            symbols = list(self.cachePriceAll.original_symbols)
        else:
            symbols = list(self.cachePriceAll.symbols)

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
            print(
                f"[Checker] Thresholds: default ↑ +{self.config['default']['up_percent']}% | ↓ -{self.config['default']['down_percent']}% | "
                f"dynamic ↑ +{self.config['dynamic']['up_percent']}% | ↓ -{self.config['dynamic']['down_percent']}%"
            )

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
            "symbols_count": len(self.cachePriceAll.original_symbols if hasattr(self.cachePriceAll, 'original_symbols') else self.cachePriceAll.symbols),
            "last_alerts": dict(self._last_alert_time)
        }


# ============================================
# Convenience function for quick integration
# ============================================


def start_price_alert_system(cachePriceAll, alert_callback=None, check_interval_seconds=60):
    """
    Start the complete price alert system.

    Args:
        cachePriceAll: The EnhancedCachePriceManager instance.
        alert_callback: Function called when an alert is generated (optional).
        check_interval_seconds: Interval between checks.

    Returns:
        The PriceChecker instance.
    """
    Checker = PriceChecker(cachePriceAll, alert_callback=alert_callback)
    Checker.start_monitoring(check_interval_seconds)
    return Checker
