
import time
import datetime
import math
import sys

#my imports
import utils as u
from binanceapi import client

import symbols as sym
  
#######
#######      get_all_orders     #######
#######

####fixed
def get_filled_orders_bed(order_type, symbol, backdays=3, limit=1000):
    try:
        # Validare simbol
        sym.validate_symbols(symbol)
        sym.validate_ordertype(order_type)

        # Validare order_type doar dacă nu e None
        if order_type is not None:
            order_type = order_type.upper()

        end_time = int(time.time() * 1000)  # ms
        interval_hours = 24
        interval_ms = interval_hours * 60 * 60 * 1000
        start_time = end_time - backdays * 24 * 60 * 60 * 1000

        all_filtered_orders = []

        while start_time < end_time:
            current_end_time = min(start_time + interval_ms, end_time)
            try:
                time.sleep(2)
                orders = client.get_all_orders(
                    symbol=symbol,
                    startTime=start_time,
                    endTime=current_end_time,
                    limit=limit
                ) or []
            except Exception as api_err:
                print(f"[Eroare Binance] {symbol}: {api_err}")
                orders = []

            print(f"{len(orders)} orders retrieved for interval {interval_ms/60*60*1000}")

            filtered_orders = [
                {
                    'orderId': order.get('orderId'),
                    'price': float(order.get('price', 0)),
                    'quantity': float(order.get('origQty', 0)),
                    'timestamp': order.get('time'),  # ms
                    'side': order.get('side', '').upper()
                }
                for order in orders
                if order.get('status') == 'FILLED'
                and (
                    order_type is None  # dacă None → acceptă orice
                    or order.get('side', '').upper() == order_type
                )
            ]

            all_filtered_orders.extend(filtered_orders)
            start_time = current_end_time

        print(f"Filtered filled orders ({'ALL' if order_type is None else order_type}): {len(all_filtered_orders)}")
        for filled_order in all_filtered_orders[:5]:
            print(filled_order)

        return all_filtered_orders

    except Exception as e:
        print(f"Unexpected error in get_filled_orders: {e}")
        return []


def get_filled_orders(order_type, symbol, startTime, limit=1000):
    try:
        sym.validate_symbols(symbol)
        sym.validate_ordertype(order_type)

        end_time = int(time.time() * 1000)  # ms
        #start_time = end_time - backdays * 24 * 60 * 60 * 1000

        trades = client.get_my_trades(symbol=symbol, startTime=startTime, limit=limit) or []
        filtered_trades = [
            {
                'orderId': trade.get('orderId'),
                'price': float(trade.get('price', 0)),
                'quantity': float(trade.get('qty', 0)),
                'timestamp': trade.get('time'),  # ms
                'side': 'BUY' if trade.get('isBuyer') else 'SELL'
            }
            for trade in trades
            if start_time <= trade.get('time', 0) <= end_time
            and (order_type is None or 
                 (order_type.upper() == "BUY" and trade.get('isBuyer')) or
                 (order_type.upper() == "SELL" and not trade.get('isBuyer')))
        ]

        #print(f"Filtered filled trades ({'ALL' if order_type is None else order_type}): {len(filtered_trades)} from {len(trades)} trades")
        for t in filtered_trades[:5]:
            print(t)

        return filtered_trades

    except Exception as e:
        print(f"Unexpected error in get_filled_orders: {e}")
        return []



def get_recent_filled_orders(order_type, symbol, max_age_seconds):

    #backdays = math.ceil(max_age_seconds / (24 * 60 * 60))  # Aproximăm numărul de zile

    all_filled_orders = get_filled_orders(order_type, symbol, max_age_seconds)    
    recent_filled_orders = []
    current_time = time.time()
    if(len(all_filled_orders) < 1) :
        return []
    
    print(f"have {len(all_filled_orders)} orders. ignore oldest.")
    for order in all_filled_orders:
        if current_time - order['timestamp']/1000 <= max_age_seconds:
            recent_filled_orders.append(order)

    # Sort the recent_filled_orders by price in ascending order
    recent_filled_orders.sort(key=lambda x: x['price'])

    return recent_filled_orders



def get_trade_orders(order_type, symbol, max_age_seconds):
    import cacheManager as cm
    cache_order_manager = cm.get_cache_manager("Order")

    sym.validate_ordertype(order_type)
    sym.validate_symbols(symbol)

    if not cache_order_manager.cache:  # dacă e None sau gol
        return []
        
    # extrage lista pentru simbol
    orders_for_symbol = cache_order_manager.cache.get(symbol, [])
    if not orders_for_symbol:
        return []

    #print(f" orders_for_symbol {orders_for_symbol}")
    current_time_ms = int(time.time() * 1000)
    max_age_ms = max_age_seconds * 1000  # convert to ms

    filtered_orders = [
        {
            'orderId': order.get('orderId'),
            'price': float(order.get('price', 0)),
            'quantity': float(order.get('quantity', 0)),  # atenție: aici e 'quantity', nu 'origQty'
            'timestamp': order.get('timestamp'),  # deja în ms în cache
            'side': order.get('side', '').upper()
        }
        for order in orders_for_symbol
        if (order_type is None or order.get('side', '').upper() == order_type)
        and (current_time_ms - order.get('timestamp', 0)) <= max_age_ms
    ]

    #print(f" filtered_orders {filtered_orders} , current_time_ms {current_time_ms} timestamp  max_age+ms {max_age_ms}")
    return filtered_orders
