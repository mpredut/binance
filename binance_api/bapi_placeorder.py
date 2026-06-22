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
import symbols as sym
import config as cfg
import priceAnalysis as pa
from . import order_id_context as rc   # client_order_id + tag context (mutat in binance_api/)

from . import bapi as api
from .bapi_client import client
from lock import trade_cooldown   # gate anti rapid-fire (mutat in pachetul lock/)


def _maybe_wait_trend(side, symbol, wait_trend, max_wait_sec):
    """Gate de întârziere oportunistă, partajat de toate funcțiile de plasare.
    Așteaptă cât timp trendul aduce un preț mai bun (BUY: preț scade,
    SELL: preț urcă), până la max_wait_sec. No-op dacă wait_trend e False
    sau managerul de trend lipsește. Returnează secundele așteptate."""
    if not wait_trend:
        return 0.0
    try:
        import cacheManager as cm
        waited = cm.get_short_trend_manager().wait_for_favorable_entry(
            side, symbol, max_wait_sec=max_wait_sec, poll_sec=0.2, sleep_fn=time.sleep, mode="full")
        if waited:
            print(f"[{side} {symbol}] așteptat {waited:.1f}s pentru preț mai bun (trend favorabil)")
        return waited
    except Exception as e:
        print(f"[{side} {symbol}] trend gate indisponibil: {e}")
        return 0.0


def _fresh_price(symbol):
    """Prețul cel mai proaspăt (WS via CacheCurrentPriceManager), cu fallback
    pe bapi.get_current_price. Folosit după wait, pentru reacție rapidă."""
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
    try:
        # weight din permisiuni
        weight = pa.get_weight_for_cash_permission_at_quant_time(symbol, order_type)
        if weight is None or math.isnan(weight):
            print("Weight is None, set it at default 0.03")
            weight = 0.03

        # 2. Obține cât s-a tranzacționat deja în ultimele 24h (în quote)
        stats = apiorders.get_total_traded_stats(symbol)
        traded_value = stats.get(order_type.upper(), {}).get('total_value', 0)

        # 3. Calculează valoarea totală tranzacționabilă (tranzacționată + disponibilă)
        total_value_reference = traded_value + available_qty * price
        # 4. Calculează plafonul maxim permis (în quote) pe baza weight
        max_trade_value = total_value_reference * weight
        #max_trade_value = available_qty * price * weight

        # 5. Cât mai pot tranzacționa în quote asset (USDC, USDT etc.)
        remaining_trade_value = max(0, max_trade_value - traded_value)

        # qty maxim în în cantitate/baza (BTC, TAO etc.)
        remaining_trade_qty = remaining_trade_value / price if price else 0

        # alegem cantitatea cea mai mică între ce vreau și cât am voie
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

def manage_quantity(order_type, symbol, required_qty, price_to_be_traded, cancelorders=False, hours=5):

    current_price = api.get_current_price(symbol)
                
    # 1. cat am efectiv disponibil
    available_qty = api.get_asset_info(order_type, symbol, current_price)

    # 2. aplicam limita de cash/weight
    required_qty = apply_weight_limit(symbol, order_type, current_price, required_qty, available_qty)


    if available_qty < required_qty:
        print(f"Not enough available {symbol}. Available: {available_qty:.8f}, Required: {required_qty:.8f}")

        freed_quantity = 0
        if cancelorders:
            freed_quantity = api.cancel_orders_old_or_outlier(
                order_type, symbol, required_qty, hours=hours, price_difference_percentage=0.15
            ) or 0

        available_qty += freed_quantity

        if available_qty < required_qty:
            print(f"Still not enough quantity. Adjusting order quantity to {available_qty:.8f}")

    return required_qty, available_qty


           
def place_BUY_order(symbol, price, qty):
    try:
        if not cfg.is_trade_enabled() :
            print(f"Trade is desabled!")
            return None

        price = round(min(price, _fresh_price(symbol)), 2)
        qty = round(qty, 4)
        client_order_id = rc.create_client_order_id()
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

def place_SELL_order(symbol, price, qty):
    try:
        if not cfg.is_trade_enabled() :
            print(f"Trade is disabled!")
            return None

        price = round(max(price, _fresh_price(symbol)), 2)
        qty = round(qty, 4)
        client_order_id = rc.create_client_order_id()
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

def place_BUY_order_at_market(symbol, qty):
    try:
        if not cfg.is_trade_enabled():
            print(f"Trade este dezactivat!")
            return None

        qty = round(qty, 4)  # Rotunjim cantitatea la 4 zecimale
        client_order_id = rc.create_client_order_id()
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


def place_SELL_order_at_market(symbol, qty):
    try:
        if not cfg.is_trade_enabled():
            print(f"Trade este dezactivat!")
            return None

        qty = round(qty, 4)  # Rotunjim cantitatea la 4 zecimale
        client_order_id = rc.create_client_order_id()
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
    """Pretul ULTIMEI executii OPUSE pe symbol — PERSISTENT, fara limita de timp.
    Pt BUY -> ultimul SELL executat; pt SELL -> ultimul BUY executat.
    Returneaza None DOAR cand cache-ul e OK dar nu exista fill opus (referinta lipsa legitima).
    RIDICA exceptie daca managerul/cache-ul nu e disponibil -> apelantul decide fail-closed.
    Delegat la clasa dedicata CacheTradeManager (fills reale via WS) -> ZERO apel API."""
    import cacheManager as cm
    return cm.get_cache_manager("Trade").last_opposite_fill_price(symbol, order_type)


def _last_opposite_fill_price_api(symbol, order_type):
    """Fallback API DIRECT (get_my_trades) cand cache-ul nu are tranzactia opusa
    (ex. cacheManager nepopulat inca / simbol nou). RIDICA exceptie pe eroare ->
    apelantul face fail-closed. None DOAR daca Binance confirma ca nu exista opus."""
    want_buyer = (order_type.upper() == "SELL")   # opusul unui SELL e un BUY (isBuyer=True)
    for tr in reversed(client.get_my_trades(symbol=symbol, limit=200)):
        if tr["isBuyer"] == want_buyer:
            return float(tr["price"])
    return None


def if_place_safe_order(order_type, symbol, price, qty, time_back_in_seconds, max_daily_trades=10, profit_percentage=0.01, bypass_profit_guard=False):
    # bypass_profit_guard=True -> IGNORA gardul de profit/istorie. ATENTIE: e DIFERIT de
    # `force` (care doar executa la MARKET in __place_order, dar RESPECTA gardul). Sare peste
    # gardul de profit SI peste fail-closed, pastrand siguranta (limita zilnica, anti-spam).
    # Il paseaza DISJUNCTORUL DE CRASH (trailing: force=True + bypass=True = market, fara profit).
    # Tradingul normal NU-l paseaza -> gard activ; eroare cache/manager fara bypass -> fail-closed.
    #import bapi_trades as apitrades
    from . import bapi_allorders as apiorders
    

    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, price, qty)
    minutes_ago = time.time() - 3 * 60  # With 3 min in urma , time.time() => seconds
    #apitrades.compare_trade_sources(symbol, order_type=order_type, max_age_seconds=time_back_in_seconds, limit=1000)
        
    try:
        
        current_price = api.get_current_price(symbol)
        
        if order_type == "BUY":
            price = round(min(price, current_price), 0)
        else:  # pentru "SELL"
            price = round(max(price, current_price), 0)

        qty = round(qty, 4)

        opposite_order_type = "SELL" if order_type == "BUY" else "BUY"
        backdays = math.ceil(time_back_in_seconds / 86400)
        #all_trades = apitrades.get_my_trades(order_type, symbol, backdays=backdays, limit=1000)
        #all_trades = apitrades.get_trade_orders(order_type, symbol, max_age_seconds=time_back_in_seconds)
        all_trades = apiorders.get_trade_orders(order_type, symbol, max_age_seconds=time_back_in_seconds)
        
        #all_trades = apitrades.get_trade_orders_24(order_type, symbol, days_back=backdays)
        #oposite_trades = apitrades.get_my_trades(opposite_order_type, symbol, backdays=backdays, limit=1000) ## curent date
        #oposite_trades = apitrades.get_trade_orders(opposite_order_type, symbol, max_age_seconds=time_back_in_seconds) ## curent date
        oposite_trades = apiorders.get_trade_orders(opposite_order_type, symbol, max_age_seconds=time_back_in_seconds) ## curent date
        if len(all_trades)/backdays > max_daily_trades:
            print(f"Am {len(oposite_trades)} trades. Limita zilnica este de {max_daily_trades} pentru '{order_type}'.")
            return False
        for trade in all_trades:
            trade_time = trade['timestamp'] / 1000  # 'time' este in milisecunde
            if trade_time > minutes_ago:
                print(f"Are recent transactions in last 3 minutes")
                return False
                
        #print("Tranzactii anterioare:")
        #for trade in oposite_trades:
            #print(apitrades.format_trade(trade, time_limit))
            
        print(f"Am {len(oposite_trades)} trades de tip {opposite_order_type} pentru {backdays} zile. ")
        
        time_limit = float(time.time() * 1000) - (time_back_in_seconds * 1000)  # in milisecunde
        # Filtram tranzactiile opuse care au avut loc in intervalul specificat
        # price > 0: ignora orice ordin fara pret real (defensiv; dupa fix-ul din cacheManager
        # anulatele nu mai ajung in cache, dar pastram filtrul ca plasa de siguranta).
        recent_opposite_trades = [trade for trade in oposite_trades
                                  if float(trade['timestamp']) >= float(time_limit)
                                  and float(trade.get('price', 0)) > 0]
        print(f"Ma raportrez doar la cele care sunt cu {time_back_in_seconds} sec. back , in numar de '{len(recent_opposite_trades)}'")
        for trade in recent_opposite_trades:
            readable = datetime.fromtimestamp(trade['timestamp'] / 1000)
            print(f"[CHECK] {readable} - price: {trade['price']} - included: {float(trade['timestamp']) >= time_limit}")
        
        # ---- GARD PROFIT: 3 niveluri de referinta, in cascada ----
        # 1) time-windowed: min(sell)/max(buy) din fereastra (cand are date).
        # 2) persistent: ultimul fill opus din cache (fara limita de timp) cand fereastra e goala.
        # 3) API DIRECT (get_my_trades) cand nici cache-ul n-are opusul (ex. cacheManager
        #    nepopulat inca). Cand fereastra are date, min/max e oricum >= la fel de strict.
        # bypass_profit_guard (ignora profit/istorie; il da disjunctorul de crash) sare tot.
        # Orice eroare de cache/manager/API -> ridica -> prins de except-ul de jos -> fail-closed.
        if not bypass_profit_guard:
            if recent_opposite_trades:                       # 1) fereastra (time-windowed) PRIMAR
                if order_type == "BUY":
                    ref = min(float(t['price']) for t in recent_opposite_trades)
                else:
                    ref = max(float(t['price']) for t in recent_opposite_trades)
                src = "fereastra"
            else:
                ref = _last_opposite_fill_price(symbol, order_type)   # 2) cache fills (persistent)
                src = "persistent"
                if ref is None:                              # 3) nici in cache -> API DIRECT (ultim resort)
                    ref = _last_opposite_fill_price_api(symbol, order_type)
                    src = "api"

            if ref is not None and ref > 0:
                if order_type == "BUY":
                    diff_percent = u.value_diff_to_percent(ref, price)   # (ref_SELL - pret_BUY)/ref_SELL
                else:
                    diff_percent = u.value_diff_to_percent(price, ref)   # (pret_SELL - ref_BUY)/pret_SELL
                print(f"[GARD] {order_type} {symbol}: ref {ref} ({src}), pret {price}, "
                      f"diff {diff_percent:.2f}%, prag {profit_percentage}%")
                if diff_percent < profit_percentage:
                    print(f"Diferenta procentuala ({diff_percent:.2f}%) sub prag {profit_percentage}%. "
                          f"Ordinul de {order_type} BLOCAT.")
                    return False
        return True

    except BinanceAPIException as e:
        print(f"Eroare la verificare if place safe order {order_type}: {e}")
        return False
    except Exception as e:
        # obs.1: nu pot aduce datele / eroare manager -> fara bypass fail-closed (NU tranzactionez);
        # cu bypass_profit_guard (disjunctor crash) lasam sa treaca (trebuie executat).
        print(f"[GARD] {order_type} {symbol}: verificare esuata ({e}) -> "
              f"{'TREC (bypass)' if bypass_profit_guard else 'BLOCAT (fail-closed)'}")
        return bool(bypass_profit_guard)


def place_order(order_type, symbol, price, qty, force=False, cancelorders=False, hours=5,
                fee_percentage=0.001):
    order = __place_order(order_type, symbol, price, qty, force, cancelorders, hours,
                          fee_percentage)
    
    if order is None:
        if force and order_type == 'BUY':
            print("ULTRA DUBIOS!!!!")
            #order = place_SELL_order_at_market(symbol.forcesellsymbol[symbol], symbol.quantities[symbol]) 
            #time.sleep(0.2)
            #order = __place_order(order_type, symbol, price, qty, force, cancelorders, hours, fee_percentage)
            
    return order
         

from decimal import Decimal, ROUND_DOWN
# Gate-ul de trend e MEREU activ la acest nivel (ultimul, comun tuturor tipurilor
# de ordin). max_wait_sec = 1h. Nu se mai expune în API-urile de mai sus.
def __place_order(order_type, symbol, price, qty, force=False, cancelorders=False, hours=5,
                  fee_percentage=0.001, wait_trend=True, max_wait_sec=3600.0):

    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, price, qty)
        
    try:
        print(f"Order Request {order_type} {symbol} qty {qty}, Price {price}")
        qty, available_qty = manage_quantity(order_type, symbol, qty, price_to_be_traded=price, cancelorders=cancelorders, hours=hours)

        if available_qty <= 0:
            print(f"No sufficient quantity available to place the {order_type} order.")
            return None
                
        if order_type == 'SELL':      
            print(f"available_qty {available_qty:.8f} versus requested {qty:.8f}")
            
            adjusted_qty = qty * (1 + fee_percentage)

            if available_qty < adjusted_qty:
                print(f"Adjusting {order_type} order quantity from {qty:.8f} to {available_qty / (1 + fee_percentage):.8f} to cover fees")
                qty = available_qty / (1 + fee_percentage)

        elif order_type == 'BUY':
            # in cazul unei comenzi de BUY, trebuie sa calculezi cantitatea necesara de USDT pentru achizitionare
            total_usdt_needed = qty * price * (1 + fee_percentage)

            if available_qty * price < total_usdt_needed:
                print(f"Not enough {symbol} available for {order_type}. You need {total_usdt_needed:.8f}, but you only have {available_qty:.8f} {symbol}.")
                # Ajusteaza cantitatea pe care o poti cumpara cu USDT disponibili
                qty = available_qty / (price * (1 + fee_percentage))
                print(f"Adjusting {order_type} order quantity to {qty:.8f} based on available {symbol}.")

        # Rotunjim cantitatea la 5 zecimale in jos
        #qty = math.floor(qty * 10**5) / 10**5  # Rotunjire in jos la 5 zecimale
        qty = round(qty, 4)
        qty = float(Decimal(qty).quantize(Decimal('0.0001'), rounding=ROUND_DOWN))  # Rotunjit la 5 zecimale

        current_price = api.get_current_price(symbol)
        if qty * current_price < 100:
            print(f"Value {qty * current_price} of {symbol} is too small to make sense to be traded :-) .by by!")
            return None
        
        print(f"Trying to place {order_type} order of {symbol} for quantity {qty:.8f} at {'market price' if force else f'price {price}'}")

        # GATE unic de întârziere oportunistă — chiar înainte de trimitere, ca să
        # reacționăm ultra-rapid la inversarea trendului (flip-to-send minim).
        # Acoperă toate tipurile: BUY/SELL × limit/market.
        if _maybe_wait_trend(order_type, symbol, wait_trend, max_wait_sec):
            current_price = _fresh_price(symbol)   # preț proaspăt după așteptare

        # GATE anti rapid-fire (cross-proces + cross-thread), stil RAII: rezervarea se
        # ELIBEREAZĂ AUTOMAT la ieșirea din `with` dacă nu facem commit (eșec/excepție/
        # uitat) → fără blocaje fantomă, fără release manual. Lock-ul nu e ținut peste
        # plasare → fără deadlock.
        with trade_cooldown.trade_slot(order_type, symbol) as slot:
            if not slot.allowed:
                age = time.time() - slot.info.get("timestamp", 0)
                print(f"[{order_type} {symbol}] BLOCAT de cooldown: ultim ordin "
                      f"({slot.info.get('side')}) acum {age:.0f}s (< {trade_cooldown.DEFAULT_COOLDOWN_SEC}s)")
                return None

            if order_type == 'SELL':
                price = round(max(price, current_price), 0)
                order = place_SELL_order_at_market(symbol, qty) if force else place_SELL_order(symbol, price, qty)
            elif order_type == 'BUY':
                price = round(min(price, current_price), 0)
                order = place_BUY_order_at_market(symbol, qty) if force else place_BUY_order(symbol, price, qty)
            else:
                print(f"Invalid order type: {order_type}")
                return None                                  # fără commit → auto-release

            if order:
                slot.commit(order.get("orderId"))            # succes → cooldown rămâne activ
            return order                                      # order None → auto-release

    except BinanceAPIException as e:
        print(f"Error placing {order_type.upper()} order: {e}")
        return None                                           # with deja a eliberat (no commit)
    #except Exception as e:
    #    print(f"place_order: A aparut o eroare: {e}")
    #    return None


def place_safe_order(order_type, symbol, price, qty, safeback_seconds=48*3600+60, force=False, cancelorders=False, hours=5, fee_percentage=0.001, bypass_profit_guard=False):

    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, price, qty)

    if not if_place_safe_order(order_type, symbol, price, qty, time_back_in_seconds=safeback_seconds, max_daily_trades=25, profit_percentage = 1.15, bypass_profit_guard=bypass_profit_guard) :
        return None

    return place_order(order_type, symbol, price, qty, force=force, cancelorders=cancelorders,
                       hours=hours, fee_percentage=fee_percentage)
    

def place_order_smart(order_type, symbol, price, qty, safeback_seconds=48*3600+60, force=False, cancelorders=True, hours=5, pair=True):
    
    order_type = order_type.upper()
    sym.validate_params(order_type, symbol, price, qty) 
    pair = False
    try:
        qty = round(qty, 5)
        cancel = False
        current_price = api.get_current_price(symbol)
        
        if order_type.upper() == 'BUY':
            open_SELL_orders = api.get_open_orders("SELL", symbol)
            # Anuleaza ordinele de vanzare existente la un pret mai mic decat pretul de cumparare dorit
            for order_id, order_details in open_SELL_orders.items():
                if order_details['price'] < price:
                    cancel = api.cancel_order(symbol, order_id)
                    if not cancel:
                        print(f"Fail cancel order {order_id} prep. for BUY order. We wanted becuse low price for SELL.")
            
            price = min(price, current_price)
            price = round(price * 0.999, 0)
            order = place_safe_order("BUY", symbol, price=price, qty=qty,
                safeback_seconds=safeback_seconds, force=force, cancelorders=cancelorders, hours=hours)
            # appy pair
            if order and pair :            
                price = max(price * 1.11, current_price)
                price = round(price * 1.001, 0)
                place_safe_order("SELL", symbol, price=price, qty=qty,
                    safeback_seconds=safeback_seconds, force=force, cancelorders=cancelorders, hours=hours)
                
        elif order_type.upper() == 'SELL':
            open_BUY_orders = api.get_open_orders("BUY", symbol)
            # Anuleaza ordinele de cumparare existente la un pret mai mare decat pretul de vanzare dorit
            for order_id, order_details in open_BUY_orders.items():
                if order_details['price'] > price:
                    cancel = api.cancel_order(symbol, order_id)
                    if not cancel:
                        print(f"Fail cancel order {order_id} prep. for SELL order. We wanted becuse high price for BUY")
                   
            price = max(price, current_price)
            price = round(price * (1 + 0.001), 0)
            order = place_safe_order("SELL", symbol, price=price, qty=qty,
                safeback_seconds=safeback_seconds, force=force, cancelorders=cancelorders, hours=hours)
            # appy pair
            if order and pair :
                price = min(price * (1 - 0.11), current_price)
                price = round(price * 0.999, 0)
                place_safe_order("BUY", symbol, price=price, qty=qty,
                    safeback_seconds=safeback_seconds, force=force, cancelorders=cancelorders, hours=hours)
        else:
            print("Tipul ordinului este invalid. Trebuie sa fie 'BUY' sau 'SELL'.")
            return None
        
        return order
    except BinanceAPIException as e:
        print(f"Eroare la plasarea ordinului de {order_type}: {e}")
        return None
        #return place_order(order_type, symbol, price, qty)
    #except Exception as e:
    #    print(f"place_order_smart: A aparut o eroare: {e}")
    #    return None
        #return place_order(order_type, symbol, price, qty)
