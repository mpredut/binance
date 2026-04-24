import time

import binanceapi as api
import cacheManager as cm
import symbols as sym


CHECK_INTERVAL_SECONDS = 1 * 60 # 5 minutes
TARGET_GROWTH_PERCENT = 2.9
ASSET_REFERENCE_MINUTES_BACK_DEFAULT = 24 * 60 # 24 hours

# Evita vanzari repetate in acelasi run.
_sell_triggered = False


def _read_cache_rows():
    try:
        manager = cm.get_cache_manager("AssetValue")
        manager.enable_save_state_to_file()
        # Citim direct din memoria managerului; fisierul poate fi in urma.
        rows = manager.cache.get("TOTAL", [])
        print(f"[DEBUG] cache rows loaded: {len(rows)}")
        if rows:
            print("[DEBUG] dump cache TOTAL rows:")
            for idx, row in enumerate(rows, start=1):
                print(f"  [{idx}] {row}")
        return rows
    except Exception as e:
        print(f"ERROR reading AssetValue cache via cacheManager: {e}")
        return []


def _get_value_minutes_ago_from_cache(minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT):
    rows = _read_cache_rows()
    if not rows:
        print("[DEBUG] no rows available in cache TOTAL.")
        return None

    now_ts = int(time.time())
    target_ts = now_ts - int(minutes_back * 60)
    print(f"[DEBUG] window start for last {minutes_back}m: {target_ts}")

    # Folosim toate inregistrarile din ultimele `minutes_back` minute.
    window_rows = [r for r in rows if target_ts <= int(r.get("timestamp", 0)) <= now_ts]
    print(f"[DEBUG] candidate rows in window: {len(window_rows)}")
    if not window_rows:
        return None

    # Sortam cronologic, apoi alegem minimul dupa valoare.
    window_rows = sorted(window_rows, key=lambda r: int(r.get("timestamp", 0)))
    chosen = min(window_rows, key=lambda r: float(r.get("total_value_usdt", float("inf"))))
    print(f"[DEBUG] chosen MIN baseline row from window: {chosen}")
    return chosen


def _get_sell_symbol_for_asset(asset):
    # Prioritate: perechi deja folosite in proiect (sym.symbols).
    preferred = [f"{asset}USDC", f"{asset}USDT", f"{asset}BUSD"]
    for candidate in preferred:
        if candidate in sym.symbols:
            return candidate
    return None


def sell_all_assets():
    balances = api.get_account_assets_balances(include_zero=False)
    print(f"[DEBUG] balances fetched: {len(balances)}")
    for bal in balances:
        print(f"[DEBUG] balance row: {bal}")

    if not balances:
        print(" No balances available for selling.")
        return

    excluded_assets = {"USDT", "USDC", "BUSD"}
    sell_count = 0

    for bal in balances:
        asset = bal.get("asset")
        qty = float(bal.get("free", 0.0))
        total_qty = float(bal.get("total", 0.0))
        locked_qty = float(bal.get("locked", 0.0))
        print(
            f"[DEBUG] analyze asset={asset}, free={qty}, "
            f"locked={locked_qty}, total={total_qty}"
        )

        if asset in excluded_assets:
            print(f"[DEBUG] skip {asset}: excluded stable asset")
            continue
        if qty <= 0:
            print(f"[DEBUG] skip {asset}: free qty <= 0")
            continue

        sell_symbol = _get_sell_symbol_for_asset(asset)
        if not sell_symbol:
            print(f" Skip {asset}: no supported sell pair in symbols.py")
            continue
        print(f"[DEBUG] selected sell symbol for {asset}: {sell_symbol}")

        try:
            order = api.place_SELL_order_at_market(sell_symbol, qty)
            if order:
                sell_count += 1
                print(f" SELL market sent: {sell_symbol} qty={qty}")
            else:
                print(f" SELL failed: {sell_symbol} qty={qty}")
        except Exception as e:
            print(f"ERROR selling {sell_symbol}: {e}")

    print(f" Finished sell_all_assets. Orders sent: {sell_count}")


def evaluate_and_maybe_sell(threshold_percent=TARGET_GROWTH_PERCENT, minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT):
    global _sell_triggered

    current_value = api.get_total_assets_value_usdt(use_cache=False)
    print(f"[DEBUG] current assets value (USDT): {current_value}")
    past_row = _get_value_minutes_ago_from_cache(minutes_back=minutes_back)

    if not past_row:
        print(f" No baseline in cache yet for last {minutes_back}m.")
        return False

    past_value = float(past_row.get("total_value_usdt", 0.0))
    if past_value <= 0:
        print(" Invalid baseline value.")
        return False

    growth_percent = ((current_value - past_value) / past_value) * 100.0
    threshold_value = past_value * (1 + threshold_percent / 100.0)
    print(
        f"Current Assets value: {current_value:.4f} USDT, "
        f"Past assets value: {past_value:.4f} USDT, min_back={minutes_back:.4f}, growth={growth_percent:.4f}%"
    )
    print(
        f"[DEBUG] trigger when current >= {threshold_value:.4f} USDT "
        f"(threshold={threshold_percent}%)"
    )

    if _sell_triggered:
        print(" Sell already triggered in this process.")
        return False

    if growth_percent >= threshold_percent:
        print(
            f" Threshold reached ({growth_percent:.4f}% >= {threshold_percent}%). "
            "Selling all assets..."
        )
        sell_all_assets()
        _sell_triggered = True
        return True

    return False


def run_forever():
    print(
        f" Started. check_interval={CHECK_INTERVAL_SECONDS}s, "
        f"threshold={TARGET_GROWTH_PERCENT}%, minutes_back={ASSET_REFERENCE_MINUTES_BACK_DEFAULT}m"
    )
    while True:
        try:
            evaluate_and_maybe_sell(minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT)
        except Exception as e:
            print(f" Runtime ERROR: {e}")
        print(f"[DEBUG] sleep {CHECK_INTERVAL_SECONDS}s before next cycle")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()
