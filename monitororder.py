import time
import datetime
import random
from collections import OrderedDict

# Binance imports.
from binance.client import Client
from binance.exceptions import BinanceAPIException

# Project imports.
import utils as u
import symbols as sym
from binance_api import bapi as api
from binance_api import bapi_placeorder as po
from providers.market_api import api as mkt  # Single guarded proxy (Instrument.place).
from providers.strategy_executor import SubmissionRefused
import order_retry
import accepted_order_persistence

MAX_PROC = 0.77
monitor_interval = 3.7
MAX_TRACKED_ORDER_IDS = 10_000
initial_prices = OrderedDict()
initial_sell_prices = OrderedDict()
initial_buy_prices = OrderedDict()


def _remember_initial_price(cache, order_id, price):
    cache[order_id] = price
    cache.move_to_end(order_id)
    while len(cache) > MAX_TRACKED_ORDER_IDS:
        cache.popitem(last=False)


TIME_SLEEP_ERROR = 10


def monitor_open_orders_by_type(symbol, order_type, failed_orders=None):
    """Reprice eligible open orders without owning a second retry loop.

    Each replacement is persisted as ``awaiting_cancel`` before the old venue
    order is touched. Only explicit venue confirmation of cancellation makes that
    durable record submittable. ``failed_orders`` remains a compatibility-only
    argument for old callers; this function neither writes nor drains it.
    """
    orders = api.get_open_orders(order_type, symbol)
    if not orders:
        print(f"For {symbol} there are no open {order_type} orders to begin with.")
        return
    
    current_price = api.get_current_price(symbol)
    if current_price is None:
        print("Could not obtain the current price.")
        return
    
    print(f"The current price : {current_price:.2f}")
    
    initial_prices = initial_sell_prices if order_type == "SELL" else initial_buy_prices
    
    for order_id in list(orders.keys()):
        order = orders[order_id]
        price = order['price']

        if not price or price <= 0:          # Guard against division by zero for invalid prices.
            print(f"Invalid price ({price}) for order {order_id}, skipping.")
            continue

        if order_id not in initial_prices:
            _remember_initial_price(initial_prices, order_id, price)

        difference_percent = abs(current_price - price) / price * 100
        print(f"{order_type.capitalize()} price {price}, current price {current_price} difference: {difference_percent:.2f}%, Order ID {order_id}")
        
        are_close = u.are_close(current_price, price, MAX_PROC)
        if are_close:
            print(f"Current price {current_price} and {order_type} price {price} are close!")
            
            difference_percent = abs(current_price - initial_prices[order_id]) / initial_prices[order_id] * 100
            are_close = u.are_close(current_price, initial_prices[order_id], MAX_PROC)
            if not are_close:
                print(f"However the price moved too much ({difference_percent}%) compared with the initial price ({initial_prices[order_id]}). The order is no longer modified.")
                continue
            else:
                print(f"Current price {current_price} and initial {order_type} price {initial_prices[order_id]} are close!")
            
            # Build and validate the exact replacement before canceling the live
            # order. Instrument.place repeats this check immediately before submit
            # to close the time-of-check/time-of-use window.
            if order_type == "SELL":
                new_price = current_price * 1.001 + 1
            else:
                new_price = current_price * 0.999 - 1
            try:
                original_quantity = float(order["quantity"])
                quantity = float(
                    order.get("remainingQty", order["quantity"]))
            except (TypeError, ValueError, OverflowError):
                print(
                    f"Order {order_id} has an invalid remaining quantity; "
                    "it is not canceled or replaced."
                )
                continue
            if quantity <= 0:
                print(
                    f"Order {order_id} has no unfilled quantity; "
                    "it is not canceled or replaced."
                )
                continue

            # Persist and lease the exact replacement before canceling the live
            # order. Any later policy refusal or response loss therefore leaves a
            # durable intent for the shared worker instead of silently removing
            # protection/exposure from the venue.
            replacement_id = order_retry.enqueue(
                symbol, order_type, quantity,
                {"smart": False, "kind": "monitor_order_replace"},
                requested_price=new_price, ref_price=current_price,
                failure_reason="submit_pending",
                provider_name="Binance", kind="monitor_order_replace",
                lifecycle="awaiting_cancel", replaces_order_id=order_id,
                replaces_original_qty=original_quantity)
            claimed = (
                order_retry.claim(
                    [replacement_id],
                    lease_sec=order_retry.RETRY_CLAIM_LEASE_SEC)
                if replacement_id else []
            )
            if not claimed:
                print(
                    f"The {order_type} replacement could not be persisted and "
                    "leased; the existing order remains active."
                )
                continue
            replacement_claim = claimed[0]
            client_order_id = dict(
                replacement_claim.get("place_kwargs") or {}).get(
                    "client_order_id")
            if not client_order_id:
                order_retry.complete_claim(
                    replacement_claim, "success")
                print(
                    f"The {order_type} replacement has no durable client ID; "
                    "the existing order remains active."
                )
                continue

            # Issue the one-shot permit only after the durable intent exists, so
            # permit acquisition stays immediately adjacent to cancel and submit.
            try:
                cache_permit = mkt.preflight_order(
                    symbol, order_type, quantity, new_price,
                    market=False, kind="monitor_order_replace")
                if cache_permit is None:
                    raise SubmissionRefused("account_cache_not_fresh")
            except Exception as exc:  # noqa: BLE001
                order_retry.complete_claim(replacement_claim, "success")
                refusal_reason = getattr(exc, "reason", str(exc))
                print(
                    f"The {order_type} replacement was refused before cancellation: "
                    f"{refusal_reason}. The existing order remains active."
                )
                continue

            try:
                cancel_confirmed = bool(api.cancel_order(symbol, order_id))
            except Exception as exc:  # noqa: BLE001
                cancel_confirmed = False
                print(
                    f"The cancel request for order {order_id} raised {exc}; "
                    "reconciling venue status before deciding on replacement."
                )
            try:
                # The cancel response cannot reveal a fill racing the request.
                # Always reconcile the final old-order state before fixing the
                # replacement quantity and making it submittable.
                status = mkt.order_status(symbol, str(order_id))
            except Exception as exc:  # noqa: BLE001
                order_retry.complete_claim(
                    replacement_claim, "status_error",
                    failure_reason=str(exc))
                print(
                    f"The cancellation of {order_id} is ambiguous and status "
                    f"reconciliation failed: {exc}. The durable replacement "
                    "remains non-submittable and is deferred."
                )
                continue
            venue_status = str(status.venue_status or "").upper()
            if venue_status in {"CANCELED", "CANCELLED"}:
                if not cancel_confirmed:
                    print(
                        f"Order {order_id} is confirmed canceled after the cancel "
                        "response was lost; continuing with its replacement."
                    )
            else:
                if status.terminal:
                    order_retry.complete_claim(
                        replacement_claim, "success")
                    initial_prices.pop(order_id, None)
                else:
                    order_retry.complete_claim(
                        replacement_claim, "release")
                print(
                    f"Order {order_id} reconciled as {venue_status or 'UNKNOWN'}; "
                    "no replacement is submitted."
                )
                continue

            try:
                filled_quantity = max(0.0, float(status.filled_qty or 0.0))
                quantity = max(0.0, original_quantity - filled_quantity)
            except (TypeError, ValueError, OverflowError):
                order_retry.complete_claim(
                    replacement_claim, "status_error",
                    failure_reason="invalid_final_filled_quantity")
                continue
            activation = order_retry.activate_claimed_replacement(
                replacement_claim, quantity)
            if activation == "resolved":
                initial_prices.pop(order_id, None)
                continue
            if activation != "activated":
                print(
                    f"The durable replacement for order {order_id} could not be "
                    "activated after cancellation; no replacement is submitted."
                )
                continue

            refreshed_claim = order_retry.begin_claimed_submit(
                replacement_claim)
            if refreshed_claim is None:
                print(
                    f"The durable replacement claim for order {order_id} changed "
                    "before dispatch; no replacement is submitted by this process."
                )
                continue
            replacement_claim = refreshed_claim

            # Attempt placement through the single guarded proxy.
            outcome = {}
            try:
                new_order = mkt.place(
                    symbol, order_type, new_price, quantity, smart=False,
                    kind="monitor_order_replace",
                    caller_owns_retry=True,
                    client_order_id=client_order_id,
                    _outcome_context=outcome,
                    cache_permit=cache_permit)
            except Exception as exc:  # noqa: BLE001
                new_order = None
                outcome.update(
                    state="unknown", reason=f"replacement_exception:{exc}")

            if new_order:
                tracked = accepted_order_persistence.complete_accepted_claim(
                    order_retry, replacement_claim, order=new_order,
                    provider_name="Binance", symbol=symbol, side=order_type)
                if not tracked:
                    print(
                        f"CRITICAL: replacement order {new_order.get('orderId')} "
                        "was accepted but its durable tracker could not be updated."
                    )
            else:
                failure_reason = str(
                    outcome.get("reason") or "replacement_not_accepted")
                submission_state = (
                    "refused" if outcome.get("state") == "refused"
                    else "unknown"
                )
                terminal_execution_refusal = (
                    submission_state == "refused"
                    and failure_reason in {
                        "execution_disabled", "trading_disabled"})
                claim_outcome = (
                    "success" if terminal_execution_refusal else
                    "deferred"
                    if order_retry.is_non_failure_deferral(failure_reason)
                    else "failure"
                )
                order_retry.complete_claim(
                    replacement_claim, claim_outcome,
                    failure_reason=failure_reason,
                    submission_state=submission_state)
                if terminal_execution_refusal:
                    initial_prices.pop(order_id, None)
                    print(
                        f"The {order_type} replacement was canceled by the "
                        f"execution gate after old order {order_id} was canceled; "
                        "the stale replacement intent was removed.")
            
            if new_order:
                orders[new_order['orderId']] = {
                    'price': new_price,
                    'quantity': quantity
                }
                _remember_initial_price(
                    initial_prices, new_order['orderId'], initial_prices.pop(order_id))
                print(f"Updated order from {price} to {new_price}. New ID: {new_order['orderId']}")
            else:
                print(
                    f"The {order_type} replacement was not accepted immediately; "
                    "the persisted intent stays in the shared outbox for a retry."
                )
    


MONITOR_BETWEEN_ORDERS_INTERVAL = 2
MONITOR_OPEN_ORDER_INTERVAL = 8
MONITOR_CLOSE_ORDER_INTERVAL = 8
max_age_seconds = 3 * 24 * 3600  # Maximum age for treating filled orders as recent (three days).

def monitor_orders():
    # monitor_filled_buy_orders()
    # return
    
    monitor_open_orders_lasttime = time.time() - MONITOR_OPEN_ORDER_INTERVAL - TIME_SLEEP_ERROR
    monitor_close_orders_by_age_lasttime = time.time() - MONITOR_CLOSE_ORDER_INTERVAL - TIME_SLEEP_ERROR

    while not api.stop:
        try:
            currenttime = time.time()
            if(currenttime - monitor_open_orders_lasttime > MONITOR_OPEN_ORDER_INTERVAL) :
                for symbol in sym.symbols:
                    monitor_open_orders_by_type(symbol, "SELL")
                    monitor_open_orders_by_type(symbol, "BUY")
                    monitor_open_orders_lasttime = currenttime
            if(currenttime - monitor_close_orders_by_age_lasttime > MONITOR_CLOSE_ORDER_INTERVAL) :
                #monitor_close_orders_by_age(max_age_seconds)
                monitor_close_orders_by_age_lasttime = currenttime   
                
            time.sleep(min(MONITOR_OPEN_ORDER_INTERVAL, MONITOR_CLOSE_ORDER_INTERVAL))
            
        except BinanceAPIException as e:
            print(f"Binance API error: {e}")
            time.sleep(TIME_SLEEP_ERROR)
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(TIME_SLEEP_ERROR)


if __name__ == "__main__":
    monitor_orders()
