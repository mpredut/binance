# run_cachePriceAll.py
import time
import os
import threading
from datetime import datetime
from pathlib import Path

# Importă modulele tale existente
import log

# Importă modulele principale
from pricefetcher import create_cachePriceAll
from pricechecker import start_price_alert_system, PRICE_ALERT_CONFIG
from new_coins_discovery import create_new_coins_monitor, NewCoinsMonitor, NewCoinsFactory, MAX_NEW_COINS_TO_TRACK

TIME_INTERVAL_CLEANUP = 6 * 60 # * 60  # 6 hours in seconds
REQUIRED_ENV_VARS = ("CMC_API_KEY", "PHONE_ALERT_URL")


def load_env_file(filename=".env"):
    env_path = Path(__file__).resolve().parent / filename
    if not env_path.exists():
        if any(os.environ.get(key) for key in REQUIRED_ENV_VARS):
            return
        raise FileNotFoundError(
            f"Missing required environment file: {env_path}. Please create .env with required variables."
        )

    try:
        with env_path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and (key not in os.environ or not os.environ[key].strip()):
                    os.environ[key] = value
    except Exception as e:
        raise RuntimeError(f"Unable to load environment file {env_path}: {e}") from e


def validate_required_env():
    missing = []
    for key in REQUIRED_ENV_VARS:
        if not os.environ.get(key):
            missing.append(key)

    if not os.environ.get("PHONE_ALERT_URL") and not os.environ.get("NTFY_TOPIC"):
        missing.append("PHONE_ALERT_URL or NTFY_TOPIC")

    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(sorted(set(missing)))
        )


load_env_file()
validate_required_env()

# Try to import AlertNotifier, but do not continue without it
try:
    from alertnotifiers import AlertNotifier
    ALERT_NOTIFIER_AVAILABLE = True
except ImportError as exc:
    raise RuntimeError("AlertNotifier is required but could not be imported") from exc


def print_notification_channels_status():
    print("\n" + "=" * 70)
    print("NOTIFICATION CHANNEL CONFIGURATION CHECK")
    print("=" * 70)

    phone_url = os.environ.get("PHONE_ALERT_URL")
    ntfy_topic = os.environ.get("NTFY_TOPIC")
    if phone_url or ntfy_topic:
        target = phone_url if phone_url else f"ntfy topic '{ntfy_topic}'"
        print(f"   ✅ Phone webhook: ENABLED -> {target[:40]}...")
    else:
        print("   ❌ Phone webhook: DISABLED (PHONE_ALERT_URL or NTFY_TOPIC is missing)")

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        print(f"   ✅ Telegram: ENABLED -> Chat ID: {tg_chat}")
    else:
        missing = []
        if not tg_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not tg_chat:
            missing.append("TELEGRAM_CHAT_ID")
        print(f"   ❌ Telegram: DISABLED (missing: {', '.join(missing)})")

    email_user = os.environ.get("SMTP_USERNAME")
    email_pass = os.environ.get("SMTP_PASSWORD")
    alert_to_email = os.environ.get("ALERT_TO_EMAIL")
    if email_user and email_pass and alert_to_email:
        print(f"   ✅ Email: ENABLED -> Sender: {email_user}")
    else:
        missing = []
        if not email_user:
            missing.append("SMTP_USERNAME")
        if not email_pass:
            missing.append("SMTP_PASSWORD")
        if not alert_to_email:
            missing.append("ALERT_TO_EMAIL")
        print(f"   ❌ Email: DISABLED (missing: {', '.join(missing)})")

    print("=" * 70 + "\n")


print_notification_channels_status()


def custom_alert_handler(alert):
    """Handler for price alerts."""
    alerts = alert if isinstance(alert, list) else [alert]
    if ALERT_NOTIFIER_AVAILABLE:
        for item in alerts:
            AlertNotifier.print_to_console(item)
            AlertNotifier.save_to_file(item, filename="crypto_alerts.log")

        if os.environ.get("PHONE_ALERT_URL") or os.environ.get("NTFY_TOPIC"):
            AlertNotifier.send_phone_webhook_batch(alerts)
        else:
            print("[Warning] Phone alert was not sent because PHONE_ALERT_URL or NTFY_TOPIC is missing.")

        if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
            for item in alerts:
                AlertNotifier.send_telegram(item)
        else:
            print("[Warning] Telegram alert was not sent because TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing.")

        if os.environ.get("SMTP_USERNAME") and os.environ.get("SMTP_PASSWORD") and os.environ.get("ALERT_TO_EMAIL"):
            AlertNotifier.send_email_batch(alerts)
        else:
            print("[Warning] Email alert was not sent because SMTP_USERNAME, SMTP_PASSWORD, or ALERT_TO_EMAIL is missing.")
    else:
        print("[Warning] AlertNotifier is not available, falling back to console output.")
        for item in alerts:
            print("\n" + "=" * 60)
            print(str(item))
            print("=" * 60)


def new_coin_alert_handler(coin_info):
    """Handler for newly discovered coins."""
    source = coin_info.get('source', 'unknown')
    has_price = coin_info.get('has_price', False)
    auto_added = coin_info.get('auto_added', False)
    added_at = AlertNotifier.format_human_readable_time(coin_info.get('added_at'))

    print("\n" + "=" * 70)
    print(f"🆕 NEW COIN: {coin_info['symbol']} - {coin_info.get('name', 'N/A')}")
    print(f"   📡 Source: {source}")
    print(f"   📅 Added: {added_at}")

    if has_price:
        print(f"   💰 Price: ${coin_info.get('price', 0):.8f}")
        print(f"   ✅ Auto-added to watchlist: {auto_added}")
    else:
        print(f"   ⚠️ Source {source} does not provide price - informational only")
        print(f"   💡 The coin will only be monitored after a price becomes available from CoinMarketCap")

    if coin_info.get('url'):
        print(f"   🔗 {coin_info['url']}")
    print("=" * 70)

    if not ALERT_NOTIFIER_AVAILABLE:
        return

    if os.environ.get("PHONE_ALERT_URL") or os.environ.get("NTFY_TOPIC"):
        AlertNotifier.send_phone_webhook_batch([coin_info])

    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        AlertNotifier.send_telegram(coin_info)

    if os.environ.get("SMTP_USERNAME") and os.environ.get("SMTP_PASSWORD") and os.environ.get("ALERT_TO_EMAIL"):
        AlertNotifier.send_email_batch([coin_info])


def print_status_report(cachePriceAll, new_coins_monitor):
    """Print a status report."""
    print("\n" + "=" * 70)
    print("📊 STATUS REPORT")
    print("=" * 70)

    if hasattr(cachePriceAll, 'original_symbols'):
        symbols_count = len(cachePriceAll.original_symbols)
        print(f"\n💰 Tracked price symbols: {symbols_count}")
        print(f"   First 10: {cachePriceAll.original_symbols[:10]}")

    if new_coins_monitor:
        summary = new_coins_monitor.get_summary()
        print(f"\n🆕 New coins discovered total: {summary['total_new_coins']}")
        for source, data in summary['sources'].items():
            print(f"   {source}: {data['count']} coins")
        if summary['all_symbols']:
            print(f"   New symbols: {summary['all_symbols'][:10]}")

    print(f"\n⚙️ Price alert configuration:")
    print(f"   Default list: up +{PRICE_ALERT_CONFIG['default']['up_percent']}% / down -{PRICE_ALERT_CONFIG['default']['down_percent']}%")
    print(f"   Dynamic list: up +{PRICE_ALERT_CONFIG['dynamic']['up_percent']}% / down -{PRICE_ALERT_CONFIG['dynamic']['down_percent']}%")
    print(f"   Cooldown: {PRICE_ALERT_CONFIG['cooldown_minutes']} minutes")

    print("=" * 70)


def periodic_cleanup(cachePriceAll, new_coins_monitor):
    """Run cleanup every 6 hours."""
    while True:
        print(f"sleeping for {TIME_INTERVAL_CLEANUP} hours before next cleanup...")
        time.sleep(TIME_INTERVAL_CLEANUP )
        print("[Periodic] Running cleanup for stale prices...")

        if hasattr(cachePriceAll, 'cleanup_old_prices'):
            cachePriceAll.cleanup_old_prices()
        else:
            print("[Periodic] cachePriceAll.cleanup_old_prices() does not exist")

        if hasattr(cachePriceAll, 'cleanup_old_symbols'):
            cachePriceAll.cleanup_old_symbols(max_age_days=7)
        else:
            print("[Periodic] cachePriceAll.cleanup_old_symbols() does not exist")

        if new_coins_monitor and hasattr(new_coins_monitor, 'cleanup_old_new_coins'):
            new_coins_monitor.cleanup_old_new_coins()
        else:
            print("[Periodic] new_coins_monitor.cleanup_old_new_coins() does not exist")


def main():
    """Main entry point."""

    print("=" * 70)
    print("🚀 CRYPTO PRICE MONITORING SYSTEM")
    print("=" * 70)
    print(f"📅 Startup time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    CMC_API_KEY = os.environ.get('CMC_API_KEY')
    ENABLED_SOURCES = ["coinmarketcap", "coingecko", "binance", "dexscreener"]

    print("\n⏳ Initializing price monitor...")
    cachePriceAll = create_cachePriceAll(cmc_api_key=CMC_API_KEY)
    print("Price monitor started!")

    print("\n⏳ Waiting for first price sync (30 seconds)...")
    time.sleep(30)

    print("\n⏳ Starting price alert system...")
    analyzer = start_price_alert_system(
        cachePriceAll=cachePriceAll,
        alert_callback=custom_alert_handler,
        check_interval_seconds=60
    )
    print("Price alert system started!")

    print("\n⏳ Initializing new coin monitor...")
    factory = NewCoinsFactory(enabled_sources=ENABLED_SOURCES, cmc_api_key=CMC_API_KEY)
    new_coins_monitor = NewCoinsMonitor(cachePriceAll, factory=factory)
    new_coins_monitor.register_alert_callback(new_coin_alert_handler)
    new_coins_monitor.start_monitoring(interval_seconds=3600)
    print(f"New coin monitor started! Active sources: {factory.get_available_sources()}")

    print("\n" + "=" * 70)
    print("📋 NEW COIN MONITOR CONFIGURATION")
    print("=" * 70)
    print("   ✅ CoinMarketCap - discover + price -> auto-add to watchlist")
    print("   ℹ️ CoinGecko - discovery only -> informational")
    print("   ℹ️ Binance - new listings only -> informational")
    print("   ℹ️ DexScreener - new DEX tokens only -> informational")
    print("=" * 70)

    print("\n⏳ Performing initial new coin discovery...")
    new_coins_monitor.refresh()

    auto_added_count = 0
    for source_name, coins in new_coins_monitor.all_new_coins.items():
        if source_name.lower() == "coinmarketcap":
            for coin in coins[:MAX_NEW_COINS_TO_TRACK]:
                if new_coins_monitor.add_new_coin_to_watchlist(coin):
                    auto_added_count += 1
        else:
            if coins:
                symbols_list = ', '.join([c['symbol'] for c in coins[:10]])
                if len(coins) > 10:
                    symbols_list += f" and {len(coins) - 10} more"
                print(f"[Startup] ℹ️ Source {source_name}: {len(coins)} new coins: {symbols_list}")

    if auto_added_count > 0:
        print(f"✅ {auto_added_count} new coins auto-added to watchlist from CoinMarketCap")
    else:
        print("ℹ️ No new coins with price were found on CoinMarketCap")

    cleanup_thread = threading.Thread(
        target=periodic_cleanup,
        args=(cachePriceAll, new_coins_monitor),
        daemon=True
    )
    cleanup_thread.start()
    print("Periodic cleanup started (every 6 hours)")

    print_status_report(cachePriceAll, new_coins_monitor)
    print(new_coins_monitor.get_report())

    print("\n" + "=" * 70)
    print("SYSTEM FULLY ACTIVE!")
    print("   📊 New coins are auto-added to the watchlist")
    print("   📈 PriceChecker will monitor their price")
    print("   🔔 Alerts will be sent when thresholds are reached")
    print("=" * 70)
    print("\n👉 Waiting for alerts... (Ctrl+C to stop)\n")

    try:
        while True:
            time.sleep(60)
            print_status_report(cachePriceAll, new_coins_monitor)
    except KeyboardInterrupt:
        print("\n\n🛑 Stopping system...")
        new_coins_monitor.stop_monitoring()
        analyzer.stop_monitoring()
        print("👋 Goodbye!")


if __name__ == "__main__":
    main()
