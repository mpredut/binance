import os
import math
import time

from binance_api import bapi as api
from providers.market_api import api as mkt   # proxy unic guardat (Instrument.place)
from providers.quantity import resolve_assets
from assetguardian_state import AssetGuardianState
import cacheManager as cm
import symbols as sym

# 23 iul: incarca parametrii tunabili din assetguardian_config.env (versionat,
# se COMITE — fara secrete) INAINTE de a citi orice os.environ.get(...) de mai
# jos. botcore.load_dotenv NU suprascrie variabile deja setate in mediul real.
from botcore import load_dotenv as _load_dotenv
_load_dotenv("assetguardian_config.env")

CHECK_INTERVAL_SECONDS = float(os.environ.get("AG_CHECK_INTERVAL_SEC", str(0.9 * 60)))  # 9 minute
# 2.9% declansa "sell all" la fiecare ciclu intr-un uptrend, dar sell_all_assets
# cheama place_safe_order(force=False) -> apply_weight_limit zero-uia ordinul
# (vindea NIMIC, spam "Orders sent: 0"). Walk-forward pe feed real (291z) a aratat
# ca vanzarea agresiva pierde fata de deTinere -> ridicat la 100 (practic oprit).
# Protectia reala de crash o face trailing_stop.py (force=True, prag larg ~22%).
TARGET_GROWTH_PERCENT = float(os.environ.get("AG_TARGET_GROWTH_PCT", "100.0"))
TARGET_DROP_PERCENT = float(os.environ.get("AG_TARGET_DROP_PCT", "7.0"))
ASSET_REFERENCE_MINUTES_BACK_DEFAULT = float(os.environ.get("AG_REFERENCE_MINUTES_BACK", str(24 * 60)))  # 24 ore

BUY_SYMBOL_DEFAULT = sym.symbols[0] if sym.symbols else "BTCUSDC"
BUY_USE_CASH_RATIO = float(os.environ.get("AG_BUY_USE_CASH_RATIO", "0.995"))
BUY_TIERS_RAW = os.environ.get(
    "AG_BUY_TIERS", f"{TARGET_DROP_PERCENT}:0.35,10:0.35,14:0.295")
RECOVERY_RESET_PERCENT = float(os.environ.get("AG_RECOVERY_RESET_PCT", "3.0"))
NEAR_TRIGGER_SECONDS = float(os.environ.get("AG_NEAR_TRIGGER_SEC", "30"))
ACTIVE_TRIGGER_SECONDS = float(os.environ.get("AG_ACTIVE_TRIGGER_SEC", "15"))
NEAR_TRIGGER_DISTANCE_PCT = float(os.environ.get("AG_NEAR_TRIGGER_DISTANCE_PCT", "2.0"))


def _parse_buy_tiers(raw):
    tiers = []
    for item in str(raw).split(","):
        threshold, allocation = item.strip().split(":", 1)
        tiers.append((float(threshold), float(allocation)))
    tiers.sort()
    return tuple(tiers)


BUY_TIERS = _parse_buy_tiers(BUY_TIERS_RAW)
STATE = AssetGuardianState()
_last_evaluation = {"drawdown": None, "pending_tier": False}


def _validate_config():
    if not math.isfinite(CHECK_INTERVAL_SECONDS) or CHECK_INTERVAL_SECONDS <= 0:
        raise ValueError("AG_CHECK_INTERVAL_SEC trebuie sa fie > 0")
    if (not math.isfinite(TARGET_GROWTH_PERCENT)
            or not math.isfinite(TARGET_DROP_PERCENT)
            or TARGET_GROWTH_PERCENT <= 0 or TARGET_DROP_PERCENT <= 0):
        raise ValueError("pragurile AG growth/drop trebuie sa fie > 0")
    if (not math.isfinite(ASSET_REFERENCE_MINUTES_BACK_DEFAULT)
            or ASSET_REFERENCE_MINUTES_BACK_DEFAULT <= 0):
        raise ValueError("AG_REFERENCE_MINUTES_BACK trebuie sa fie > 0")
    if not math.isfinite(BUY_USE_CASH_RATIO) or not 0 < BUY_USE_CASH_RATIO <= 1:
        raise ValueError("AG_BUY_USE_CASH_RATIO trebuie sa fie in (0, 1]")
    if not BUY_TIERS or any(
            not math.isfinite(threshold) or threshold <= 0
            or not math.isfinite(allocation) or allocation <= 0
            for threshold, allocation in BUY_TIERS):
        raise ValueError("AG_BUY_TIERS trebuie sa contina prag:alocare pozitive")
    if len({threshold for threshold, _ in BUY_TIERS}) != len(BUY_TIERS):
        raise ValueError("AG_BUY_TIERS contine praguri duplicate")
    if sum(allocation for _, allocation in BUY_TIERS) > BUY_USE_CASH_RATIO + 1e-12:
        raise ValueError("suma alocarilor AG_BUY_TIERS depaseste AG_BUY_USE_CASH_RATIO")
    for value, name in (
            (RECOVERY_RESET_PERCENT, "AG_RECOVERY_RESET_PCT"),
            (NEAR_TRIGGER_SECONDS, "AG_NEAR_TRIGGER_SEC"),
            (ACTIVE_TRIGGER_SECONDS, "AG_ACTIVE_TRIGGER_SEC"),
            (NEAR_TRIGGER_DISTANCE_PCT, "AG_NEAR_TRIGGER_DISTANCE_PCT")):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} trebuie sa fie finit si > 0")


_validate_config()


def _finite_float(raw, *, positive=False):
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def _row_value_usdc(row):
    """Valoare normalizata USDC; accepta cheia istorica USDT doar la citire."""
    raw = row.get("total_value_usdc")
    if raw is None:
        raw = row.get("total_value_usdt")
    return _finite_float(raw, positive=True)


def _row_timestamp(row):
    return _finite_float(row.get("timestamp"), positive=True)

def _read_cache_rows():
    try:
        manager = cm.get_cache_manager("AssetValue")
        manager.enable_save_state_to_file()
        # Citim direct din memoria managerului; fisierul poate fi in urma.
        # Threadul CacheAssetValueManager poate adauga simultan un snapshot.
        # Copia sub lock ofera fiecarei evaluari o vedere coerenta si scurta.
        with manager.lock:
            rows = list(manager.cache.get("TOTAL", []))
        print(f"[DEBUG] cache rows loaded: {len(rows)}")
        #if rows:
            #print("[DEBUG] dump cache TOTAL rows:")
            #for idx, row in enumerate(rows, start=1):
            #    print(f"  [{idx}] {row}")
        return rows
    except Exception as e:
        print(f"ERROR reading AssetValue cache via cacheManager: {e}")
        return []


def _get_window_extrema_from_cache(minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT):
    rows = _read_cache_rows()
    if not rows:
        print("[DEBUG] no rows available in cache TOTAL.")
        return None

    minutes_back = _finite_float(minutes_back, positive=True)
    if minutes_back is None:
        print(" Invalid cache window; skip evaluation.")
        return None
    now_ts = int(time.time())
    target_ts = now_ts - int(minutes_back * 60)
    print(f"[DEBUG] window start for last {minutes_back}m: {target_ts}")

    # Folosim toate inregistrarile din ultimele `minutes_back` minute.
    window_rows = [
        r for r in rows
        if isinstance(r, dict)
        and _row_timestamp(r) is not None
        and target_ts <= _row_timestamp(r) <= now_ts
        and _row_value_usdc(r) is not None
    ]
    print(f"[DEBUG] candidate rows in window: {len(window_rows)}")
    if not window_rows:
        return None

    #printam doar un row per ora pentru a nu fi prea multe linii in output
    if window_rows:
        print("[DEBUG] dump cache TOTAL rows:")

    last_printed_ts = None
    for idx, row in enumerate(window_rows, start=1):
        current_ts = _row_timestamp(row)

        # primul row se afiseaza mereu
        if last_printed_ts is None:
            print(f"  [{idx}] {row}")
            last_printed_ts = current_ts
            continue

        # afiseaza doar daca a trecut minim 1 ora
        if current_ts - last_printed_ts >= 3600:
            print(f"  [{idx}] {row}")
            last_printed_ts = current_ts


    # Profitul se masoara fata de minim, drawdown-ul fata de maxim.
    window_rows = sorted(window_rows, key=_row_timestamp)
    minimum = min(window_rows, key=_row_value_usdc)
    maximum = max(window_rows, key=_row_value_usdc)
    print(f"[DEBUG] chosen MIN: {minimum}")
    print(f"[DEBUG] chosen MAX: {maximum}")
    return minimum, maximum


def _get_value_minutes_ago_from_cache(minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT):
    """Compatibilitate pentru consumatorii vechi: intoarce minimul ferestrei."""
    extrema = _get_window_extrema_from_cache(minutes_back)
    return extrema[0] if extrema else None


def _get_sell_symbol_for_asset(asset):
    # Contul Binance operational foloseste exclusiv USDC drept quote/cash.
    candidate = f"{asset}USDC"
    return candidate if candidate in sym.symbols else None


def sell_all_assets():
    balances = api.get_account_assets_balances()
    if not balances:
       print("No balances available for selling.")
       return False
        
    print(f"[DEBUG] balances fetched: {len(balances)}")
  
    # Extragem doar asset-urile de baza din sym.symbols (ex: BTCUSDC -> BTC)
    tracked_assets = {resolve_assets(s)[0] for s in sym.symbols}
    
    excluded_assets = {"USDC"}
    sell_count = 0

    for bal in balances:
        if not isinstance(bal, dict):
            print("[DEBUG] skip malformed non-dict balance row")
            continue
        asset = bal.get("asset")        
        if asset not in tracked_assets:
            #print(f"[DEBUG] skip {asset}: not in sym.symbols")
            continue

        qty = _finite_float(bal.get("free", 0.0))
        total_qty = _finite_float(bal.get("total", 0.0))
        locked_qty = _finite_float(bal.get("locked", 0.0))

        if qty is None or total_qty is None or locked_qty is None:
            print(f"[DEBUG] skip malformed balance row for asset={asset}")
            continue

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
            current_price = api.get_current_price(sell_symbol)
            order = mkt.place(
                sell_symbol, "SELL", current_price, qty,
                force=False, smart=False, caller_owns_retry=True,
                motivation="assetguardian_growth_exit")
            if order:
                sell_count += 1
                print(f" SELL safe-order sent: {sell_symbol} qty={qty}")
            else:
                print(f" SELL safe-order failed: {sell_symbol} qty={qty}")
        except Exception as e:
            print(f"ERROR selling {sell_symbol}: {e}")

    print(f" Finished sell_all_assets. Orders sent: {sell_count}")
    return sell_count > 0


def buy_with_all_cash(buy_symbol=BUY_SYMBOL_DEFAULT, cash_ratio=BUY_USE_CASH_RATIO,
                      cash_amount=None):
    try:
        _, quote_asset = resolve_assets(buy_symbol)
        cash_ratio = _finite_float(cash_ratio, positive=True)
        if cash_ratio is None or cash_ratio > 1:
            print(f" Invalid cash ratio for buy: {cash_ratio}")
            return False
        free_cash = _finite_float(
            mkt.provider_by_name("binance").free_balance(quote_asset),
            positive=True,
        )
        current_price = _finite_float(api.get_current_price(buy_symbol), positive=True)
    except Exception as e:
        print(f"ERROR preparing buy for {buy_symbol}: {e}")
        return False
    print(
        f"[DEBUG] buy check symbol={buy_symbol}, quote={quote_asset}, "
        f"free_cash={free_cash}, current_price={current_price}"
    )

    if free_cash is None:
        print(f" No valid available {quote_asset} balance; skip buy fail-closed.")
        return False
    if current_price is None:
        print(f" Invalid current price for {buy_symbol}.")
        return False

    requested_cash = (free_cash * cash_ratio if cash_amount is None
                      else _finite_float(cash_amount, positive=True))
    if requested_cash is None:
        print(" Invalid requested cash amount for buy.")
        return False
    cash_to_use = min(requested_cash, free_cash * BUY_USE_CASH_RATIO)
    qty = cash_to_use / current_price
    if qty <= 0:
        print(" Computed qty <= 0. Skip buy.")
        return False

    print(
        f" BUY trigger active -> symbol={buy_symbol}, using {cash_to_use:.6f} {quote_asset} "
        f"(safe cap {cash_ratio*100:.2f}% of free cash), qty={qty:.8f}"
    )
    try:
        order = mkt.place(
            buy_symbol, "BUY", current_price, qty,
            force=False, smart=False, caller_owns_retry=True,
            motivation="assetguardian_drawdown_buy")
        if order:
            print(f" BUY safe-order sent: {buy_symbol}, qty={qty:.8f}")
            return True
        print(f" BUY safe-order failed: {buy_symbol}, qty={qty:.8f}")
        return False
    except Exception as e:
        print(f"ERROR buying {buy_symbol}: {e}")
        return False


def _campaign_tier(drawdown_abs, maximum_row, free_cash):
    """Selectează prima tranșă depășită și încă neacceptată în campania curentă."""
    state = STATE.load()
    if drawdown_abs < RECOVERY_RESET_PERCENT:
        if state:
            STATE.save({})
        return None, {}

    peak_value = _row_value_usdc(maximum_row)
    peak_ts = _row_timestamp(maximum_row)
    stored_peak = _finite_float(state.get("peak_value"), positive=True)
    if not state or stored_peak is None or peak_value > stored_peak:
        state = {
            "peak_value": peak_value,
            "peak_ts": peak_ts,
            "initial_cash": free_cash,
            "completed_tiers": [],
        }
        STATE.save(state)

    completed = {float(value) for value in state.get("completed_tiers", [])}
    for threshold, allocation in BUY_TIERS:
        if drawdown_abs >= threshold and threshold not in completed:
            return (threshold, allocation), state
    return None, state


def _complete_campaign_tier(state, threshold):
    completed = {float(value) for value in state.get("completed_tiers", [])}
    completed.add(float(threshold))
    state["completed_tiers"] = sorted(completed)
    STATE.save(state)


def _next_check_seconds():
    drawdown = _last_evaluation.get("drawdown")
    if drawdown is None:
        return CHECK_INTERVAL_SECONDS
    if _last_evaluation.get("pending_tier"):
        return min(CHECK_INTERVAL_SECONDS, ACTIVE_TRIGGER_SECONDS)
    completed = {float(value) for value in STATE.load().get("completed_tiers", [])}
    next_threshold = next((threshold for threshold, _ in BUY_TIERS
                           if threshold not in completed), None)
    if (next_threshold is not None
            and next_threshold - abs(drawdown) <= NEAR_TRIGGER_DISTANCE_PCT):
        return min(CHECK_INTERVAL_SECONDS, NEAR_TRIGGER_SECONDS)
    return CHECK_INTERVAL_SECONDS


def evaluate_and_maybe_sell_or_buy(
    threshold_percent=TARGET_GROWTH_PERCENT,
    drop_percent=TARGET_DROP_PERCENT,
    minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT,
    buy_symbol=BUY_SYMBOL_DEFAULT,
):
    _last_evaluation.update(drawdown=None, pending_tier=False)
    threshold_percent = _finite_float(threshold_percent, positive=True)
    drop_percent = _finite_float(drop_percent, positive=True)
    if threshold_percent is None or drop_percent is None:
        print("Error: invalid growth/drop threshold; skip evaluation.")
        return False

    current_value = _finite_float(
        api.get_total_assets_value_usdc(use_cache=False), positive=True)
    if current_value is None:
        print(f"Error: evaluate_and_maybe_sell_or_buy: Current assets value can't be calculated")
        return False

    print(f"[DEBUG] Current ASSETS value (USDC): {current_value}")
    extrema = _get_window_extrema_from_cache(minutes_back=minutes_back)

    if not extrema:
        print(f" No baseline in cache yet for last {minutes_back}m.")
        return False

    minimum_row, maximum_row = extrema
    minimum_value = _row_value_usdc(minimum_row)
    maximum_value = _row_value_usdc(maximum_row)
    if not minimum_value or not maximum_value:
        print(" Invalid baseline value.")
        return False

    growth_percent = ((current_value - minimum_value) / minimum_value) * 100.0
    drawdown_percent = ((current_value - maximum_value) / maximum_value) * 100.0
    _last_evaluation["drawdown"] = drawdown_percent
    if abs(drawdown_percent) < RECOVERY_RESET_PERCENT and STATE.load():
        print(f" Drawdown recovered below {RECOVERY_RESET_PERCENT:.2f}%; rearm BUY campaign.")
        STATE.save({})
    threshold_value = minimum_value * (1 + threshold_percent / 100.0)
    print(f"Current ASSETS value: {current_value:.1f} USDC ")
    print(f"Window MIN/MAX: {minimum_value:.1f}/{maximum_value:.1f} USDC, "
          f"minutes_back={minutes_back:.4f}, growth_from_min={growth_percent:.4f}%, "
          f"drawdown_from_max={drawdown_percent:.4f}%"
    )
    print(
        f"[DEBUG] Trigger when ASSETS >= {threshold_value:.4f} USDC "
        f"(threshold={threshold_percent}%)"
    )

    if growth_percent >= threshold_percent:
        print(
            f" Threshold reached ({growth_percent:.4f}% >= {threshold_percent}%). "
            "Selling all assets..."
        )
        return sell_all_assets()

    if drawdown_percent <= -BUY_TIERS[0][0]:
        print(
            f" Drawdown threshold reached ({drawdown_percent:.4f}% "
            f"<= -{BUY_TIERS[0][0]:.4f}%). Checking staged BUY..."
        )
        provider = mkt.provider_by_name("binance")
        free_cash = (_finite_float(provider.free_balance("USDC"), positive=True)
                     if provider else None)
        if free_cash is None:
            print(" No valid USDC balance for staged BUY.")
            return False
        tier, campaign = _campaign_tier(abs(drawdown_percent), maximum_row, free_cash)
        if tier is None:
            print(" No uncompleted BUY tier at current drawdown.")
            return False
        threshold, allocation = tier
        _last_evaluation["pending_tier"] = True
        initial_cash = _finite_float(campaign.get("initial_cash"), positive=True)
        cash_amount = initial_cash * allocation
        print(f" BUY tier -{threshold:.2f}% allocation={allocation:.3f} "
              f"cash_target={cash_amount:.6f} USDC")
        if buy_with_all_cash(buy_symbol=buy_symbol, cash_amount=cash_amount):
            _complete_campaign_tier(campaign, threshold)
            _last_evaluation["pending_tier"] = False
            return True

    return False


def run_forever():
    print(
        f" Started. check_interval={CHECK_INTERVAL_SECONDS}s, "
        f"sell_threshold={TARGET_GROWTH_PERCENT}%, drop_buy_threshold={TARGET_DROP_PERCENT}%, "
        f"minutes_back={ASSET_REFERENCE_MINUTES_BACK_DEFAULT}m, buy_symbol={BUY_SYMBOL_DEFAULT}"
    )
    while True:
        try:
            evaluate_and_maybe_sell_or_buy(minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT)
        except Exception as e:
            print(f" Runtime ERROR: {e}")
        sleep_seconds = _next_check_seconds()
        print(f"[DEBUG] sleep {sleep_seconds}s before next cycle")
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    run_forever()
