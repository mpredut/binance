# run_price_monitor.py
import time
import os
from datetime import datetime

# Importă modulele tale existente și noile module
import log
import utils as u

# Importă noile module
from pricefetcher import create_price_monitor
from pricechecker import start_price_alert_system, PRICE_ALERT_CONFIG
from alertnotifiers import AlertNotifier
# run_price_monitor.py (versiune completă cu auto-add)
import time
import os
from datetime import datetime

from new_coins_discovery import create_new_coins_monitor, NewCoinsMonitor, NewCoinsFactory


def custom_alert_handler(alert):
    """Handler personalizat pentru alerte de preț"""
    AlertNotifier.print_to_console(alert)
    AlertNotifier.save_to_file(alert, filename="crypto_alerts.log")


def new_coin_alert_handler(coin_info):
    """Handler pentru monede nou descoperite"""
    print("\n" + "=" * 70)
    print(f"🆕 MONEDĂ NOUĂ: {coin_info['symbol']} - {coin_info.get('name', 'N/A')}")
    print(f"   📡 Sursă: {coin_info.get('source', 'unknown')}")
    print(f"   💰 Preț: ${coin_info.get('price', 0):.8f}")
    print(f"   ✅ Adăugată automat în watchlist: {coin_info.get('auto_added', False)}")
    if coin_info.get('url'):
        print(f"   🔗 {coin_info['url']}")
    print("=" * 70)


def print_status_report(price_monitor, new_coins_monitor, analyzer):
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
    # PASUL 1: Pornește monitorul de prețuri pentru simbolurile existente
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
    # PASUL 4: Pornește monitorul pentru monede noi (cu auto-add)
    # =========================================================
    print("\n⏳ Inițializare monitor monede noi...")
    
    # Creează factory-ul cu sursele dorite
    from new_coins_discovery import NewCoinsFactory, NewCoinsMonitor
    factory = NewCoinsFactory(enabled_sources=ENABLED_SOURCES, cmc_api_key=CMC_API_KEY)
    
    # Creează monitorul și conectează-l la price_monitor
    new_coins_monitor = NewCoinsMonitor(price_monitor=price_monitor, factory=factory)
    new_coins_monitor.register_alert_callback(new_coin_alert_handler)
    
    # Pornește monitorizarea (refresh la fiecare oră)
    new_coins_monitor.start_monitoring(interval_seconds=3600)
    print(f"✅ Monitor monede noi pornit! Surse active: {factory.get_available_sources()}")
    
    # =========================================================
    # PASUL 5: Forțează o descoperire inițială și adaugă monedele noi
    # =========================================================
    print("\n⏳ Descoperire inițială monede noi...")
    new_coins_monitor.refresh()
    
    # Adaugă automat monedele noi în watchlist
    auto_added_count = 0
    for source_name, coins in new_coins_monitor.all_new_coins.items():
        for coin in coins:
            if new_coins_monitor.add_new_coin_to_watchlist(coin):
                auto_added_count += 1
    
    if auto_added_count > 0:
        print(f"✅ {auto_added_count} monede noi adăugate automat în watchlist!")
    

    def periodic_cleanup():
        """Rulează cleanup la fiecare 6 ore"""
        while True:
            time.sleep(6 * 3600)  # 6 ore
            print("[Periodic] Rulez cleanup prețuri vechi...")
            price_monitor.cleanup_old_prices()
            price_monitor.cleanup_old_symbols(max_age_days=7)
            7
            if new_coins_monitor:
                new_coins_monitor.cleanup_old_new_coins()

    # Pornește cleanup-ul într-un thread separat
    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()

    # =========================================================
    # PASUL 6: Afișează raportul de status
    # =========================================================
    print_status_report(price_monitor, new_coins_monitor, analyzer)
    
    # =========================================================
    # PASUL 7: Afișează raportul detaliat cu monede noi
    # =========================================================
    print(new_coins_monitor.get_report())
    
    print("\n" + "=" * 70)
    print("✅ SISTEM COMPLET ACTIVAT!")
    print("   📊 Monedele noi sunt adăugate AUTOMAT în watchlist")
    print("   📈 PriceAnalyzer le va monitoriza prețurile")
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