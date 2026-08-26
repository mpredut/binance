import os
import hashlib
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


def _required_config(name):
    """Return one mandatory AG setting or abort startup on missing/empty input."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        raise ValueError(f"Configuratie AssetGuardian lipsa sau goala: {name}")
    return str(raw).strip()


def _required_float_config(name):
    raw = _required_config(name)
    try:
        return float(raw)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"Configuratie AssetGuardian numerica invalida: {name}={raw!r}") from exc


# Every financial/operational parameter is mandatory in assetguardian_config.env.
# There are deliberately no hidden code defaults: a missing key stops startup.
CHECK_INTERVAL_SECONDS = _required_float_config("AG_CHECK_INTERVAL_SEC")
ASSET_REFERENCE_MINUTES_BACK_DEFAULT = _required_float_config(
    "AG_REFERENCE_MINUTES_BACK")
BUY_USE_CASH_RATIO = _required_float_config("AG_BUY_USE_CASH_RATIO")
BUY_TIERS_RAW = _required_config("AG_BUY_TIERS")
SELL_TIERS_RAW = _required_config("AG_SELL_TIERS")
SELL_REARM_GROWTH_PERCENT = _required_float_config(
    "AG_SELL_REARM_GROWTH_PCT")
SELL_ORDER_MAX_AGE_SECONDS = _required_float_config(
    "AG_SELL_ORDER_MAX_AGE_SEC")
TRACKED_SYMBOLS_RAW = _required_config("AG_SYMBOLS")
RECOVERY_RESET_PERCENT = _required_float_config("AG_RECOVERY_RESET_PCT")
NEAR_TRIGGER_SECONDS = _required_float_config("AG_NEAR_TRIGGER_SEC")
ACTIVE_TRIGGER_SECONDS = _required_float_config("AG_ACTIVE_TRIGGER_SEC")
NEAR_TRIGGER_DISTANCE_PCT = _required_float_config(
    "AG_NEAR_TRIGGER_DISTANCE_PCT")


def _parse_tiers(raw):
    tiers = []
    for item in str(raw).split(","):
        threshold, allocation = item.strip().split(":", 1)
        tiers.append((float(threshold), float(allocation)))
    tiers.sort()
    return tuple(tiers)


def _parse_symbols(raw):
    return tuple(dict.fromkeys(
        item.strip().upper() for item in str(raw).split(",") if item.strip()))


BUY_TIERS = _parse_tiers(BUY_TIERS_RAW)
SELL_TIERS = _parse_tiers(SELL_TIERS_RAW)
TRACKED_SYMBOLS = _parse_symbols(TRACKED_SYMBOLS_RAW)
LEGACY_BUY_SYMBOL = "BTCUSDC"
PENDING_ORDER_MISSING_CONFIRMATIONS = 2
STATE = AssetGuardianState()
_last_evaluation = {
    symbol: {"growth": None, "drawdown": None, "pending_tier": False}
    for symbol in TRACKED_SYMBOLS
}
_local_price_samples = {symbol: [] for symbol in TRACKED_SYMBOLS}


def _validate_config():
    if not math.isfinite(CHECK_INTERVAL_SECONDS) or CHECK_INTERVAL_SECONDS <= 0:
        raise ValueError("AG_CHECK_INTERVAL_SEC trebuie sa fie > 0")
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
    if not SELL_TIERS or any(
            not math.isfinite(threshold) or threshold <= 0
            or not math.isfinite(allocation) or allocation <= 0
            for threshold, allocation in SELL_TIERS):
        raise ValueError("AG_SELL_TIERS trebuie sa contina prag:alocare pozitive")
    if len({threshold for threshold, _ in SELL_TIERS}) != len(SELL_TIERS):
        raise ValueError("AG_SELL_TIERS contine praguri duplicate")
    if not math.isclose(
            sum(allocation for _, allocation in SELL_TIERS), 1.0,
            rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("suma ponderilor AG_SELL_TIERS trebuie sa fie exact 1")
    if (not math.isfinite(SELL_REARM_GROWTH_PERCENT)
            or SELL_REARM_GROWTH_PERCENT <= 0
            or SELL_REARM_GROWTH_PERCENT >= SELL_TIERS[0][0]):
        raise ValueError(
            "AG_SELL_REARM_GROWTH_PCT trebuie sa fie > 0 si sub primul prag SELL")
    if (not math.isfinite(SELL_ORDER_MAX_AGE_SECONDS)
            or SELL_ORDER_MAX_AGE_SECONDS <= 0):
        raise ValueError("AG_SELL_ORDER_MAX_AGE_SEC trebuie sa fie finit si > 0")
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


def sell_asset(symbol, qty, current_price, client_order_id, tier_threshold):
    """Submit one explicit SELL tier and return the native order response."""
    try:
        base_asset, _ = resolve_assets(symbol)
        provider = mkt.provider_by_name("binance")
        if provider is None:
            print(f"[{symbol}] Binance provider unavailable; skip SELL fail-closed.")
            return None
        free_qty = _finite_float(provider.free_balance(base_asset), positive=True)
        requested_qty = _finite_float(qty, positive=True)
        price = _finite_float(current_price, positive=True)
    except Exception as e:
        print(f"ERROR preparing SELL for {symbol}: {e}")
        return None
    if free_qty is None:
        print(f"[{symbol}] No valid free {base_asset} balance; skip SELL.")
        return None
    if requested_qty is None or price is None:
        print(f"[{symbol}] Invalid SELL price/quantity; skip SELL.")
        return None
    qty_to_sell = min(requested_qty, free_qty)
    if qty_to_sell <= 0:
        print(f"[{symbol}] SELL tier has no reconciled free quantity.")
        return None
    try:
        order = mkt.place(
            symbol, "SELL", price, qty_to_sell,
            force=False, smart=False, caller_owns_retry=True,
            bypass_quantity_policy=True,
            client_order_id=client_order_id,
            motivation=f"assetguardian_growth_exit_tier_{tier_threshold:g}")
        if order:
            print(
                f" SELL tier submitted: {symbol} requested={requested_qty:.8f} "
                f"free={free_qty:.8f} submitted_cap={qty_to_sell:.8f} "
                f"clientOrderId={client_order_id}")
            return order
        print(f" SELL tier not accepted: {symbol} qty={qty_to_sell}")
        return None
    except Exception as e:
        print(f"ERROR selling {symbol}: {e}")
        return None


def buy_with_all_cash(buy_symbol, cash_ratio=BUY_USE_CASH_RATIO,
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
            bypass_profit_reference=True,
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
    """Load v3 BUY/SELL state and conservatively migrate older BUY-only data."""
    raw = STATE.load()
    if not isinstance(raw, dict):
        raw = {}
    symbol_rows = raw.get("symbols")
    if isinstance(symbol_rows, dict):
        normalized = {}
        for symbol, value in symbol_rows.items():
            if not isinstance(value, dict):
                continue
            if "buy" in value or "sell" in value:
                buy = value.get("buy")
                sell = value.get("sell")
                row = {}
                if isinstance(buy, dict) and buy:
                    row["buy"] = dict(buy)
                if isinstance(sell, dict) and sell:
                    row["sell"] = dict(sell)
            else:
                # Version 2 stored the BUY campaign directly under the symbol.
                row = {"buy": dict(value)} if value else {}
            if row:
                normalized[str(symbol)] = row
        return {
            "version": 3,
            "symbols": normalized,
        }
    legacy_keys = {"peak_value", "peak_ts", "initial_cash", "completed_tiers"}
    if legacy_keys.intersection(raw):
        return {
            "version": 3,
            "symbols": {LEGACY_BUY_SYMBOL: {"buy": dict(raw)}},
        }
    return {"version": 3, "symbols": {}}


def _symbol_campaign(symbol, kind):
    row = _load_state_root()["symbols"].get(symbol, {})
    campaign = row.get(kind, {}) if isinstance(row, dict) else {}
    return dict(campaign) if isinstance(campaign, dict) else {}


def _save_symbol_campaign(symbol, kind, campaign):
    if kind not in {"buy", "sell"}:
        raise ValueError(f"campanie AssetGuardian necunoscuta: {kind}")
    root = _load_state_root()
    symbols_state = dict(root["symbols"])
    row = dict(symbols_state.get(symbol, {}))
    if campaign:
        row[kind] = dict(campaign)
    else:
        row.pop(kind, None)
    if row:
        symbols_state[symbol] = row
    else:
        symbols_state.pop(symbol, None)
    STATE.save({"version": 3, "symbols": symbols_state})


def _campaign_tier(symbol, drawdown_abs, maximum_row, free_cash):
    """Select one crossed, incomplete BUY tier for one symbol campaign."""
    state = _symbol_campaign(symbol, "buy")
    if drawdown_abs < RECOVERY_RESET_PERCENT:
        if state:
            _save_symbol_campaign(symbol, "buy", {})
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
        _save_symbol_campaign(symbol, "buy", state)
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
        _save_symbol_campaign(symbol, "buy", state)
    elif peak_price > stored_peak:
        # A higher peak within the same unrecovered campaign changes the
        # reference, but must never clear completed tiers or replenish budget.
        state = dict(state)
        state["peak_price"] = peak_price
        state["peak_ts"] = peak_ts
        _save_symbol_campaign(symbol, "buy", state)

    completed = _completed_tier_values(state)
    for threshold, allocation in BUY_TIERS:
        if drawdown_abs >= threshold and threshold not in completed:
            return (threshold, allocation), state
    return None, state


def _complete_campaign_tier(symbol, kind, state, threshold):
    completed = _completed_tier_values(state)
    completed.add(float(threshold))
    state = dict(state)
    state["completed_tiers"] = sorted(completed)
    _save_symbol_campaign(symbol, kind, state)


def _tier_key(threshold):
    return f"{float(threshold):g}"


def _sell_client_order_id(symbol, state, threshold, attempt):
    raw = (
        f"assetguardian:sell:{symbol}:{state.get('trough_ts')}:"
        f"{float(threshold):g}:{int(attempt)}")
    return "AGS" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:29]


def _record_terminal_sell(symbol, state, status):
    """Apply one terminal venue status without inferring acceptance as a fill."""
    pending = dict(state.get("pending") or {})
    threshold = _finite_float(pending.get("threshold"), positive=True)
    if threshold is None:
        print(f"[{symbol}] Corrupt SELL pending tier; preserve fail-closed.")
        return state

    key = _tier_key(threshold)
    filled_by_tier = dict(state.get("filled_qty_by_tier") or {})
    prior_filled = _finite_float(filled_by_tier.get(key)) or 0.0
    filled_qty = _finite_float(status.filled_qty) or 0.0
    filled_by_tier[key] = prior_filled + max(0.0, filled_qty)

    state = dict(state)
    state["filled_qty_by_tier"] = filled_by_tier
    state["total_filled_qty"] = (
        (_finite_float(state.get("total_filled_qty")) or 0.0)
        + max(0.0, filled_qty))
    if status.status == "closed" and filled_qty > 0:
        completed = _completed_tier_values(state)
        completed.add(threshold)
        state["completed_tiers"] = sorted(completed)

    terminal = list(state.get("terminal_orders") or [])[-19:]
    terminal.append({
        "order_id": pending.get("order_id"),
        "client_order_id": pending.get("client_order_id"),
        "threshold": threshold,
        "status": status.status,
        "filled_qty": filled_qty,
        "cost": _finite_float(status.cost) or 0.0,
        "fee": _finite_float(status.fee) or 0.0,
        "terminal_ts": time.time(),
    })
    state["terminal_orders"] = terminal
    state.pop("pending", None)
    _save_symbol_campaign(symbol, "sell", state)
    print(
        f"[{symbol}] SELL tier terminal: threshold={threshold:g}% "
        f"status={status.status} orderId={pending.get('order_id')} "
        f"filled={filled_qty:.8f} cost={float(status.cost):.8f} "
        f"fee={float(status.fee):.8f}")
    return state


def _reconcile_pending_sell(symbol, state):
    """Return ``active``/``terminal``/``none`` after querying venue truth."""
    pending = state.get("pending")
    if not isinstance(pending, dict) or not pending:
        return state, "none"
    pending = dict(pending)
    client_order_id = str(pending.get("client_order_id") or "")
    if not client_order_id:
        print(f"[{symbol}] SELL pending without client_order_id; block fail-closed.")
        return state, "active"

    order_id = pending.get("order_id")
    if order_id is None:
        try:
            native = mkt.order_by_client_id(
                symbol, client_order_id, provider_name="binance")
        except Exception as exc:
            print(f"[{symbol}] SELL lookup by client id failed; keep pending: {exc}")
            return state, "active"
        if native is None:
            misses = int(pending.get("lookup_misses") or 0) + 1
            pending["lookup_misses"] = misses
            state = dict(state)
            state["pending"] = pending
            _save_symbol_campaign(symbol, "sell", state)
            if misses < PENDING_ORDER_MISSING_CONFIRMATIONS:
                print(
                    f"[{symbol}] SELL intent absent at venue ({misses}/"
                    f"{PENDING_ORDER_MISSING_CONFIRMATIONS}); keep pending.")
                return state, "active"
            state.pop("pending", None)
            _save_symbol_campaign(symbol, "sell", state)
            print(
                f"[{symbol}] SELL intent confirmed absent at venue twice; "
                "clear pending without completing tier.")
            return state, "terminal"
        order_id = native.get("orderId") if isinstance(native, dict) else None
        if order_id is None:
            print(f"[{symbol}] SELL lookup response without orderId; keep pending.")
            return state, "active"
        pending["order_id"] = str(order_id)
        pending["lookup_misses"] = 0
        state = dict(state)
        state["pending"] = pending
        _save_symbol_campaign(symbol, "sell", state)

    try:
        status = mkt.order_status(symbol, str(order_id), provider_name="binance")
    except Exception as exc:
        print(f"[{symbol}] SELL order status unavailable; keep pending: {exc}")
        return state, "active"
    if not status.terminal:
        pending["last_status"] = status.status
        pending["filled_qty"] = status.filled_qty
        state = dict(state)
        state["pending"] = pending
        _save_symbol_campaign(symbol, "sell", state)

        created_at = _finite_float(pending.get("created_at"), positive=True)
        now_ts = float(time.time())
        age_seconds = (max(0.0, now_ts - created_at)
                       if created_at is not None else None)
        cancel_attempted = _finite_float(
            pending.get("cancel_attempted_at"), positive=True)
        if (age_seconds is not None
                and age_seconds >= SELL_ORDER_MAX_AGE_SECONDS
                and cancel_attempted is None):
            # Persist before the side effect. Even an ambiguous API error must not
            # generate repeated cancel requests or permit a replacement order.
            pending["cancel_attempted_at"] = now_ts
            state["pending"] = pending
            _save_symbol_campaign(symbol, "sell", state)
            print(
                f"[{symbol}] SELL order exceeded owned TTL: orderId={order_id} "
                f"clientOrderId={client_order_id} age={age_seconds:.1f}s; "
                "request one cancel after fresh status reconciliation.")
            try:
                mkt.cancel_order(
                    symbol, str(order_id), provider_name="binance")
            except Exception as exc:
                print(
                    f"[{symbol}] SELL cancel result ambiguous/failed; keep pending "
                    f"without retrying cancel: orderId={order_id} error={exc}")
                return state, "active"
            try:
                status = mkt.order_status(
                    symbol, str(order_id), provider_name="binance")
            except Exception as exc:
                print(
                    f"[{symbol}] SELL post-cancel status unavailable; keep pending: "
                    f"orderId={order_id} error={exc}")
                return state, "active"
            if status.terminal:
                return _record_terminal_sell(symbol, state, status), "terminal"
            pending["last_status"] = status.status
            pending["filled_qty"] = status.filled_qty
            state["pending"] = pending
            _save_symbol_campaign(symbol, "sell", state)
            print(
                f"[{symbol}] SELL cancel not terminal yet; keep pending: "
                f"orderId={order_id} status={status.status} "
                f"filled={status.filled_qty:.8f}")
            return state, "active"

        if age_seconds is None:
            print(
                f"[{symbol}] SELL pending has no valid created_at; cannot expire "
                "safely and remains fail-closed.")
        print(
            f"[{symbol}] SELL tier still {status.status}: orderId={order_id} "
            f"filled={status.filled_qty:.8f} age={age_seconds}")
        return state, "active"
    return _record_terminal_sell(symbol, state, status), "terminal"


def _active_sell_orders(provider, symbol):
    """Return active SELL orders, or ``None`` when venue truth is unavailable."""
    if provider is None:
        return None
    try:
        orders = provider.open_orders(symbol) or []
    except Exception as exc:
        print(f"[{symbol}] Open-order reconciliation unavailable: {exc}")
        return None
    return [
        order for order in orders
        if isinstance(order, dict)
        and str(order.get("side") or "").upper() == "SELL"
    ]


def _sell_campaign_tier(symbol, current_price, minimum_row, free_qty, provider):
    """Select one incomplete SELL tier against a frozen campaign trough."""
    state = _symbol_campaign(symbol, "sell")
    new_campaign = not bool(state)
    current_price = _finite_float(current_price, positive=True)
    free_qty = _finite_float(free_qty)
    if current_price is None or free_qty is None or free_qty < 0:
        return None, state, None
    if free_qty == 0:
        open_sells = _active_sell_orders(provider, symbol)
        if open_sells is None or open_sells:
            print(f"[{symbol}] Zero free qty with unknown/active SELL orders; preserve state.")
            return None, state, None
        if state:
            _save_symbol_campaign(symbol, "sell", {})
        return None, {}, None

    if state:
        trough_price = _finite_float(state.get("trough_price"), positive=True)
        trough_ts = _finite_float(state.get("trough_ts"), positive=True)
        initial_qty = _finite_float(state.get("initial_qty"), positive=True)
        if trough_price is None or trough_ts is None or initial_qty is None:
            print(f"[{symbol}] Invalid SELL campaign; clear without submitting.")
            _save_symbol_campaign(symbol, "sell", {})
            return None, {}, None
        growth = ((current_price - trough_price) / trough_price) * 100.0
        if growth <= SELL_REARM_GROWTH_PERCENT:
            print(
                f"[{symbol}] SELL campaign rearmed: growth={growth:.4f}% <= "
                f"{SELL_REARM_GROWTH_PERCENT:.4f}% against frozen trough "
                f"{trough_price:.8f}.")
            _save_symbol_campaign(symbol, "sell", {})
            return None, {}, growth
        expected_remaining = max(
            0.0,
            initial_qty - (_finite_float(state.get("total_filled_qty")) or 0.0),
        )
        balance_tolerance = max(1e-12, initial_qty * 1e-6)
        if free_qty > expected_remaining + balance_tolerance:
            print(
                f"[{symbol}] Free balance increased from expected "
                f"{expected_remaining:.8f} to {free_qty:.8f}; rearm SELL campaign "
                "for the reconciled larger position.")
            _save_symbol_campaign(symbol, "sell", {})
            return None, {}, growth
    else:
        trough_price = _finite_float(minimum_row.get("price"), positive=True)
        trough_ts = _finite_float(minimum_row.get("timestamp"), positive=True)
        if trough_price is None or trough_ts is None:
            return None, {}, None
        growth = ((current_price - trough_price) / trough_price) * 100.0
        if growth < SELL_TIERS[0][0]:
            return None, {}, growth
        state = {
            "trough_price": trough_price,
            "trough_ts": trough_ts,
            "initial_qty": free_qty,
            "created_at": time.time(),
            "completed_tiers": [],
            "filled_qty_by_tier": {},
            "total_filled_qty": 0.0,
            "attempts_by_tier": {},
            "terminal_orders": [],
        }
    open_sells = _active_sell_orders(provider, symbol)
    if open_sells is None:
        print(f"[{symbol}] Cannot verify open SELL orders; do not submit tier.")
        return None, ({} if new_campaign else state), growth
    if open_sells:
        print(
            f"[{symbol}] Existing SELL order blocks AssetGuardian tier: "
            f"{[order.get('orderId') for order in open_sells]}")
        return None, ({} if new_campaign else state), growth
    if new_campaign:
        _save_symbol_campaign(symbol, "sell", state)

    completed = _completed_tier_values(state)
    initial_qty = _finite_float(state.get("initial_qty"), positive=True)
    filled_by_tier = dict(state.get("filled_qty_by_tier") or {})
    for threshold, allocation in SELL_TIERS:
        if growth < threshold or threshold in completed:
            continue
        target_qty = initial_qty * allocation
        already_filled = _finite_float(
            filled_by_tier.get(_tier_key(threshold))) or 0.0
        remaining_qty = max(0.0, target_qty - already_filled)
        if remaining_qty <= 0:
            _complete_campaign_tier(symbol, "sell", state, threshold)
            return None, _symbol_campaign(symbol, "sell"), growth
        return (threshold, allocation, remaining_qty), state, growth
    return None, state, growth


def _submit_sell_tier(symbol, current_price, tier, state):
    threshold, allocation, qty = tier
    key = _tier_key(threshold)
    attempts = dict(state.get("attempts_by_tier") or {})
    attempt = int(attempts.get(key) or 0) + 1
    attempts[key] = attempt
    client_order_id = _sell_client_order_id(symbol, state, threshold, attempt)
    pending = {
        "intent_id": client_order_id,
        "client_order_id": client_order_id,
        "threshold": threshold,
        "allocation": allocation,
        "requested_qty": qty,
        "attempt": attempt,
        "created_at": time.time(),
        "lookup_misses": 0,
    }
    state = dict(state)
    state["attempts_by_tier"] = attempts
    state["pending"] = pending
    _save_symbol_campaign(symbol, "sell", state)

    order = sell_asset(
        symbol, qty=qty, current_price=current_price,
        client_order_id=client_order_id, tier_threshold=threshold)
    if not order:
        # The shared guard may have refused before submission, or the venue response
        # may be ambiguous. Preserve the intent and reconcile by deterministic ID.
        return False
    order_id = order.get("orderId") if isinstance(order, dict) else None
    if order_id is not None:
        pending["order_id"] = str(order_id)
    if isinstance(order, dict):
        submitted_qty = _finite_float(order.get("origQty"), positive=True)
        if submitted_qty is not None:
            pending["submitted_qty"] = submitted_qty
        pending["submit_status"] = str(order.get("status") or "accepted")
    state["pending"] = pending
    _save_symbol_campaign(symbol, "sell", state)
    # Immediate reconciliation catches a synchronous FILLED response. A NEW or
    # PARTIALLY_FILLED order remains pending and blocks duplicate tiers.
    _reconcile_pending_sell(symbol, state)
    return True


def _next_check_seconds():
    if any(state.get("pending_tier") for state in _last_evaluation.values()):
        return min(CHECK_INTERVAL_SECONDS, ACTIVE_TRIGGER_SECONDS)
    root = _load_state_root()
    if any(
            isinstance(row.get("sell", {}).get("pending"), dict)
            for row in root["symbols"].values() if isinstance(row, dict)):
        return min(CHECK_INTERVAL_SECONDS, ACTIVE_TRIGGER_SECONDS)
    for symbol, evaluation in _last_evaluation.items():
        drawdown = evaluation.get("drawdown")
        row = root["symbols"].get(symbol, {})
        buy_completed = _completed_tier_values(row.get("buy", {}))
        buy_next = next((threshold for threshold, _ in BUY_TIERS
                         if threshold not in buy_completed), None)
        if (drawdown is not None and buy_next is not None
                and buy_next - max(0.0, -drawdown) <= NEAR_TRIGGER_DISTANCE_PCT):
            return min(CHECK_INTERVAL_SECONDS, NEAR_TRIGGER_SECONDS)
        growth = evaluation.get("growth")
        sell_completed = _completed_tier_values(row.get("sell", {}))
        sell_next = next((threshold for threshold, _ in SELL_TIERS
                          if threshold not in sell_completed), None)
        if (growth is not None and sell_next is not None
                and sell_next - max(0.0, growth) <= NEAR_TRIGGER_DISTANCE_PCT):
            return min(CHECK_INTERVAL_SECONDS, NEAR_TRIGGER_SECONDS)
    return CHECK_INTERVAL_SECONDS


def evaluate_symbol(symbol, minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT):
    """Evaluate and possibly submit exactly one asset-specific SELL or BUY."""
    evaluation = _last_evaluation.setdefault(
        symbol, {"growth": None, "drawdown": None, "pending_tier": False})
    evaluation.update(growth=None, drawdown=None, pending_tier=False)
    if symbol not in TRACKED_SYMBOLS:
        print(f"[{symbol}] Invalid AssetGuardian symbol; skip evaluation.")
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

    rolling_growth_percent = ((current_price - minimum_price) / minimum_price) * 100.0
    drawdown_percent = ((current_price - maximum_price) / maximum_price) * 100.0
    evaluation["drawdown"] = drawdown_percent
    if drawdown_percent > -RECOVERY_RESET_PERCENT and _symbol_campaign(symbol, "buy"):
        print(f"[{symbol}] Drawdown recovered below {RECOVERY_RESET_PERCENT:.2f}%; "
              "rearm BUY campaign.")
        _save_symbol_campaign(symbol, "buy", {})

    provider = mkt.provider_by_name("binance")
    sell_state = _symbol_campaign(symbol, "sell")
    if sell_state.get("pending"):
        sell_state, pending_outcome = _reconcile_pending_sell(symbol, sell_state)
        if pending_outcome == "active":
            evaluation["pending_tier"] = True
            return False
        if pending_outcome == "terminal":
            # Cool down one evaluator cycle after terminal reconciliation. This
            # prevents a cancel/partial terminal from being resubmitted immediately.
            return False

    try:
        base_asset, _ = resolve_assets(symbol)
        free_base_qty = (provider.free_balance(base_asset)
                         if provider is not None else None)
    except Exception as exc:
        print(f"[{symbol}] SELL balance unavailable; skip SELL fail-closed: {exc}")
        free_base_qty = None
    sell_tier, sell_state, effective_growth = _sell_campaign_tier(
        symbol, current_price, minimum_row, free_base_qty, provider)
    evaluation["growth"] = (
        rolling_growth_percent if effective_growth is None else effective_growth)
    frozen_trough = _finite_float(sell_state.get("trough_price"), positive=True)
    print(
        f"[{symbol}] price={current_price:.8f}, window_min/max="
        f"{minimum_price:.8f}/{maximum_price:.8f}, minutes_back={minutes_back:.1f}, "
        f"rolling_growth_from_min={rolling_growth_percent:.4f}%, "
        f"sell_growth={evaluation['growth']:.4f}%, "
        f"frozen_sell_trough={frozen_trough}, "
        f"drawdown_from_max={drawdown_percent:.4f}%"
    )

    if sell_tier is not None:
        threshold, allocation, qty = sell_tier
        print(
            f"[{symbol}] SELL tier +{threshold:.2f}% allocation={allocation:.3f} "
            f"target_remaining={qty:.8f} base."
        )
        evaluation["pending_tier"] = True
        accepted = _submit_sell_tier(
            symbol, current_price=current_price, tier=sell_tier, state=sell_state)
        if accepted:
            evaluation["pending_tier"] = bool(
                _symbol_campaign(symbol, "sell").get("pending"))
            return True
        return False

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
            _save_symbol_campaign(symbol, "buy", {})
            return False
        cash_amount = initial_cash * BUY_USE_CASH_RATIO * allocation
        print(f"[{symbol}] BUY tier -{threshold:.2f}% allocation={allocation:.3f} "
              f"cash_target={cash_amount:.6f} USDC")
        if buy_with_all_cash(buy_symbol=symbol, cash_amount=cash_amount):
            _complete_campaign_tier(symbol, "buy", campaign, threshold)
            evaluation["pending_tier"] = False
            return True

    return False


def evaluate_and_maybe_sell_or_buy(
    minutes_back=ASSET_REFERENCE_MINUTES_BACK_DEFAULT,
    symbols=None,
):
    """Evaluate configured assets independently, accepting at most one order/cycle."""
    selected = tuple(symbols) if symbols is not None else TRACKED_SYMBOLS
    for symbol in selected:
        if evaluate_symbol(symbol, minutes_back=minutes_back):
            return True
    return False


def run_forever():
    print(
        f" Started. check_interval={CHECK_INTERVAL_SECONDS}s, "
        f"sell_tiers={SELL_TIERS}, sell_rearm={SELL_REARM_GROWTH_PERCENT}%, "
        f"sell_order_max_age={SELL_ORDER_MAX_AGE_SECONDS}s, "
        f"buy_tiers={BUY_TIERS}, "
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
