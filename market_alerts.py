#!/usr/bin/env python3
"""
market_alerts.py — orchestrator alerte: monede noi (CoinMarketCap/CoinGecko) +
praguri de pret pe watchlist. CONFIG-DRIVEN (fostul run_price_monitor.py).

Tot ce era hardcodat (watchlist, praguri, intervale, surse, limite) vine acum din
market_alerts.conf. Praguri PER-MONEDA + default + prag pt monede noi. Un singur
proces, cache de pret comun (un singur buget API CoinMarketCap). Modulele de jos
raman separate (new_coins_discovery vs pricechecker); aici e doar cablajul.

  python3 market_alerts.py
  python3 market_alerts.py --config alt.conf
"""
from __future__ import annotations

import argparse
import os
import threading
import time

from alerts_config import load_config
from pricechecker import start_price_alert_checker
from pricefetcher import create_cachePriceAll
# refolosim handlerele + starterul de monede noi (acum parametrizat) din modulul vechi
from run_price_monitor import (
    CMC_API_KEY,
    alert_handler,
    periodic_cleanup,
    start_new_coin_checker,
)

_HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description="Monitor alerte: monede noi + praguri pret (config-driven).")
    ap.add_argument("--config", default=os.path.join(_HERE, "market_alerts.conf"))
    ap.add_argument("--check", action="store_true", help="valideaza configul + importurile si iese (nu porneste monitorul)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    ac = cfg["alert_config"]
    print("=" * 70)
    print(f"⚙️ market_alerts — config: {args.config}")
    print(f"   watchlist : {cfg['watch']}  (max {cfg['max_monitored']})")
    print(f"   default   : up +{ac['default']['up_percent']}% / down -{ac['default']['down_percent']}%")
    print(f"   new_coin  : up +{ac['dynamic']['up_percent']}% / down -{ac['dynamic']['down_percent']}%")
    if ac["per_coin"]:
        pc = ", ".join(f"{k}(+{v['up_percent']}/-{v['down_percent']})" for k, v in ac["per_coin"].items())
        print(f"   per-moneda: {pc}")
    print(f"   cooldown {ac['cooldown_minutes']}min | lookback {ac['lookback_hours']}h | "
          f"scan pret {cfg['price_scan_seconds']}s | scan monede noi {cfg['new_coins_scan_seconds']}s")
    print("=" * 70)

    if args.check:
        print("✅ --check: config valid + importuri OK. Ies fara sa pornesc monitorul.")
        return

    print("\n⏳ Init cache preturi...")
    cachePriceAll = create_cachePriceAll(cmc_api_key=CMC_API_KEY,
                                         symbols=cfg["watch"], max_symbols=cfg["max_monitored"])
    print("⏳ Astept primul sync de pret (5s)...")
    time.sleep(5)

    print("⏳ Pornesc verificatorul de praguri de pret...")
    price_checker = start_price_alert_checker(
        cachePriceAll=cachePriceAll, alert_callback=alert_handler,
        check_interval_seconds=cfg["price_scan_seconds"], config=ac)

    cleanup_thread = threading.Thread(target=periodic_cleanup, name="periodic_cleanup",
                                      args=(cachePriceAll, None), daemon=True)
    cleanup_thread.start()

    new_coins_checker = None
    if not cfg.get("discover_new_coins", True):
        print("NEW COIN ALERT DEZACTIVAT din config (discover_new_coins = no) — doar watchlist")
    elif os.environ.get("ALERT_NEW_COIN", "").upper() == "TRUE":
        print("⏳ Pornesc checker-ul de monede noi...")
        new_coins_checker = start_new_coin_checker(
            cachePriceAll, interval_seconds=cfg["new_coins_scan_seconds"],
            max_new_coins=cfg["max_new_coins"], sources=cfg["sources"])
    else:
        print("NEW COIN ALERT DEZACTIVAT (ALERT_NEW_COIN != TRUE)")

    try:
        while True:
            time.sleep(160)
            print("\n👉 Astept alerte... (Ctrl+C pt oprire)\n")
    except KeyboardInterrupt:
        print("\n🛑 Opresc sistemul...")
        if new_coins_checker is not None:
            new_coins_checker.stop_monitoring()
        price_checker.stop_monitoring()
        print("👋 Gata.")


if __name__ == "__main__":
    main()
