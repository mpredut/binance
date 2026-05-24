# run_price_monitor.py
import time
import os
from datetime import datetime

# Importă modulele tale existente și noile module
import log
import utils as u

# Importă noile module
from price_fetcher_managers import create_price_monitor
from price_analyzer import start_price_alert_system, PRICE_ALERT_CONFIG
from alertnotifiers import AlertNotifier


def custom_alert_handler(alert):
    """
    Handler personalizat pentru alerte.
    Poți modifica cum vrei să fie notificat.
    """
    # Afișează în consolă
    AlertNotifier.print_to_console(alert)
    
    # Salvează în fișier
    AlertNotifier.save_to_file(alert, filename="crypto_alerts.log")
    
    # Dacă vrei Telegram, decomentează și setează variabilele de mediu:
    # AlertNotifier.send_telegram(alert)
    
    # Poți adăuga și alte acțiuni aici:
    # - Redă un sunet pe speaker
    # - Trimite un email
    # - Postează pe un webhook Discord


def print_banner():
    """Afișează un banner la pornire"""
    print("=" * 70)
    print("  🚀 SISTEM MONITORIZARE PRECURI CRYPTO 🚀")
    print("=" * 70)
    print(f"  📅 Data pornire: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  📊 Prag creștere: +{PRICE_ALERT_CONFIG['up_percent']}% față de minim 24h")
    print(f"  📊 Prag scădere: -{PRICE_ALERT_CONFIG['down_percent']}% față de maxim 24h")
    print(f"  ⏱️  Interval salvare preț: 5 minute")
    print(f"  🔍 Interval verificare alerte: 60 secunde")
    print(f"  🛡️  Cooldown alerte: {PRICE_ALERT_CONFIG['cooldown_minutes']} minute")
    print("=" * 70)


def main():
    """Funcția principală"""
    
    # Configurație - modifică după nevoi
    WATCHLIST = [
        "BTC",      # Bitcoin
        "ETH",      # Ethereum
        "HYPE",     # Hyperliquid
        "SOL",      # Solana
        "BNB",      # Binance Coin
        "ADA",      # Cardano
        "DOGE",     # Dogecoin
    ]
    
    # Cheia API CoinMarketCap (opțională - fără ea folosește doar Binance și Hyperliquid)
    CMC_API_KEY = os.environ.get('CMC_API_KEY', None)
    # Dacă ai cheie, seteaz-o așa:
    # CMC_API_KEY = "cheia_ta_aici"
    
    print_banner()
    
    # Pasul 1: Pornește monitorul de prețuri (salvează la 5 minute)
    print("\n⏳ Inițializare monitor prețuri...")
    price_monitor = create_price_monitor(
        symbols=WATCHLIST,
        cmc_api_key=CMC_API_KEY
    )
    print("✅ Monitor prețuri pornit!")
    
    # Așteaptă puțin pentru a colecta primele prețuri
    print("\n⏳ Așteptăm colectarea primelor prețuri (30 secunde)...")
    time.sleep(30)
    
    # Pasul 2: Pornește sistemul de analiză și alertă
    print("\n⏳ Pornire sistem analiză prețuri...")
    analyzer = start_price_alert_system(
        price_monitor=price_monitor,
        alert_callback=custom_alert_handler,
        check_interval_seconds=60  # Verifică la fiecare 60 secunde
    )
    print("✅ Sistem analiză pornit!")
    
    # Afișează statusul curent al configurării
    print("\n📋 Configurație activă:")
    status = analyzer.get_status()
    print(f"   - Simboluri monitorizate: {status['symbols_count']}")
    print(f"   - Prag creștere: +{status['config']['up_percent']}%")
    print(f"   - Prag scădere: -{status['config']['down_percent']}%")
    print(f"   - Cooldown: {status['config']['cooldown_minutes']} minute")
    
    print("\n✅ SISTEM COMPLET ACTIVAT!")
    print("👉 Așteaptă alerte... (Ctrl+C pentru oprire)\n")
    
    # Menține programul în funcțiune
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\n🛑 Oprire sistem...")
        analyzer.stop_monitoring()
        print("👋 La revedere!")


if __name__ == "__main__":
    main()