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
from . import order_id_context as rc   # client_order_id + tag context (mutat in binance_api/)

from . import bapi as api
from .bapi_client import client
from lock import trade_cooldown   # gate anti rapid-fire (mutat in pachetul lock/)

# 30 iul: incarca parametrii tunabili din bapi_placeorder_config.env (versionat,
# se COMITE — fara secrete) INAINTE de a citi orice os.environ.get(...) de mai
# jos. botcore.load_dotenv NU suprascrie variabile deja setate in mediul real,
# doar completeaza ce lipseste — sigur de adaugat fara sa schimbe ce era deja
# configurat altfel. Acelasi tipar ca tradeall_config.env/monitortrades_config.env.
from botcore import load_dotenv as _load_dotenv
_load_dotenv("bapi_placeorder_config.env")

# Valorile de mai jos erau constante HARDCODATE direct in semnaturile functiilor
# de mai jos pana acum (unele inca din 3 iun, `722a548`) — extrase cu valorile
# IMPLICITE identice (zero schimbare de comportament daca nu modifici config.env).
# Vezi bapi_placeorder_config.env pt comentarii detaliate per parametru.
PLACE_ORDER_FEE_PCT = float(os.environ.get("PLACE_ORDER_FEE_PCT", "0.001"))
PLACE_ORDER_HOURS = int(float(os.environ.get("PLACE_ORDER_HOURS", "5")))
PLACE_ORDER_SAFEBACK_SEC = int(float(os.environ.get("PLACE_ORDER_SAFEBACK_SEC", str(48 * 3600 + 60))))
PLACE_ORDER_MAX_DAILY_TRADES = int(float(os.environ.get("PLACE_ORDER_MAX_DAILY_TRADES", "25")))
PLACE_ORDER_WAIT_TREND = os.environ.get("PLACE_ORDER_WAIT_TREND", "true").strip().lower() == "true"
PLACE_ORDER_MAX_WAIT_SEC = float(os.environ.get("PLACE_ORDER_MAX_WAIT_SEC", "10.0"))
PLACE_ORDER_WAIT_POLL_SEC = float(os.environ.get("PLACE_ORDER_WAIT_POLL_SEC", "0.2"))
PLACE_ORDER_WAIT_MODE = os.environ.get("PLACE_ORDER_WAIT_MODE", "full").strip()


class WeightLimitBlock(Exception):
    """Ridicată când limita 24h de tranzacționare e atinsă — nu are sens să retryi."""
    pass


def _resolve_qty(qty):
    """Model uniform de cantitate (21 iul): qty=None ("nu dau cantitate") ->
    foloseste maximul PERMIS de algoritm (apply_weight_limit + clamp pe
    balanta reala, mai jos in lant) — nu un placeholder numeric arbitrar
    (vechiul api.quantities[symbol], inconsistent intre 1000 si 10000 USD
    nominal, oricum irelevant: era mereu taiat de weight-limit). Unde SE DA
    o cantitate explicita, ramane neschimbata — algoritmul tot o plafoneaza
    ca gard de siguranta, dar intentia apelantului nu e alterata."""
    return float("inf") if qty is None else qty


def _maybe_wait_trend(side, symbol):
    """Gate de întârziere oportunistă, partajat de toate funcțiile de plasare.
    Așteaptă cât timp trendul aduce un preț mai bun (BUY: preț scade,
    SELL: preț urcă), până la PLACE_ORDER_MAX_WAIT_SEC. No-op dacă
    PLACE_ORDER_WAIT_TREND e False sau managerul de trend lipsește.
    Returnează secundele așteptate.
    30 iul: wait_trend/max_wait_sec ELIMINATE ca parametri (erau MOARTE — singurul
    apelant, __place_order, nu le suprascria niciodata; nimeni altcineva nu apela
    aceasta functie privata). Citite direct din config, sursa unica de adevar."""
    if not PLACE_ORDER_WAIT_TREND:
        return 0.0
    try:
        import cacheManager as cm
        waited = cm.get_short_trend_manager().wait_for_favorable_entry(
            side, symbol, max_wait_sec=PLACE_ORDER_MAX_WAIT_SEC, poll_sec=PLACE_ORDER_WAIT_POLL_SEC,
            sleep_fn=time.sleep, mode=PLACE_ORDER_WAIT_MODE)
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
    required_qty = _resolve_qty(required_qty)
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

        # 5. Cat mai pot tranzactiona in USDC
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

def manage_quantity(order_type, symbol, required_qty=None, price_to_be_traded=None, cancelorders=False, hours=PLACE_ORDER_HOURS):
    # required_qty=None -> _resolve_qty (in apply_weight_limit) foloseste maximul
    # permis; required_qty rescris mai jos cu valoarea deja plafonata, deci restul
    # functiei lucreaza mereu cu un numar real, niciodata None.

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

        qty = round(qty, 4)  # Rotunjim cantitatea la 4 zecimale
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

        qty = round(qty, 4)  # Rotunjim cantitatea la 4 zecimale
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


def if_place_safe_order(order_type, symbol, price, qty, time_back_in_seconds,
                        bypass_profit_guard=False):
    # 30 iul: max_daily_trades si profit_percentage ELIMINATE ca parametri (erau
    # MOARTE — 0 apelanti in tot repo-ul, singurul apelant real, place_safe_order
    # mai jos, trecea mereu EXACT PLACE_ORDER_MAX_DAILY_TRADES / respectiv
    # order_guard.margin_for("binance"), niciodata altceva). Citite direct mai
    # jos, ca sursa unica de adevar — nu mai sunt parametri de suprascris.
    # bypass_profit_guard=True -> IGNORA gardul de profit/istorie. ATENTIE: e DIFERIT de
    # `force` (care doar executa la MARKET in __place_order, dar RESPECTA gardul). Sare peste
    # gardul de profit SI peste fail-closed, pastrand siguranta (limita zilnica, anti-spam).
    # Il paseaza DISJUNCTORUL DE CRASH (trailing: force=True + bypass=True = market, fara profit).
    # Tradingul normal NU-l paseaza -> gard activ; eroare cache/manager fara bypass -> fail-closed.
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
        else:  # pentru "SELL"
            price = round(max(price, current_price), 0)

        qty = round(qty, 4)

        opposite_order_type = "SELL" if order_type == "BUY" else "BUY"
        backdays = math.ceil(time_back_in_seconds / 86400)

        # 30 iul: plafon zilnic + anti-spam DELEGATE la order_guard.daily_limit_guard()
        # — elimina o a DOUA implementare a EXACT aceleiasi logici (era duplicata cu
        # cea folosita deja de Kraken/Hyperliquid prin Instrument.place(), commit
        # d8f7c86). BinanceProvider.get_orders() e doar un wrapper subtire peste
        # apiorders.get_trade_orders() — ACEEASI sursa de date ca inainte, zero
        # schimbare reala (formula backdays din daily_limit_guard e acum IDENTICA,
        # math.ceil, ca sa nu schimbe pragul efectiv fata de cei 18 luni de aici).
        # max_daily_trades/safeback_sec raman din PROPRIA configurare Binance
        # (PLACE_ORDER_MAX_DAILY_TRADES / time_back_in_seconds), nu din
        # order_guard.conf, ca sa nu introduca un al 2-lea knob care ar putea diverge.
        ok, reason = order_guard.daily_limit_guard(
            provider, symbol, order_type,
            max_daily_trades=PLACE_ORDER_MAX_DAILY_TRADES,
            safeback_sec=time_back_in_seconds)
        if not ok:
            return False, reason

        oposite_trades = apiorders.get_trade_orders(opposite_order_type, symbol, max_age_seconds=time_back_in_seconds) ## curent date
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
        
        # ---- GARD PROFIT (AGNOSTIC, order_guard) ----
        # Logica de profit traieste acum decuplat in order_guard, ca sa ruleze IDENTIC si pe
        # alte venue-uri (ex. Kraken HYPE). Referinta, in cascada: 1) min(sell)/max(buy) din
        # fereastra (Order cache, calculat aici); 2) altfel ultimul fill opus al providerului
        # (BinanceProvider.last_opposite_fill = cache fills + API direct). bypass_profit_guard
        # sare tot; orice eroare de citire ridica -> prins de except-ul de jos -> fail-closed.
        if not bypass_profit_guard:
            window_ref = None
            if recent_opposite_trades:                       # fereastra (time-windowed) PRIMAR
                _prices = [float(t['price']) for t in recent_opposite_trades]
                window_ref = min(_prices) if order_type == "BUY" else max(_prices)
            # profit_percentage nu mai e parametru (30 iul, dead — vezi nota de mai sus);
            # calculat aici, lazy, doar cand chiar se foloseste (nu si pe calea bypass).
            # `provider` (BinanceProvider) e deja construit mai sus, pt daily_limit_guard —
            # reutilizat aici (e stateless, dar zero motiv sa construiesti 2 instante).
            if not order_guard.profit_guard(provider, symbol, order_type, price,
                                            order_guard.margin_for("binance"), window_ref=window_ref):
                return False, "profit_guard"
        return True, None

    except BinanceAPIException as e:
        print(f"Eroare la verificare if place safe order {order_type}: {e}")
        return False, "guard_check_api_exception"
    except Exception as e:
        # obs.1: nu pot aduce datele / eroare manager -> fara bypass fail-closed (NU tranzactionez);
        # cu bypass_profit_guard (disjunctor crash) lasam sa treaca (trebuie executat).
        print(f"[GARD] {order_type} {symbol}: verificare esuata ({e}) -> "
              f"{'TREC (bypass)' if bypass_profit_guard else 'BLOCAT (fail-closed)'}")
        return bool(bypass_profit_guard), (None if bypass_profit_guard else "guard_check_failed")


def place_order(order_type, symbol, price, qty, force=False, cancelorders=False, hours=PLACE_ORDER_HOURS):
    # fee_percentage NU mai e parametru (30 iul, dead — 0 apelanti in tot repo-ul
    # au trecut vreodata altceva decat PLACE_ORDER_FEE_PCT); __place_order il
    # citeste acum direct din config.
    order = __place_order(order_type, symbol, price, qty, force, cancelorders, hours)

    if order is None:
        if force and order_type == 'BUY':
            print("ULTRA DUBIOS!!!!")
            #order = place_SELL_order_at_market(symbol.forcesellsymbol[symbol], symbol.quantities[symbol])
            #time.sleep(0.2)
            #order = __place_order(order_type, symbol, price, qty, force, cancelorders, hours)

    return order
         

from decimal import Decimal, ROUND_DOWN
# Gate-ul de trend e MEREU activ la acest nivel (ultimul, comun tuturor tipurilor
# de ordin). Nu se mai expune în API-urile de mai sus.
# 30 iul: max_wait_sec REDUS de la 3600.0 (1 ORA — hardcodat asa din prima zi a
# mecanismului, 3 iun, niciodata schimbat) la 10.0 (ordinul secundelor, cerere
# user). Motiv: cu valoarea veche, o intarziere REALA (trend inca nefavorabil)
# putea bloca activ o ora intreaga per ordin — mult peste intentia "prinde
# primul semn scurt de inversare". should_wait() intoarce acum False (nu
# blocheaza deloc) daca trendul lipseste/e stale, deci riscul de blocaj pe
# infrastructura cazuta e deja eliminat separat; asta scurteaza si cazul
# "trend cunoscut, dar inca nefavorabil". Acum configurabil (nu mai hardcodat)
# via PLACE_ORDER_MAX_WAIT_SEC in bapi_placeorder_config.env.
# 30 iul: fee_percentage/wait_trend/max_wait_sec ELIMINATE ca parametri (erau
# MOARTE — singurul apelant, place_order, nu le suprascria niciodata, nici un
# alt apelant din tot repo-ul, inclusiv archive/old_trade/). Citite direct din config.
def __place_order(order_type, symbol, price, qty=None, force=False, cancelorders=False, hours=PLACE_ORDER_HOURS):

    order_type = order_type.upper()
    qty = _resolve_qty(qty)   # None = fara cantitate ceruta -> maximul permis de algoritm (defense in depth)
    sym.validate_params(order_type, symbol, price, qty)
        
    try:
        print(f"Order Request {order_type} {symbol} qty {qty}, Price {price}")
        qty, available_qty = manage_quantity(order_type, symbol, qty, price_to_be_traded=price, cancelorders=cancelorders, hours=hours)

        if qty == 0.0:
            raise WeightLimitBlock(f"{order_type} {symbol}: limita 24h atinsă — nu retry")

        if available_qty <= 0:
            print(f"No sufficient quantity available to place the {order_type} order.")
            return None
                
        from providers.quantity import fee_cap_quantity
        fee_cap = fee_cap_quantity(available_qty, PLACE_ORDER_FEE_PCT)
        if qty > fee_cap:
            print(f"Adjusting {order_type} order quantity from {qty:.8f} "
                  f"to {fee_cap:.8f} to cover balance and fees")
            qty = fee_cap

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
        if _maybe_wait_trend(order_type, symbol):
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


# fee_percentage NU mai e parametru (30 iul, dead — vezi place_order). max_daily_trades/
# profit_percentage nu mai sunt trecute la if_place_safe_order — le citeste ea insasi.
def _guarded_market_place(symbol, order_type, price, qty, **kwargs):
    """Import lazy pentru a evita ciclul bapi_placeorder <-> market_api.

    Punct unic usor de testat pentru adaptoarele legacy de mai jos. Orice ordin
    venit prin API-ul vechi intra astfel in acelasi pipeline ``Instrument.place``
    folosit de rtrade/tradeall si de ceilalti provideri.
    """
    from providers.market_api import api as market_api
    return market_api.place(symbol, order_type, price, qty, **kwargs)


def place_safe_order(order_type, symbol, price, qty=None,
                     safeback_seconds=PLACE_ORDER_SAFEBACK_SEC, force=False,
                     cancelorders=False, hours=PLACE_ORDER_HOURS,
                     bypass_profit_guard=False, _reason_out=None):
    """Adaptor compatibil SAFE -> pipeline-ul comun (fara smart repricing)."""
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
    # Pipeline-ul comun jurnalizeaza motivul exact. Dict-ul ramane acceptat pentru
    # compatibilitate, dar vechiul lant nu mai dubleaza evaluarea gardurilor.
    if order is None and _reason_out is not None:
        _reason_out.setdefault("reason", "common_pipeline_refused")
    return order
    

# 30 iul: jurnalul FLEET-WIDE extras in order_outcomes_log.py (sursa unica —
# reutilizat acum si de Instrument.place() pt Kraken/Hyperliquid, care inainte
# erau invizibile in logger/order_outcomes_*.log). Re-export pt compat inapoi
# (orice cod care citea bapi_placeorder.ORDER_OUTCOMES_LOG_DIR direct).
import order_outcomes_log as _outcomes_log
ORDER_OUTCOMES_LOG_DIR = _outcomes_log.ORDER_OUTCOMES_LOG_DIR


def _log_order_outcome(symbol, side, price, qty, outcome, refuse_reason, motivation):
    """Jurnal FLEET-WIDE (toti apelantii place_order_smart): un rand
    pipe-delimited per incercare de ordin. Observational — nu poate afecta
    returul catre caller (protejat de try/except in order_outcomes_log)."""
    try:
        caller = os.path.basename(sys._getframe(2).f_code.co_filename)
    except Exception:
        caller = None
    _outcomes_log.log_order_outcome(symbol, side, price, qty, outcome, refuse_reason,
                                    motivation, caller=caller)


# `pair` ramane in semnatura DOAR pt compatibilitate cu apelantii existenti
# (tradeall.py, archive/old_trade/trade3.py, trade5.py trec pair=True explicit) — dar
# e IGNORAT complet (era deja suprascris necontitionat cu False in corp, cod
# mort de multa vreme). Nu sterge parametrul fara sa actualizezi si apelantii.
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
# MECANICA de plasare Binance, EXTRASA ca sa fie apelata prin proxy-ul unic
# (Instrument.place() -> BinanceProvider). 30 iul: aceste 2 functii contin DOAR
# mecanica specific-Binance (ajustare pret + curatare ordine opuse; clamp de
# fee/balanta + min-notional + dispatch limit/market). PROTECTIA (plafon zilnic,
# gard profit, weight, trend-wait, cooldown, jurnal) traieste in stratul AGNOSTIC
# (Instrument.place + order_guard) — NU aici. Vechiul lant place_order_smart ramane
# (apelanti directi) pana la rewiring; aceste functii sunt varianta noua, curata.
# ============================================================================

def adjust_price_and_cancel_opposite(order_type, symbol, price, cancel_opposite=True):
    """MECANICA pret Binance (ex place_order_smart): (optional) anuleaza ordinele
    OPUSE contraproductive (SELL sub pretul de BUY / BUY peste pretul de SELL), apoi
    ajusteaza pretul (clamp la current +- nudge 0.1%, rotunjit). Intoarce pretul de
    folosit. Rulat de Instrument.place() INAINTE de gardul de profit — ca gardul sa
    vada exact acelasi pret ca in lantul vechi."""
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
    """MECANICA de trimitere Binance (ex __place_order, DOAR partea de mecanica):
    clamp de fee/balanta reala, min-notional (100 USDC), rotunjire, dispatch
    limit/market. `qty` vine DEJA plafonat de weight (cap_quantity, in Instrument.place).
    NU face weight/trend-wait/cooldown/garduri — acelea sunt in stratul agnostic.
    Intoarce order dict sau None. Cooldown-ul (RAII) e tinut de Instrument.place in
    jurul acestui apel."""
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

        # Ultimul check ramane langa submit pentru cazul in care soldul s-a
        # schimbat dupa planificare. available_qty este deja cantitate de baza.
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
