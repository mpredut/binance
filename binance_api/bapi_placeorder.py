import os
import time
import datetime
import math
import sys
from datetime import datetime, timedelta

import signal
import asyncio
#import threading
#from threading import Thread
import json

####Binance
import binance
print(binance.__version__)
from binance.exceptions import BinanceAPIException


####MYLIB
import utils as u
import order_guard
import symbols as sym
import config as cfg
import priceAnalysis as pa
from . import order_id_context as rc   # client_order_id and tag context moved into binance_api.

from . import bapi as api
from .bapi_client import client
from lock import trade_cooldown   # Rapid-fire gate moved into the lock package.

# Load versioned, non-secret tuning parameters before reading the environment below.
# ``botcore.load_dotenv`` never overwrites variables already set in the real environment;
# it only fills missing values, matching tradeall_config.env and monitortrades_config.env.
from botcore import load_dotenv as _load_dotenv, required_float_env, required_int_env
_load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "bapi_placeorder_config.env"))

# Order policy is mandatory; missing configuration aborts startup.
PLACE_ORDER_FEE_PCT = required_float_env("PLACE_ORDER_FEE_PCT")
PLACE_ORDER_HOURS = required_int_env("PLACE_ORDER_HOURS")
PLACE_ORDER_SAFEBACK_SEC = required_int_env("PLACE_ORDER_SAFEBACK_SEC")
PLACE_ORDER_MAX_DAILY_TRADES = required_int_env("PLACE_ORDER_MAX_DAILY_TRADES")


class WeightLimitBlock(Exception):
    """Raised when the 24-hour trade limit makes retrying pointless."""
    pass


def _resolve_qty(qty):
    """Normalize the quantity model used by the placement pipeline.

    ``qty=None`` means use the maximum allowed by weight limits and the real-balance
    clamp rather than an arbitrary numeric placeholder. An explicit quantity remains
    unchanged here; downstream safety guards may still cap it without changing the
    caller's stated intent.
    """
    return float("inf") if qty is None else qty


def _fresh_price(symbol):
    """Return the freshest WS price, falling back to ``bapi.get_current_price``."""
    try:
        import cacheManager as cm
        p = cm.get_current_price_manager().get_price_value(symbol)
        if p is not None:
            return p
    except Exception:
        pass
    return api.get_current_price(symbol)



def apply_weight_limit(symbol, order_type, price, required_qty, available_qty):
    from . import bapi_allorders as apiorders
    required_qty = _resolve_qty(required_qty)
    try:
        # Obtain the weight from permission analysis.
        weight = pa.get_weight_for_cash_permission_at_quant_time(symbol, order_type)
        if weight is None or math.isnan(weight):
            print("Weight is None, set it at default 0.03")
            weight = 0.03

        # 2. Calculate already-traded quote value over the previous 24 hours.
        stats = apiorders.get_total_traded_stats(symbol)
        traded_value = stats.get(order_type.upper(), {}).get('total_value', 0)

        # 3. Calculate total tradable value: already traded plus available.
        total_value_reference = traded_value + available_qty * price
        # 4. Calculate the weight-based maximum quote allowance.
        max_trade_value = total_value_reference * weight
        #max_trade_value = available_qty * price * weight

        # 5. Calculate the remaining tradable quote value in USDC.
        remaining_trade_value = max(0, max_trade_value - traded_value)

        # Convert the maximum allowance to base-asset quantity.
        remaining_trade_qty = remaining_trade_value / price if price else 0

        # Select the smaller of requested and permitted quantity.
        adjusted_qty = min(required_qty, remaining_trade_qty)

        print(f"apply_weight_limit → {order_type} {symbol}, "
              f"Available qty {available_qty:.8f}, "
              f"Weight {weight}, "
              f"Traded in 24h {traded_value:.2f} USDC, "
              f"Max trade allowed (24h): {max_trade_value:.2f} USDC, "
              f"Remaining: {remaining_trade_value:.2f} USDC, "
              f"Required qty: {required_qty:.8f}, "
              f"Final qty: {adjusted_qty:.8f}")


        return adjusted_qty

    except Exception as e:
        print(f"apply_weight_limit: Error: {e}, order_type {order_type} and {symbol}")
        return required_qty

def place_BUY_order(symbol, price, qty, client_order_id=None):
    try:
        if not cfg.is_trade_enabled() :
            print(f"Trade is desabled!")
            return None

        price = round(min(price, _fresh_price(symbol)), 2)
        qty = round(qty, 4)
        client_order_id = client_order_id or rc.create_client_order_id()
        BUY_order = client.order_limit_buy(
            symbol=symbol,
            quantity=qty,
            price=str(price),
            newClientOrderId=client_order_id
        )

        if BUY_order:
            print(f"BUY order placed successfully: {BUY_order['orderId']} clientId {client_order_id}")
        else :
            print(f"Eroare la plasarea ordinului de BUY")
        
        return BUY_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de cumparare: {e}")
        return None

def place_SELL_order(symbol, price, qty, client_order_id=None):
    try:
        if not cfg.is_trade_enabled() :
            print(f"Trade is disabled!")
            return None

        price = round(max(price, _fresh_price(symbol)), 2)
        qty = round(qty, 4)
        client_order_id = client_order_id or rc.create_client_order_id()
        SELL_order = client.order_limit_sell(
            symbol=symbol,
            quantity=qty,
            price=str(price),
            newClientOrderId=client_order_id
        )

        if SELL_order:
            print(f"SELL order placed successfully: {SELL_order['orderId']} clientId {client_order_id}")
        else :
            print(f"Eroare la plasarea ordinului de SELL")
        
        return SELL_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de vanzare: {e}")
        return None


def place_SELL_BUY_order(order_type, symbol, price, qty) :
 
    if not cfg.is_trade_enabled():
        print(f"Trade este dezactivat!")
        return None
    
    order = None
    client_order_id = rc.create_client_order_id()
    if order_type == "BUY":
        order = client.order_limit_buy(
            symbol=symbol,
            quantity=qty,
            price=str(price),
            newClientOrderId=client_order_id
        )
    elif order_type == "SELL":
        order = client.order_limit_sell(
            symbol=symbol,
            quantity=qty,
            price=str(price),
            newClientOrderId=client_order_id
        )

    if order:
        print(f"{order_type} order placed successfully: {order['orderId']} clientId {client_order_id}")
    else :
        print(f"Eroare la plasarea ordinului de {order_type}, pret {price:.2f}")
    return order

def place_BUY_order_at_market(symbol, qty, client_order_id=None):
    try:
        if not cfg.is_trade_enabled():
            print(f"Trade este dezactivat!")
            return None

        qty = round(qty, 4)  # Round quantity to four decimal places.
        client_order_id = client_order_id or rc.create_client_order_id()
        BUY_order = client.order_market_buy(
            symbol=symbol,
            quantity=qty,
            newClientOrderId=client_order_id
        )

        if BUY_order:
            print(f"BUY order de market executat cu succes: {BUY_order['orderId']} clientId {client_order_id}")
        else:
            print(f"Eroare la plasarea ordinului de BUY de market")
        
        return BUY_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de market de cumparare: {e}")
        return None


def place_SELL_order_at_market(symbol, qty, client_order_id=None):
    try:
        if not cfg.is_trade_enabled():
            print(f"Trade este dezactivat!")
            return None

        qty = round(qty, 4)  # Round quantity to four decimal places.
        client_order_id = client_order_id or rc.create_client_order_id()
        SELL_order = client.order_market_sell(
            symbol=symbol,
            quantity=qty,
            newClientOrderId=client_order_id
        )

        if SELL_order:
            print(f"SELL order de market executat cu succes: {SELL_order['orderId']} clientId {client_order_id}")
        else:
            print(f"Eroare la plasarea ordinului de SELL de market")
        
        return SELL_order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de market de vanzare: {e}")
        return None


def _last_opposite_fill_price(symbol, order_type):
    """Return the latest opposite fill price without a time limit.

    BUY uses the latest SELL fill and SELL uses the latest BUY fill. Return ``None``
    only when the cache is healthy but has no opposite fill. Raise when the manager or
    cache is unavailable so the caller can fail closed. CacheTradeManager supplies real
    WebSocket fills without an API call.
    """
    import cacheManager as cm
    return cm.get_cache_manager("Trade").last_opposite_fill_price(symbol, order_type)


def _last_opposite_fill_price_api(symbol, order_type):
    """Query ``get_my_trades`` when the cache has no opposite fill.

    This covers a new symbol or an unpopulated cache. Raise on API errors so the caller
    fails closed; return ``None`` only when Binance confirms no opposite fill exists.
    """
    want_buyer = (order_type.upper() == "SELL")   # The opposite of SELL is BUY (isBuyer=True).
    for tr in reversed(client.get_my_trades(symbol=symbol, limit=200)):
        if tr["isBuyer"] == want_buyer:
            return float(tr["price"])
    return None


def if_place_safe_order(order_type, symbol, price, qty, time_back_in_seconds,
                        bypass_profit_guard=False):
    # Removed dead max_daily_trades and profit_percentage parameters. The only real caller
    # always supplied these exact configured values, now read here as the source of truth.
    # ``bypass_profit_guard=True`` skips profit/history and fail-closed checks, unlike
    # ``force``, which only selects MARKET execution. Daily limits and anti-spam remain.
    # The crash circuit breaker uses both flags; normal trading keeps this bypass disabled.
    #import bapi_trades as apitrades
    from . import bapi_allorders as apiorders
    from providers.market_api import BinanceProvider

    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, price, qty)
    #apitrades.compare_trade_sources(symbol, order_type=order_type, max_age_seconds=time_back_in_seconds, limit=1000)
    provider = BinanceProvider()

    try:

        current_price = api.get_current_price(symbol)

        if order_type == "BUY":
            price = round(min(price, current_price), 0)
        else:  # SELL
            price = round(max(price, current_price), 0)

        qty = round(qty, 4)

        opposite_order_type = "SELL" if order_type == "BUY" else "BUY"
        backdays = math.ceil(time_back_in_seconds / 86400)

        # Delegate daily limits and anti-spam to order_guard.daily_limit_guard, removing
        # a duplicate of the provider-neutral logic. BinanceProvider.get_orders wraps
        # the same apiorders data source. Keep Binance's own limit and lookback settings
        # instead of introducing a second potentially divergent order_guard knob.
        ok, reason = order_guard.daily_limit_guard(
            provider, symbol, order_type,
            max_daily_trades=PLACE_ORDER_MAX_DAILY_TRADES,
            safeback_sec=time_back_in_seconds)
        if not ok:
            return False, reason

        oposite_trades = apiorders.get_trade_orders(opposite_order_type, symbol, max_age_seconds=time_back_in_seconds)  # current data
        print(f"Am {len(oposite_trades)} trades de tip {opposite_order_type} pentru {backdays} zile. ")

        time_limit = float(time.time() * 1000) - (time_back_in_seconds * 1000)  # in milisecunde
        # Keep opposite trades in the requested interval. Requiring price > 0 remains a
        # defensive safety net even though canceled orders no longer enter the cache.
        recent_opposite_trades = [trade for trade in oposite_trades
                                  if float(trade['timestamp']) >= float(time_limit)
                                  and float(trade.get('price', 0)) > 0]
        print(f"Ma raportrez doar la cele care sunt cu {time_back_in_seconds} sec. back , in numar de '{len(recent_opposite_trades)}'")
        for trade in recent_opposite_trades:
            readable = datetime.fromtimestamp(trade['timestamp'] / 1000)
            print(f"[CHECK] {readable} - price: {trade['price']} - included: {float(trade['timestamp']) >= time_limit}")
        
        # Provider-neutral profit guard. Reference priority is the window's minimum SELL
        # or maximum BUY, then the provider's latest opposite fill. The bypass skips both;
        # read errors reach the handler below and fail closed.
        if not bypass_profit_guard:
            window_ref = None
            if recent_opposite_trades:                       # Primary time-window reference.
                _prices = [float(t['price']) for t in recent_opposite_trades]
                window_ref = min(_prices) if order_type == "BUY" else max(_prices)
            # Resolve the configured margin lazily only when the guard is used. Reuse the
            # stateless provider already created for the daily-limit guard.
            if not order_guard.profit_guard(provider, symbol, order_type, price,
                                            order_guard.margin_for("binance"), window_ref=window_ref):
                return False, "profit_guard"
        return True, None

    except BinanceAPIException as e:
        print(f"Eroare la verificare if place safe order {order_type}: {e}")
        return False, "guard_check_api_exception"
    except Exception as e:
        # Data or manager errors fail closed unless the crash circuit breaker explicitly bypasses.
        print(f"[GARD] {order_type} {symbol}: verificare esuata ({e}) -> "
              f"{'TREC (bypass)' if bypass_profit_guard else 'BLOCAT (fail-closed)'}")
        return bool(bypass_profit_guard), (None if bypass_profit_guard else "guard_check_failed")


from decimal import Decimal, ROUND_DOWN


def _guarded_market_place(symbol, order_type, price, qty, **kwargs):
    """Import lazily to avoid the bapi_placeorder/market_api cycle.

    This testable choke point routes legacy API orders through the same
    ``Instrument.place`` pipeline used by rtrade, tradeall, and other providers.
    """
    from providers.market_api import api as market_api
    return market_api.place(symbol, order_type, price, qty, **kwargs)


def place_safe_order(order_type, symbol, price, qty=None,
                     safeback_seconds=PLACE_ORDER_SAFEBACK_SEC, force=False,
                     cancelorders=False, hours=PLACE_ORDER_HOURS,
                     bypass_profit_guard=False, _reason_out=None):
    """Adapt the compatible safe API to the common pipeline without smart repricing."""
    order_type = order_type.upper()
    qty = _resolve_qty(qty)
    sym.validate_params(order_type, symbol, price, qty)
    order = _guarded_market_place(
        symbol, order_type, price, qty,
        smart=False,
        safeback_seconds=safeback_seconds,
        force=force,
        cancelorders=cancelorders,
        hours=hours,
        bypass_profit_guard=bypass_profit_guard,
    )
    # The common pipeline logs the exact reason. Retain dict compatibility without
    # duplicating guard evaluation in the legacy chain.
    if order is None and _reason_out is not None:
        _reason_out.setdefault("reason", "common_pipeline_refused")
    return order
    

# The fleet-wide journal lives in order_outcomes_log.py as a single source used by
# Instrument.place for every provider. Re-export the directory for backward compatibility.
import order_outcomes_log as _outcomes_log
ORDER_OUTCOMES_LOG_DIR = _outcomes_log.ORDER_OUTCOMES_LOG_DIR


def _log_order_outcome(symbol, side, price, qty, outcome, refuse_reason, motivation):
    """Write one pipe-delimited fleet-wide record per placement attempt.

    This is observational and cannot affect the caller's return value because the
    logging implementation contains its own exception boundary.
    """
    try:
        caller = os.path.basename(sys._getframe(2).f_code.co_filename)
    except Exception:
        caller = None
    _outcomes_log.log_order_outcome(symbol, side, price, qty, outcome, refuse_reason,
                                    motivation, caller=caller)


# Retain ``pair`` only for compatibility with callers that explicitly pass it. It is
# intentionally ignored legacy code; do not remove it without updating those callers.
def place_order_smart(order_type, symbol, price, qty=None, safeback_seconds=PLACE_ORDER_SAFEBACK_SEC, force=False, cancelorders=True, hours=PLACE_ORDER_HOURS, pair=None, motivation=None):
    order_type = order_type.upper()
    qty = _resolve_qty(qty)
    sym.validate_params(order_type, symbol, price, qty)
    return _guarded_market_place(
        symbol, order_type, price, qty,
        smart=True,
        safeback_seconds=safeback_seconds,
        force=force,
        cancelorders=cancelorders,
        hours=hours,
        motivation=motivation,
    )


# ============================================================================
# Binance placement mechanics called through Instrument.place and BinanceProvider.
# These functions contain only venue-specific price adjustment, opposite-order cleanup,
# fee/balance and minimum-notional clamps, and limit/market dispatch. Daily limits,
# profit/weight/trend/cooldown guards, and journaling belong to the agnostic layer.
# ============================================================================

def adjust_price_and_cancel_opposite(order_type, symbol, price, cancel_opposite=True):
    """Apply Binance price mechanics and optionally cancel adverse opposite orders.

    Cancel SELL below a BUY price or BUY above a SELL price, then clamp to current
    price with a rounded 0.1% nudge. Instrument.place runs this before the profit guard
    so the guard sees the same price as the legacy chain.
    """
    order_type = order_type.upper()
    current_price = api.get_current_price(symbol)
    if order_type == "BUY":
        if cancel_opposite:
            open_SELL_orders = api.get_open_orders("SELL", symbol)
            for order_id, order_details in open_SELL_orders.items():
                if order_details['price'] < price:
                    if not api.cancel_order(symbol, order_id):
                        print(f"Fail cancel order {order_id} prep. for BUY (low SELL price).")
        price = min(price, current_price)
        price = round(price * 0.999, 0)
    elif order_type == "SELL":
        if cancel_opposite:
            open_BUY_orders = api.get_open_orders("BUY", symbol)
            for order_id, order_details in open_BUY_orders.items():
                if order_details['price'] > price:
                    if not api.cancel_order(symbol, order_id):
                        print(f"Fail cancel order {order_id} prep. for SELL (high BUY price).")
        price = max(price, current_price)
        price = round(price * (1 + 0.001), 0)
    return price


def place_order_mechanics(order_type, symbol, price, qty, force=False,
                          client_order_id=None):
    """Execute Binance-specific submission mechanics.

    Clamp to real balance after fees, enforce the 100 USDC minimum notional, round,
    and dispatch a limit or market order. ``qty`` already comes from QuantityDecision.
    Weight, trend, cooldown, and other guards belong to the agnostic layer.
    Instrument.place holds the RAII cooldown around this call. Return an order or None.
    """
    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, price, qty)
    try:
        from providers.quantity import balance_cap_quantity, fee_cap_quantity
        available_qty, _balance_asset = balance_cap_quantity(
            api.get_free_balance, symbol, order_type, price)
        if available_qty is None:
            print(f"Balance unavailable for {order_type} {symbol}; order skipped.")
            return None
        if available_qty <= 0:
            print(f"No sufficient quantity available to place the {order_type} order.")
            return None

        # Keep the final check next to submission in case balance changed after planning.
        # ``available_qty`` is already expressed as base quantity.
        fee_cap = fee_cap_quantity(available_qty, PLACE_ORDER_FEE_PCT)
        if qty > fee_cap:
            print(f"Adjusting {order_type} qty from {qty:.8f} to "
                  f"{fee_cap:.8f} to cover balance and fees")
            qty = fee_cap

        qty = round(qty, 4)
        qty = float(Decimal(qty).quantize(Decimal('0.0001'), rounding=ROUND_DOWN))

        current_price = api.get_current_price(symbol)
        if qty * current_price < 100:
            print(f"Value {qty * current_price} of {symbol} too small to trade. by by!")
            return None

        print(f"Trying to place {order_type} {symbol} qty {qty:.8f} at "
              f"{'market price' if force else f'price {price}'}")
        if order_type == 'SELL':
            price = round(max(price, current_price), 0)
            if force:
                return (place_SELL_order_at_market(symbol, qty, client_order_id)
                        if client_order_id else place_SELL_order_at_market(symbol, qty))
            return (place_SELL_order(symbol, price, qty, client_order_id)
                    if client_order_id else place_SELL_order(symbol, price, qty))
        elif order_type == 'BUY':
            price = round(min(price, current_price), 0)
            if force:
                return (place_BUY_order_at_market(symbol, qty, client_order_id)
                        if client_order_id else place_BUY_order_at_market(symbol, qty))
            return (place_BUY_order(symbol, price, qty, client_order_id)
                    if client_order_id else place_BUY_order(symbol, price, qty))
        print(f"Invalid order type: {order_type}")
        return None
    except BinanceAPIException as e:
        print(f"Error placing {order_type} order (mechanics): {e}")
        return None
