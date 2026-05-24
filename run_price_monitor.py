# run_price_monitor.py
import time
import os
import threading
from datetime import datetime
from pathlib import Path

# Importă modulele tale existente
import log

# Importă modulele principale
from pricefetcher import create_price_monitor
from pricechecker import start_price_alert_system, PRICE_ALERT_CONFIG
from new_coins_discovery import create_new_coins_monitor, NewCoinsMonitor, NewCoinsFactory, MAX_NEW_COINS_TO_TRACK


def load_env_file(filename=".env"):
    env_path = Path(__file__).resolve().parent / filename
    if not env_path.exists():
        return
    try:
        with env_path.open("r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        print(f"[Warning] Nu pot încărca {env_path}: {e}")


load_env_file()

# Încearcă să importe AlertNotifier, dar nu e critic
try:
    from alertnotifiers import AlertNotifier
    ALERT_NOTIFIER_AVAILABLE = True
except ImportError:
    print("[Warning] AlertNotifier not available, using basic alerts")
    ALERT_NOTIFIER_AVAILABLE = False
    
    # Clasa dummy dacă nu există
    class AlertNotifier:
        @staticmethod
        def print_to_console(alert):
            print(str(alert))
        
        @staticmethod
        def save_to_file(alert, filename="crypto_alerts.log"):
            with open(filename, "a") as f:
                f.write(f"{datetime.now()}: {alert.symbol} - {alert.percent_change:+.2f}%\n")


def custom_alert_handler(alert):
    """Handler personalizat pentru alerte de preț"""
    if ALERT_NOTIFIER_AVAILABLE:
        AlertNotifier.print_to_console(alert)
        AlertNotifier.save_to_file(alert, filename="crypto_alerts.log")
        if os.environ.get("PHONE_ALERT_URL") or os.environ.get("NTFY_TOPIC"):
            AlertNotifier.send_phone_webhook(alert)
        if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
            AlertNotifier.send_telegram(alert)
        if os.environ.get("SMTP_USERNAME") and os.environ.get("SMTP_PASSWORD"):
            AlertNotifier.send_email(alert)
    else:
        print("\n" + "=" * 60)
        print(str(alert))
        print("=" * 60)


def new_coin_alert_handler(coin_info):
    """Handler pentru monede nou descoperite"""
    source = coin_info.get('source', 'unknown')
    has_price = coin_info.get('has_price', False)
    auto_added = coin_info.get('auto_added', False)
    
    print("\n" + "=" * 70)
    print(f"🆕 MONEDĂ NOUĂ: {coin_info['symbol']} - {coin_info.get('name', 'N/A')}")
    print(f"   📡 Sursă: {source}")
    
    if has_price:
        print(f"   💰 Preț: ${coin_info.get('price', 0):.8f}")
        print(f"   ✅ Adăugată automat în watchlist: {auto_added}")
    else:
        print(f"   ⚠️ Sursa {source} nu oferă preț - doar informațional")
        print(f"   💡 Moneda va fi monitorizată doar dacă apare ulterior pe CoinMarketCap")
    
    if coin_info.get('url'):
        print(f"   🔗 {coin_info['url']}")
    print("=" * 70)


def print_status_report(price_monitor, new_coins_monitor):
    """Afișează un raport de status"""
    print("\n" + "=" * 70)
    print("📊 RAPORT STATUS")
    print("=" * 70)
    
    # Simboluri monitorizate pentru prețuri
    if hasattr(price_monitor, 'original_symbols'):
        symbols_count = len(price_monitor.original_symbols)
        print(f"\n💰 Simboluri monitorizate prețuri: {symbols_count}")
        print(f"   Primele 10: {price_monitor.original_symbols[:10]}")
    
    # Monede noi descoperite
    if new_coins_monitor:
        summary = new_coins_monitor.get_summary()
        print(f"\n🆕 Monede noi descoperite total: {summary['total_new_coins']}")
        for source, data in summary['sources'].items():
            print(f"   {source}: {data['count']} monede")
        if summary['all_symbols']:
            print(f"   Simboluri noi: {summary['all_symbols'][:10]}")
    
    # Configurație alerte
    print(f"\n⚙️ Configurație alerte preț:")
    print(f"   Prag creștere: +{PRICE_ALERT_CONFIG['up_percent']}% față de minim 24h")
    print(f"   Prag scădere: -{PRICE_ALERT_CONFIG['down_percent']}% față de maxim 24h")
    print(f"   Cooldown: {PRICE_ALERT_CONFIG['cooldown_minutes']} minute")
    
    print("=" * 70)


def periodic_cleanup(price_monitor, new_coins_monitor):
    """Rulează cleanup la fiecare 6 ore"""
    while True:
        time.sleep(6 * 3600)  # 6 ore
        print("[Periodic] Rulez cleanup prețuri vechi...")
        
        # Curăță prețurile vechi
        if hasattr(price_monitor, 'cleanup_old_prices'):
            price_monitor.cleanup_old_prices()
        else:
            print("[Periodic] price_monitor.cleanup_old_prices() nu există")
        
        # Curăță simbolurile vechi
        if hasattr(price_monitor, 'cleanup_old_symbols'):
            price_monitor.cleanup_old_symbols(max_age_days=7)
        else:
            print("[Periodic] price_monitor.cleanup_old_symbols() nu există")
        
        # Curăță monedele vechi din watchlist
        if new_coins_monitor and hasattr(new_coins_monitor, 'cleanup_old_new_coins'):
            new_coins_monitor.cleanup_old_new_coins()
        else:
            print("[Periodic] new_coins_monitor.cleanup_old_new_coins() nu există")


def main():
    """Funcția principală"""
    
    print("=" * 70)
    print("🚀 SISTEM COMPLET MONITORIZARE CRYPTO")
    print("=" * 70)
    print(f"📅 Pornire: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Configurație
    CMC_API_KEY = os.environ.get('CMC_API_KEY', "4d587781-722b-40a3-83f0-2436d45942f7")
    
    # Surse pentru monede noi (poți alege care să fie active)
    ENABLED_SOURCES = ["coinmarketcap", "coingecko", "binance", "dexscreener"]
    
    # =========================================================
    # PASUL 1: Pornește monitorul de prețuri
    # =========================================================
    print("\n⏳ Inițializare monitor prețuri...")
    price_monitor = create_price_monitor(cmc_api_key=CMC_API_KEY)
    print("✅ Monitor prețuri pornit!")
    
    # =========================================================
    # PASUL 2: Așteaptă prima colectare
    # =========================================================
    print("\n⏳ Așteptăm prima colectare de prețuri (30 sec)...")
    time.sleep(30)
    
    # =========================================================
    # PASUL 3: Pornește sistemul de alerte pentru prețuri
    # =========================================================
    print("\n⏳ Pornire sistem alertă prețuri...")
    analyzer = start_price_alert_system(
        price_monitor=price_monitor,
        alert_callback=custom_alert_handler,
        check_interval_seconds=60
    )
    print("✅ Sistem alertă prețuri pornit!")
    
    # =========================================================
    # PASUL 4: Pornește monitorul pentru monede noi
    # =========================================================
    print("\n⏳ Inițializare monitor monede noi...")
    
    # Creează factory-ul cu sursele dorite
    factory = NewCoinsFactory(enabled_sources=ENABLED_SOURCES, cmc_api_key=CMC_API_KEY)
    
    # Creează monitorul și conectează-l la price_monitor
    new_coins_monitor = NewCoinsMonitor(price_monitor=price_monitor, factory=factory)
    new_coins_monitor.register_alert_callback(new_coin_alert_handler)
    
    # Pornește monitorizarea (refresh la fiecare oră)
    new_coins_monitor.start_monitoring(interval_seconds=3600)
    print(f"✅ Monitor monede noi pornit! Surse active: {factory.get_available_sources()}")
    
    print("\n" + "=" * 70)
    print("📋 CONFIGURAȚIE MONITOR MONEDE NOI")
    print("=" * 70)
    print(f"   ✅ CoinMarketCap - descoperă + preț → adaugă în watchlist")
    print(f"   ℹ️ CoinGecko - doar descoperă (fără preț) → informațional")
    print(f"   ℹ️ Binance - doar listări noi → informațional")
    print(f"   ℹ️ DexScreener - doar token-uri noi DEX → informațional")
    print("=" * 70)

    # =========================================================
    # PASUL 5: Descoperire inițială monede noi
    # =========================================================
    # Secțiunea de startup - versiunea corectată
    print("\n⏳ Descoperire inițială monede noi...")
    new_coins_monitor.refresh()

    # Adaugă doar monedele de pe CoinMarketCap (singurele cu preț)
    auto_added_count = 0
    for source_name, coins in new_coins_monitor.all_new_coins.items():
        if source_name.lower() == "coinmarketcap":  # ← ignora litere mari/mici
            for coin in coins[:MAX_NEW_COINS_TO_TRACK]:
                if new_coins_monitor.add_new_coin_to_watchlist(coin):
                    auto_added_count += 1
        else:
            # Celelalte surse - doar loghează (DAR COMPRESAT)
            if coins:
                symbols_list = ', '.join([c['symbol'] for c in coins[:10]])
                if len(coins) > 10:
                    symbols_list += f" și {len(coins)-10} altele"
                print(f"[Startup] ℹ️ Sursa {source_name}: {len(coins)} monede noi: {symbols_list}")

    if auto_added_count > 0:
        print(f"✅ {auto_added_count} monede noi adăugate automat în watchlist (de pe CoinMarketCap)")
    else:
        print(f"ℹ️ Nu s-au găsit monede noi cu preț pe CoinMarketCap")
    
    # =========================================================
    # PASUL 6: Pornește cleanup-ul periodic
    # =========================================================
    cleanup_thread = threading.Thread(
        target=periodic_cleanup, 
        args=(price_monitor, new_coins_monitor),
        daemon=True
    )
    cleanup_thread.start()
    print("✅ Cleanup periodic pornit (la 6 ore)")

    # =========================================================
    # PASUL 7: Afișează raportul de status
    # =========================================================
    print_status_report(price_monitor, new_coins_monitor)
    
    # =========================================================
    # PASUL 8: Afișează raportul detaliat cu monede noi
    # =========================================================
    print(new_coins_monitor.get_report())
    
    print("\n" + "=" * 70)
    print("✅ SISTEM COMPLET ACTIVAT!")
    print("   📊 Monedele noi sunt adăugate AUTOMAT în watchlist")
    print("   📈 PriceChecker le va monitoriza prețurile")
    print("   🔔 Vei primi alerte când ating pragurile")
    print("=" * 70)
    print("\n👉 Așteaptă alerte... (Ctrl+C pentru oprire)\n")
    
    # Menține programul în funcțiune
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 Oprire sistem...")
        new_coins_monitor.stop_monitoring()
        analyzer.stop_monitoring()
        print("👋 La revedere!")


if __name__ == "__main__":
    main()
