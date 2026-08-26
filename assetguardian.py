import os
import math
import time

from binance_api import bapi as api
from providers.market_api import api as mkt   # Single guarded proxy (Instrument.place).
from providers.quantity import resolve_assets
from assetguardian_state import AssetGuardianState
import cacheManager as cm
import symbols as sym

# Load tunable parameters from the versioned, secret-free config before reading
# environment variables below. load_dotenv does not overwrite real environment.
from botcore import load_dotenv as _load_dotenv
_load_dotenv("assetguardian_config.env")

CHECK_INTERVAL_SECONDS = float(os.environ.get("AG_CHECK_INTERVAL_SEC", str(0.9 * 60)))  # 0.9 minutes.
# A 2.9% threshold triggered sell-all every uptrend cycle, but the safe-order
# weight limit reduced it to zero and produced log spam. A 291-day walk-forward
# showed aggressive selling underperformed holding, so 100 effectively disables
# it. trailing_stop.py provides the actual broad crash protection.
TARGET_GROWTH_PERCENT = float(os.environ.get("AG_TARGET_GROWTH_PCT", "100.0"))
TARGET_DROP_PERCENT = float(os.environ.get("AG_TARGET_DROP_PCT", "7.0"))
ASSET_REFERENCE_MINUTES_BACK_DEFAULT = float(os.environ.get("AG_REFERENCE_MINUTES_BACK", str(24 * 60)))  # 24 hours.

BUY_USE_CASH_RATIO = float(os.environ.get("AG_BUY_USE_CASH_RATIO", "0.995"))
BUY_TIERS_RAW = os.environ.get(
    "AG_BUY_TIERS", f"{TARGET_DROP_PERCENT}:0.35,10:0.35,14:0.30")
TRACKED_SYMBOLS_RAW = os.environ.get("AG_SYMBOLS", ",".join(sym.symbols))
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


def _parse_symbols(raw):
    return tuple(dict.fromkeys(
        item.strip().upper() for item in str(raw).split(",") if item.strip()))


BUY_TIERS = _parse_buy_tiers(BUY_TIERS_RAW)
TRACKED_SYMBOLS = _parse_symbols(TRACKED_SYMBOLS_RAW)
LEGACY_BUY_SYMBOL_DEFAULT = sym.symbols[0] if sym.symbols else "BTCUSDC"
# Backward-compatible default for direct/manual callers.  The live evaluator
# always passes the symbol whose own drawdown triggered the order.
BUY_SYMBOL_DEFAULT = LEGACY_BUY_SYMBOL_DEFAULT
STATE = AssetGuardianState()
_last_evaluation = {
    symbol: {"drawdown": None, "pending_tier": False}
    for symbol in TRACKED_SYMBOLS
}
_local_price_samples = {symbol: [] for symbol in TRACKED_SYMBOLS}


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
    if sum(allocation for _, allocation in BUY_TIERS) > 1.0 + 1e-12:
        raise ValueError("suma ponderilor AG_BUY_TIERS depaseste 1")
    if not TRACKED_SYMBOLS:
        raise ValueError("AG_SYMBOLS trebuie sa contina cel putin un simbol")
    unsupported = [symbol for symbol in TRACKED_SYMBOLS if symbol not in sym.symbols]
    if unsupported:
        raise ValueError(f"AG_SYMBOLS contine simboluri nepermise: {unsupported}")
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


def _price_row(row):
    """Normalize a Price24 row to ``(timestamp_seconds, positive_price)``."""
    if isinstance(row, dict):
        raw_ts = row.get("timestamp")
        raw_price = row.get("price")
    elif isinstance(row, (list, tuple)) and len(row) >= 2:
        raw_ts, raw_price = row[0], row[1]
    else:
        return None
    timestamp = _finite_float(raw_ts, positive=True)
    price = _finite_float(raw_price, positive=True)
    if timestamp is None or price is None:
        return None
    if timestamp > 100_000_000_000:  # Price24 persists Binance timestamps in ms.
        timestamp /= 1000.0
    return timestamp, price


def _read_symbol_price_rows(symbol):
    """Copy one symbol's shared 24h price history under the manager lock."""
    try:
        managers = cm.get_cache_manager("Price24", symbols=list(TRACKED_SYMBOLS))
        manager = managers.get(symbol) if isinstance(managers, dict) else None
        if manager is None:
            print(f"ERROR Price24 manager missing for {symbol}")
            return []
        with manager.lock:
            rows = list(manager.cache.get(symbol, []))
        print(f"[DEBUG] {symbol} Price24 rows loaded: {len(rows)}")
        return rows
    except Exception as e:
        print(f"ERROR reading Price24 cache for {symbol}: {e}")
        return []


def _get_symbol_window_extrema(symbol, current_price,
                               minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT):
    """Return validated per-symbol price minimum/maximum for the rolling window."""
    minutes_back = _finite_float(minutes_back, positive=True)
    current_price = _finite_float(current_price, positive=True)
    if minutes_back is None or current_price is None:
        print(f"[{symbol}] Invalid price/cache window; skip evaluation.")
        return None
    now_ts = float(time.time())
    target_ts = now_ts - minutes_back * 60.0

    local = _local_price_samples.setdefault(symbol, [])
    local.append([now_ts, current_price])
    local[:] = [row for row in local
                if (parsed := _price_row(row)) is not None
                and target_ts <= parsed[0] <= now_ts]

    normalized = []
    seen = set()
    for raw in _read_symbol_price_rows(symbol) + list(local):
        parsed = _price_row(raw)
        if parsed is None:
            continue
        timestamp, price = parsed
        if not target_ts <= timestamp <= now_ts:
            continue
        key = (timestamp, price)
        if key not in seen:
            seen.add(key)
            normalized.append({"timestamp": timestamp, "price": price})

    print(f"[DEBUG] {symbol} candidate price rows: {len(normalized)}")
    if len(normalized) < 2:
        print(f"[{symbol}] Insufficient per-asset price baseline; skip evaluation.")
        return None
    minimum = min(normalized, key=lambda row: row["price"])
    maximum = max(normalized, key=lambda row: row["price"])
    print(f"[DEBUG] {symbol} chosen MIN/MAX: {minimum}/{maximum}")
    return minimum, maximum


def sell_asset(symbol, current_price=None):
    """Sell only the free balance belonging to the triggered symbol."""
    try:
        base_asset, _ = resolve_assets(symbol)
        provider = mkt.provider_by_name("binance")
        if provider is None:
            print(f"[{symbol}] Binance provider unavailable; skip SELL fail-closed.")
            return False
        qty = _finite_float(provider.free_balance(base_asset), positive=True)
        price = _finite_float(
            current_price if current_price is not None else api.get_current_price(symbol),
            positive=True,
        )
    except Exception as e:
        print(f"ERROR preparing SELL for {symbol}: {e}")
        return False
    if qty is None:
        print(f"[{symbol}] No valid free {base_asset} balance; skip SELL.")
        return False
    if price is None:
        print(f"[{symbol}] Invalid current price; skip SELL.")
        return False
    try:
        order = mkt.place(
            symbol, "SELL", price, qty,
            force=False, smart=False, caller_owns_retry=True,
            motivation="assetguardian_growth_exit")
        if order:
            print(f" SELL safe-order sent: {symbol} qty={qty}")
            return True
        print(f" SELL safe-order failed: {symbol} qty={qty}")
        return False
    except Exception as e:
        print(f"ERROR selling {symbol}: {e}")
        return False


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


def _completed_tier_values(state):
    completed = set()
    for raw in state.get("completed_tiers", []):
        value = _finite_float(raw, positive=True)
        if value is not None:
            completed.add(value)
    return completed


def _load_state_root():
    """Load v2 per-symbol state and map legacy global campaigns to BTC only."""
    raw = STATE.load()
    if not isinstance(raw, dict):
        raw = {}
    symbol_rows = raw.get("symbols")
    if isinstance(symbol_rows, dict):
        return {
            "version": 2,
            "symbols": {
                str(symbol): dict(value)
                for symbol, value in symbol_rows.items()
                if isinstance(value, dict)
            },
        }
    legacy_keys = {"peak_value", "peak_ts", "initial_cash", "completed_tiers"}
    if legacy_keys.intersection(raw):
        return {
            "version": 2,
            "symbols": {LEGACY_BUY_SYMBOL_DEFAULT: dict(raw)},
        }
    return {"version": 2, "symbols": {}}


def _symbol_campaign(symbol):
    return dict(_load_state_root()["symbols"].get(symbol, {}))


def _save_symbol_campaign(symbol, campaign):
    root = _load_state_root()
    symbols_state = dict(root["symbols"])
    if campaign:
        symbols_state[symbol] = dict(campaign)
    else:
        symbols_state.pop(symbol, None)
    STATE.save({"version": 2, "symbols": symbols_state})


def _campaign_tier(symbol, drawdown_abs, maximum_row, free_cash):
    """Select one crossed, incomplete BUY tier for one symbol campaign."""
    state = _symbol_campaign(symbol)
    if drawdown_abs < RECOVERY_RESET_PERCENT:
        if state:
            _save_symbol_campaign(symbol, {})
        return None, {}

    peak_price = _finite_float(maximum_row.get("price"), positive=True)
    peak_ts = _finite_float(maximum_row.get("timestamp"), positive=True)
    stored_peak = _finite_float(state.get("peak_price"), positive=True)
    if (peak_price is None or peak_ts is None
            or _finite_float(free_cash, positive=True) is None):
        return None, state
    if not state:
        state = {
            "peak_price": peak_price,
            "peak_ts": peak_ts,
            "initial_cash": free_cash,
            "completed_tiers": [],
        }
        _save_symbol_campaign(symbol, state)
    elif stored_peak is None:
        # A legacy global campaign has no meaningful per-asset peak. Preserve
        # its completed tiers so migration cannot repeat an already accepted
        # order, then attach the first valid per-asset peak conservatively.
        state = dict(state)
        state["peak_price"] = peak_price
        state["peak_ts"] = peak_ts
        if _finite_float(state.get("initial_cash"), positive=True) is None:
            state["initial_cash"] = free_cash
        state["completed_tiers"] = sorted(_completed_tier_values(state))
        _save_symbol_campaign(symbol, state)
    elif peak_price > stored_peak:
        # A higher peak within the same unrecovered campaign changes the
        # reference, but must never clear completed tiers or replenish budget.
        state = dict(state)
        state["peak_price"] = peak_price
        state["peak_ts"] = peak_ts
        _save_symbol_campaign(symbol, state)

    completed = _completed_tier_values(state)
    for threshold, allocation in BUY_TIERS:
        if drawdown_abs >= threshold and threshold not in completed:
            return (threshold, allocation), state
    return None, state


def _complete_campaign_tier(symbol, state, threshold):
    completed = _completed_tier_values(state)
    completed.add(float(threshold))
    state = dict(state)
    state["completed_tiers"] = sorted(completed)
    _save_symbol_campaign(symbol, state)


def _next_check_seconds():
    if any(state.get("pending_tier") for state in _last_evaluation.values()):
        return min(CHECK_INTERVAL_SECONDS, ACTIVE_TRIGGER_SECONDS)
    root = _load_state_root()
    for symbol, evaluation in _last_evaluation.items():
        drawdown = evaluation.get("drawdown")
        if drawdown is None:
            continue
        completed = _completed_tier_values(root["symbols"].get(symbol, {}))
        next_threshold = next((threshold for threshold, _ in BUY_TIERS
                               if threshold not in completed), None)
        if (next_threshold is not None
                and next_threshold - max(0.0, -drawdown) <= NEAR_TRIGGER_DISTANCE_PCT):
            return min(CHECK_INTERVAL_SECONDS, NEAR_TRIGGER_SECONDS)
    return CHECK_INTERVAL_SECONDS


def evaluate_symbol(symbol, threshold_percent=TARGET_GROWTH_PERCENT,
                    minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT):
    """Evaluate and possibly submit exactly one asset-specific SELL or BUY."""
    evaluation = _last_evaluation.setdefault(
        symbol, {"drawdown": None, "pending_tier": False})
    evaluation.update(drawdown=None, pending_tier=False)
    threshold_percent = _finite_float(threshold_percent, positive=True)
    if symbol not in TRACKED_SYMBOLS or threshold_percent is None:
        print(f"[{symbol}] Invalid symbol/growth threshold; skip evaluation.")
        return False

    current_price = _finite_float(api.get_current_price(symbol), positive=True)
    if current_price is None:
        print(f"[{symbol}] Current price unavailable; skip evaluation.")
        return False
    extrema = _get_symbol_window_extrema(
        symbol, current_price, minutes_back=minutes_back)

    if not extrema:
        print(f"[{symbol}] No per-asset baseline for the last {minutes_back}m.")
        return False

    minimum_row, maximum_row = extrema
    minimum_price = _finite_float(minimum_row.get("price"), positive=True)
    maximum_price = _finite_float(maximum_row.get("price"), positive=True)
    if minimum_price is None or maximum_price is None:
        print(f"[{symbol}] Invalid per-asset baseline.")
        return False

    growth_percent = ((current_price - minimum_price) / minimum_price) * 100.0
    drawdown_percent = ((current_price - maximum_price) / maximum_price) * 100.0
    evaluation["drawdown"] = drawdown_percent
    if drawdown_percent > -RECOVERY_RESET_PERCENT and _symbol_campaign(symbol):
        print(f"[{symbol}] Drawdown recovered below {RECOVERY_RESET_PERCENT:.2f}%; "
              "rearm BUY campaign.")
        _save_symbol_campaign(symbol, {})
    sell_trigger_price = minimum_price * (1 + threshold_percent / 100.0)
    print(
        f"[{symbol}] price={current_price:.8f}, window_min/max="
        f"{minimum_price:.8f}/{maximum_price:.8f}, minutes_back={minutes_back:.1f}, "
        f"growth_from_min={growth_percent:.4f}%, "
        f"drawdown_from_max={drawdown_percent:.4f}%, "
        f"sell_trigger={sell_trigger_price:.8f}"
    )

    if growth_percent >= threshold_percent:
        print(
            f"[{symbol}] Growth threshold reached "
            f"({growth_percent:.4f}% >= {threshold_percent}%). Selling this asset..."
        )
        return sell_asset(symbol, current_price=current_price)

    if drawdown_percent <= -BUY_TIERS[0][0]:
        print(
            f"[{symbol}] Drawdown threshold reached ({drawdown_percent:.4f}% "
            f"<= -{BUY_TIERS[0][0]:.4f}%). Checking staged BUY..."
        )
        provider = mkt.provider_by_name("binance")
        free_cash = (_finite_float(provider.free_balance("USDC"), positive=True)
                     if provider else None)
        if free_cash is None:
            print(f"[{symbol}] No valid USDC balance for staged BUY.")
            return False
        tier, campaign = _campaign_tier(
            symbol, abs(drawdown_percent), maximum_row, free_cash)
        if tier is None:
            print(f"[{symbol}] No uncompleted BUY tier at current drawdown.")
            return False
        threshold, allocation = tier
        evaluation["pending_tier"] = True
        initial_cash = _finite_float(campaign.get("initial_cash"), positive=True)
        if initial_cash is None:
            print(f"[{symbol}] Invalid campaign initial cash; reset fail-closed.")
            _save_symbol_campaign(symbol, {})
            return False
        cash_amount = initial_cash * BUY_USE_CASH_RATIO * allocation
        print(f"[{symbol}] BUY tier -{threshold:.2f}% allocation={allocation:.3f} "
              f"cash_target={cash_amount:.6f} USDC")
        if buy_with_all_cash(buy_symbol=symbol, cash_amount=cash_amount):
            _complete_campaign_tier(symbol, campaign, threshold)
            evaluation["pending_tier"] = False
            return True

    return False


def evaluate_and_maybe_sell_or_buy(
    threshold_percent=TARGET_GROWTH_PERCENT,
    minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT,
    symbols=None,
):
    """Evaluate configured assets independently, accepting at most one order/cycle."""
    selected = tuple(symbols) if symbols is not None else TRACKED_SYMBOLS
    for symbol in selected:
        if evaluate_symbol(
                symbol, threshold_percent=threshold_percent, minutes_back=minutes_back):
            return True
    return False


def run_forever():
    print(
        f" Started. check_interval={CHECK_INTERVAL_SECONDS}s, "
        f"sell_threshold={TARGET_GROWTH_PERCENT}%, buy_tiers={BUY_TIERS}, "
        f"minutes_back={ASSET_REFERENCE_MINUTES_BACK_DEFAULT}m, "
        f"symbols={TRACKED_SYMBOLS}"
    )
    while True:
        try:
            evaluate_and_maybe_sell_or_buy(minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT)
        except Exception as e:
            print(f" Runtime ERROR: {e}")
        sleep_seconds = _next_check_seconds()
        # Flushing at the cycle boundary publishes all preceding lines when stdout
        # is redirected by flota_start.sh, preventing a quiet bot from appearing stale.
        print(f"[DEBUG] sleep {sleep_seconds}s before next cycle", flush=True)
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    run_forever()
