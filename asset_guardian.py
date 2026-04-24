import time

import binanceapi as api
import cacheManager as cm
import symbols as sym


CHECK_INTERVAL_SECONDS = 5 * 60
TARGET_GROWTH_PERCENT = 2.9
HOURS_BACK_DEFAULT = 24

# Evita vanzari repetate in acelasi run.
_sell_triggered = False


def _read_cache_rows():
    try:
        manager = cm.get_cache_manager("AssetValue")
        # Reincarca din fisier ca sa folosim ultimul cache salvat.
        manager.load_state()
        rows = manager.cache.get("TOTAL", [])
        print(f"[asset-guardian][debug] cache rows loaded: {len(rows)}")
        if rows:
            print("[asset-guardian][debug] dump cache TOTAL rows:")
            for idx, row in enumerate(rows, start=1):
                print(f"  [{idx}] {row}")
        return rows
    except Exception as e:
        print(f"[asset-guardian] Error reading AssetValue cache via cacheManager: {e}")
        return []


def _get_value_24h_ago_from_cache(hours_back=HOURS_BACK_DEFAULT):
    rows = _read_cache_rows()
    if not rows:
        print("[asset-guardian][debug] no rows available in cache TOTAL.")
        return None

    target_ts = int(time.time()) - int(hours_back * 3600)
    print(f"[asset-guardian][debug] target timestamp for {hours_back}h back: {target_ts}")
    older = [r for r in rows if int(r.get("timestamp", 0)) <= target_ts]
    print(f"[asset-guardian][debug] candidate baseline rows: {len(older)}")
    if not older:
        return None
    chosen = older[-1]
    print(f"[asset-guardian][debug] chosen baseline row: {chosen}")
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
    print(f"[asset-guardian][debug] balances fetched: {len(balances)}")
    for bal in balances:
        print(f"[asset-guardian][debug] balance row: {bal}")

    if not balances:
        print("[asset-guardian] No balances available for selling.")
        return

    excluded_assets = {"USDT", "USDC", "BUSD"}
    sell_count = 0

    for bal in balances:
        asset = bal.get("asset")
        qty = float(bal.get("free", 0.0))
        total_qty = float(bal.get("total", 0.0))
        locked_qty = float(bal.get("locked", 0.0))
        print(
            f"[asset-guardian][debug] analyze asset={asset}, free={qty}, "
            f"locked={locked_qty}, total={total_qty}"
        )

        if asset in excluded_assets:
            print(f"[asset-guardian][debug] skip {asset}: excluded stable asset")
            continue
        if qty <= 0:
            print(f"[asset-guardian][debug] skip {asset}: free qty <= 0")
            continue

        sell_symbol = _get_sell_symbol_for_asset(asset)
        if not sell_symbol:
            print(f"[asset-guardian] Skip {asset}: no supported sell pair in symbols.py")
            continue
        print(f"[asset-guardian][debug] selected sell symbol for {asset}: {sell_symbol}")

        try:
            order = api.place_SELL_order_at_market(sell_symbol, qty)
            if order:
                sell_count += 1
                print(f"[asset-guardian] SELL market sent: {sell_symbol} qty={qty}")
            else:
                print(f"[asset-guardian] SELL failed: {sell_symbol} qty={qty}")
        except Exception as e:
            print(f"[asset-guardian] Error selling {sell_symbol}: {e}")

    print(f"[asset-guardian] Finished sell_all_assets. Orders sent: {sell_count}")


def evaluate_and_maybe_sell(threshold_percent=TARGET_GROWTH_PERCENT, hours_back=HOURS_BACK_DEFAULT):
    global _sell_triggered

    print("[asset-guardian][debug] evaluate cycle started")
    current_value = api.get_total_assets_value_usdt(use_cache=False)
    print(f"[asset-guardian][debug] current assets value (USDT): {current_value}")
    past_row = _get_value_24h_ago_from_cache(hours_back=hours_back)

    if not past_row:
        print(f"[asset-guardian] No baseline in cache yet for last {hours_back}h.")
        return False

    past_value = float(past_row.get("total_value_usdt", 0.0))
    if past_value <= 0:
        print("[asset-guardian] Invalid 24h baseline value.")
        return False

    growth_percent = ((current_value - past_value) / past_value) * 100.0
    threshold_value = past_value * (1 + threshold_percent / 100.0)
    print(
        f"[asset-guardian] current={current_value:.4f} USDT, "
        f"{hours_back}h={past_value:.4f} USDT, growth={growth_percent:.4f}%"
    )
    print(
        f"[asset-guardian][debug] trigger when current >= {threshold_value:.4f} USDT "
        f"(threshold={threshold_percent}%)"
    )

    if _sell_triggered:
        print("[asset-guardian] Sell already triggered in this process.")
        return False

    if growth_percent >= threshold_percent:
        print(
            f"[asset-guardian] Threshold reached ({growth_percent:.4f}% >= {threshold_percent}%). "
            "Selling all assets..."
        )
        sell_all_assets()
        _sell_triggered = True
        return True

    return False


def run_forever():
    print(
        f"[asset-guardian] Started. check_interval={CHECK_INTERVAL_SECONDS}s, "
        f"threshold={TARGET_GROWTH_PERCENT}%, hours_back={HOURS_BACK_DEFAULT}h"
    )
    while True:
        try:
            evaluate_and_maybe_sell(hours_back=HOURS_BACK_DEFAULT)
        except Exception as e:
            print(f"[asset-guardian] Runtime error: {e}")
        print(f"[asset-guardian][debug] sleep {CHECK_INTERVAL_SECONDS}s before next cycle")
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_forever()
