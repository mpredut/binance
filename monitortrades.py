import os
import sys
import time
import datetime
import json

import pandas as pd

import threading
from threading import Thread,Timer

####Binance
#from binance.client import Client
#from binance.exceptions import BinanceAPIException

#my imports
import symbols as sym
from binance_api import bapi as api
from binance_api import bapi_placeorder as po
from binance_api import bapi_trades as apitrades
from binance_api import bapi_allorders as apiorders
# Faza 3 decuplare: CITIREA starii de cont (sold + ordine) trece prin facada generica,
# nu mai direct prin bapi/apiorders. Asa monitortrades devine portabil pe HYPE.
# `mkt` = singletonul facadei (azi ruteaza simbolurile Binance tot la bapi, identic).
# place_order (po) si WS RAMAN Binance-specifice, neatinse.
from market_api import api as mkt

import utils as u
import log


# Cod legacy mutat in monitortrades_legacy.py pe 16 iun 2026 (vanzare graduala,
# ProcentDistributor/BuyTransaction, monitor_close_orders_by_age*, update_trades/
# apply_sell_orders, monitor_trades/start_monitoring, test()). NU mai e folosit de
# calea activa. Pastram doar `trades` gol: e referit de liniile COMENTATE din main()
# (update_trades/apply_sell_orders), ca sa ramana valide daca le decomenteaza cineva.
trades = []


def print_number_of_trades(maxage_trade_s):
    print(f"TRADE COUNT")
    for symbol in sym.symbols:
        print(f"For {symbol}")
        close_buy_orders = apitrades.get_trade_orders("BUY", symbol, maxage_trade_s)
        print(f"get_trade_orders:           Found {len(close_buy_orders)} close 'BUY' orders in the last {u.secondsToDays(maxage_trade_s)} days.")

        close_sell_orders = apitrades.get_trade_orders("SELL", symbol, maxage_trade_s)
        print(f"get_trade_orders:           Found {len(close_sell_orders)} close 'SELL' orders in the last {u.secondsToDays(maxage_trade_s)} days.")

        orders = apitrades.get_trade_orders(None, symbol, maxage_trade_s)
        print(f"get_trade_orders:           Total found {len(orders)} orders in the last {u.secondsToDays(maxage_trade_s)} days.")


def print_number_of_orders(maxage_trade_s):
    print(f"ORDER COUNT")
    for symbol in sym.symbols:
        print(f"For {symbol}")
        close_buy_orders = apiorders.get_trade_orders("BUY", symbol, maxage_trade_s)
        print(f"get_trade_orders:           Found {len(close_buy_orders)} close 'BUY' orders in the last {u.secondsToDays(maxage_trade_s)} days.")

        close_sell_orders = apiorders.get_trade_orders("SELL", symbol, maxage_trade_s)
        print(f"get_trade_orders:           Found {len(close_sell_orders)} close 'SELL' orders in the last {u.secondsToDays(maxage_trade_s)} days.")

        orders = apiorders.get_trade_orders(None, symbol, maxage_trade_s)
        print(f"get_trade_orders:           Total found {len(orders)} orders in the last {u.secondsToDays(maxage_trade_s)} days.")




# Cache-ul care va fi actualizat periodic
default_values_sell_recommendation = {
    "BTCUSDC": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7,
        'slope': 0.0,      # Valoare default pentru slope
        'pos': 0,          # Valoare default pentru pos
        'gradient': 0.0,   # Valoare default pentru gradient
        'tick': 0,         # Valoare default pentru tick
        'min': 0.0,        # Valoare default pentru min
        'max': 0.0         # Valoare default pentru max
    },
    "TAOUSDC": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7,
        'slope': 0.0,      # Valoare default pentru slope
        'pos': 0,          # Valoare default pentru pos
        'gradient': 0.0,   # Valoare default pentru gradient
        'tick': 0,         # Valoare default pentru tick
        'min': 0.0,        # Valoare default pentru min
        'max': 0.0         # Valoare default pentru max
    },
    "ETHUSDC": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7,
        'slope': 0.0,      # Valoare default pentru slope
        'pos': 0,          # Valoare default pentru pos
        'gradient': 0.0,   # Valoare default pentru gradient
        'tick': 0,         # Valoare default pentru tick
        'min': 0.0,        # Valoare default pentru min
        'max': 0.0         # Valoare default pentru max
    },
    # HYPE (Hyperliquid spot). Necesar AICI ca is_trend_up sa nu dea KeyError pe
    # fallback cand cacheManager n-are inca snapshot de trend pt HYPEUSDC (flota
    # nu scrie trend HYPE inca). slope/gradient=0 -> is_trend_up=False (neutru/sigur).
    "HYPEUSDC": {
        'force_sell': 0,
        'procent_desired_profit': 0.07,
        'expired_duration': 3600 * 3.7,
        'min_procent': 0.0099,
        'days_after_use_current_price': 7,
        'slope': 0.0,      # Valoare default pentru slope
        'pos': 0,          # Valoare default pentru pos
        'gradient': 0.0,   # Valoare default pentru gradient
        'tick': 0,         # Valoare default pentru tick
        'min': 0.0,        # Valoare default pentru min
        'max': 0.0         # Valoare default pentru max
    }
}

sell_recommendation = {}
sell_lock = threading.Lock()

class StateTracker:
    def __init__(self):
        self.running = True
        self.states = {}  # To hold states for each symbol
    
    def background_updater(self):
        while self.running:
            try:
                self.update_sell_recommendation()
            except Exception as e:
                print(e)
            time.sleep(50)

    def update_sell_recommendation(self):
        """Construiește sell_recommendation din:
          - CONFIG static (force_sell, procent_desired_profit, etc.) = defaults din cod
          - SEMNALE de trend (slope/pos/gradient/tick/min/max) = snapshot-ul din
            CacheInstantTrendManager (cross-process, scris de tradeall).
        Înlocuiește fostul sell_recommendation.csv."""
        global sell_recommendation
        try:
            import cacheManager as cm
            mgr = cm.get_instant_trend_manager()

            new_rec = {}
            for symbol, cfg in default_values_sell_recommendation.items():
                rec = dict(cfg)   # config static (force_sell, procente, expired_duration, ...)
                snap = mgr.get_snapshot(symbol)
                if snap:
                    # Doar slope și gradient sunt folosite efectiv (is_trend_up).
                    # FIX: slope_small e adesea 0 (ferestre nepline) -> is_trend_up degenera
                    # in final_trend (LENT). gradient_recent = momentum INSTANT real (canalul
                    # rapid) -> asa is_trend_up reflecta trendul instant, cum trebuie.
                    rec['slope']    = float(snap.get('gradient_recent', snap.get('slope_small', 0.0)) or 0.0)
                    rec['gradient'] = float(snap.get('final_trend', 0.0) or 0.0)
                new_rec[symbol] = rec

            with sell_lock:
                sell_recommendation = new_rec

            print(f"sell_recommendation actualizat din CacheInstantTrendManager!")
            self.update_states_from_sell_recommendation()
        except Exception as e:
            print(f"Eroare update_sell_recommendation din cacheManager: {e}. Folosesc defaults.")
            with sell_lock:
                sell_recommendation = default_values_sell_recommendation




    def update_states_from_sell_recommendation(self):
        for symbol, data in sell_recommendation.items():
            slope = data['slope']
            tick = data['tick']
            min_val = data['min']
            max_val = data['max']
            
            # If the symbol does not exist in the states, initialize it
            if symbol not in self.states:
                self.states[symbol] = []

            # Get the last state for this symbol (if it exists)
            last_state = self.states[symbol][-1] if self.states[symbol] else None

            # Process the state based on slope conditions
            self.process_state(symbol, slope, tick, min_val, max_val, last_state)

    def process_state(self, symbol, slope, tick, min_val, max_val, last_state):
        MAX_STATES = 1000
        # If there is no previous state, create a new one
        if last_state is None:
            new_state = {
                'slope': slope,
                'tick': tick,
                'min': min_val,
                'max': max_val
            }
            self.states[symbol].append(new_state)
            if len(self.states[symbol]) > MAX_STATES:
                self.states[symbol].pop(0)
            return

        # If slope is the same as the last state, update the current state's tick and min/max
        if slope * last_state['slope'] > 0 or (abs(slope - last_state['slope']) < 1e-9):  # Au acelasi semn:
            last_state['tick'] = tick
            last_state['min'] = min(last_state['min'], min_val)
            last_state['max'] = max(last_state['max'], max_val)
        else:
            # If slope has changed, create a new state
            new_state = {
                'slope': slope,
                'tick': tick,
                'min': min_val,
                'max': max_val
            }
            self.states[symbol].append(new_state)
            if len(self.states[symbol]) > MAX_STATES:
                self.states[symbol].pop(0)

    def display_states(self):
        print("Current states:")
        for symbol, states_list in self.states.items():
            print(f"Symbol: {symbol}")
            for i, state in enumerate(states_list):
                print(f"  State {i + 1}:")
                for key, value in state.items():
                    print(f"    {key}: {value}")
            print()


    def display_sell_recommendation(self):
        print("Current sell_recommendation content:")
        for symbol, data in sell_recommendation.items():
            print(f"Symbol: {symbol}")
            for key, value in data.items():
                print(f"  {key}: {value}")
            print()


state_tracker = StateTracker()

# Functie simplificata care verifica daca trendul este de crestere
def is_trend_up(symbol):
    """Trendul INSTANT, citit DIRECT din managerul de cache (up-to-date cu
    cache_instant_trend.json), nu din copia sell_recommendation (care poate ramane in urma).
    gradient_recent = momentum rapid real; fallback pe slope_small, apoi pe copie."""
    try:
        import cacheManager as cm
        snap = cm.get_instant_trend_manager().get_snapshot(symbol)
        if snap:
            slope = float(snap.get('gradient_recent', snap.get('slope_small', 0.0)) or 0.0)
            gradient = float(snap.get('final_trend', 0.0) or 0.0)
            return slope > 0 or (slope == 0 and gradient > 0)
    except Exception as e:
        print(f"is_trend_up: snapshot direct esuat ({e}) — folosesc sell_recommendation")
    rec = sell_recommendation.get(symbol)
    if not rec:
        return False   # simbol netrackuit (non-Binance, ex HYPEUSD pe Kraken): neutru -> nu blocheaza
    slope = rec['slope']
    gradient = rec['gradient']
    return slope > 0 or (slope == 0 and gradient > 0)


def get_relevant_trade(trade_orders, trade_type, threshold_s, symbol):
    if not trade_orders:
        print(f"Warning: No {trade_type} transactions for that currency!!!")
        return None, 0, True
        
    current_time_s = int(time.time())
     
    trade_orders.sort(key=lambda x: x['timestamp'], reverse=True)
    trade_price = float(trade_orders[0]['price'])
    trade_time = float(trade_orders[0]['timestamp']) / 1000  # Timpul în secunde
    print(f"{trade_type.capitalize()} price for {symbol}: {trade_price} at {u.timeToHMS(trade_time)}")
    
    can_trade = True
    if current_time_s - trade_time < threshold_s:
        print(f"Tranzactii de {trade_type.upper()} prea recente."
            f"A trecut doar {u.secondsToHours(current_time_s - trade_time):.2f} h. Astept sa treaca {u.secondsToHours(threshold_s)} h.")
        can_trade = False

    return trade_price, trade_time, can_trade


def get_position_stats(symbol, maxage_trade_s, api=None):

    api = api or mkt
    buy_orders = api.get_orders(symbol, "BUY", maxage_trade_s)
    sell_orders = api.get_orders(symbol, "SELL", maxage_trade_s)

    total_buy_qty = sum(float(o['qty']) for o in buy_orders)
    total_sell_qty = sum(float(o['qty']) for o in sell_orders)

    total_buy_value = sum(float(o['price']) * float(o['qty']) for o in buy_orders)
    total_sell_value = sum(float(o['price']) * float(o['qty']) for o in sell_orders)

    average_buy_price = (
        total_buy_value / total_buy_qty
        if total_buy_qty > 0 else 0
    )

    average_sell_price = (
        total_sell_value / total_sell_qty
        if total_sell_qty > 0 else 0
    )

    net_qty = total_buy_qty - total_sell_qty

    return {
        "buy_qty": total_buy_qty,
        "sell_qty": total_sell_qty,
        "net_qty": net_qty,
        "average_buy_price": average_buy_price,
        "average_sell_price": average_sell_price,
        "buy_count": len(buy_orders),
        "sell_count": len(sell_orders),
    }

# ── TP DUR — COEXISTA cu logica de trend de mai jos (nu o inlocuieste). Vinde o
#    PROPORTIE din pozitie pe castig MARE, INDIFERENT de trend = backstop pt varfuri
#    pe care gate-ul de trend le-ar rata (ex. TAO la $287). Cooldown ca sa nu descarce
#    tot in cascada. Comuta cu HARD_TP_ENABLED.
# ── Parametri reglabili: defaults din cod, SUPRASCRISE de monitortrades.conf (optional) ──
HARD_TP_ENABLED    = True
HARD_TP_PCT        = 0.17       # castig (fractie) de la care TP-ul dur vinde o proportie
HARD_TP_FRACTION   = 0.5        # cat vinde (din soldul liber)
HARD_TP_COOLDOWN_S = 6 * 3600
TP_REFERENCE       = "last"     # "last" (ultimul buy) | "average" (media pe maxage zile)
SYMBOL_PARAMS      = {}         # {symbol: (gain_frac, lost_frac, maxage_s)} din conf
_hard_tp_last = {}


def _load_mt_conf(path=None):
    """Suprascrie parametrii din monitortrades.conf (optional; fallback pe valorile din cod)."""
    global HARD_TP_ENABLED, HARD_TP_PCT, HARD_TP_FRACTION, HARD_TP_COOLDOWN_S, TP_REFERENCE
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitortrades.conf")
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                line = raw.split("#", 1)[0].strip()
                if not line or "=" not in line:
                    continue
                k, _, v = line.partition("="); k, v = k.strip(), v.strip()
                if k == "hard_tp_enabled":      HARD_TP_ENABLED = v.lower() in ("yes", "true", "1", "on", "da")
                elif k == "hard_tp_pct":        HARD_TP_PCT = float(v) / 100.0
                elif k == "hard_tp_fraction":   HARD_TP_FRACTION = float(v)
                elif k == "hard_tp_cooldown_h": HARD_TP_COOLDOWN_S = float(v) * 3600
                elif k == "tp_reference":       TP_REFERENCE = v.lower()
                elif "/" in v:                  # SIMBOL = gain% / lost% / maxage_zile
                    p = [x.strip() for x in v.split("/")]
                    if len(p) == 3:
                        SYMBOL_PARAMS[k] = (float(p[0]) / 100.0, float(p[1]) / 100.0, int(float(p[2])) * 24 * 3600)
    except (OSError, ValueError) as e:
        print(f"monitortrades.conf: {e} — folosesc valorile din cod")


_load_mt_conf()


from instrument import Instrument as _Instrument
from instruments_config import load_for


def _as_instrument(x):
    """Accepta un Instrument SAU (compat) un symbol string -> Instrument rutat prin facada."""
    if isinstance(x, _Instrument):
        return x
    sym = str(x)
    base = sym
    for q in ("USDC", "USDT", "BUSD", "FDUSD", "USD"):
        if base.endswith(q):
            base = base[:-len(q)]
            break
    return _Instrument(name=sym, symbol=sym, provider=mkt.provider_name_for(sym), base=base)


def get_available_qty(symbol, api=None):
    """Cantitatea LIBERA reala din activul de baza al simbolului (ex. TAOUSDC -> free TAO).
    Sursa de adevar pt 'vinde TOT ce ai disponibil', nu aproximarea din trade-uri.
    `api` = facada de cont (default singletonul `mkt`); injectabil pt alt provider/test."""
    api = api or mkt
    base = symbol
    for q in ("USDC", "USDT", "BUSD", "FDUSD", "USD"):
        if base.endswith(q):
            base = base[:-len(q)]
            break
    try:
        return float(api.free_balance(base) or 0.0)
    except Exception as e:
        print(f"get_available_qty {symbol}: {e}")
    return 0.0


#//todo: review 0.5
def monitor_price_and_trade(inst, sbs, maxage_trade_s=None, gain_threshold=None, lost_threshold=None):
    inst = _as_instrument(inst)
    symbol = inst.symbol
    # params per-instrument (fallback pe argument, apoi pe globalele din cod) ───────────
    if gain_threshold is None:
        _g = inst.param("mt", "gain", None, float)
        gain_threshold = _g / 100.0 if _g is not None else 0.07
    if lost_threshold is None:
        _l = inst.param("mt", "lost", None, float)
        lost_threshold = _l / 100.0 if _l is not None else 0.033
    if maxage_trade_s is None:
        _md = inst.param("mt", "maxage_days", None, float)
        maxage_trade_s = int(_md * 24 * 3600) if _md is not None else 4 * 24 * 3600
    hard_tp_pct = inst.param("mt", "hardtp", HARD_TP_PCT * 100, float) / 100.0
    hard_tp_frac = inst.param("mt", "hardtp_fraction", HARD_TP_FRACTION, float)
    hard_tp_cd = inst.param("mt", "hardtp_cooldown_h", HARD_TP_COOLDOWN_S / 3600.0, float) * 3600
    tp_ref = inst.param("mt", "ref", TP_REFERENCE)
    #try:
    
    qty = 1 #qty = calculate_position_size(...)    
    threshold_s = 3 * 60 * 60 # 3 h
    current_time_s = int(time.time())
    
    # 1. Obtine ordinele de cumparare si vanzare recente pentru simbol (prin facada,
    #    normalizate la forma comuna {side,price,qty,timestamp} -> get_relevant_trade).
    #trade_orders_buy = apitrades.get_trade_orders("BUY", symbol, maxage_trade_s)
    #trade_orders_sell = apitrades.get_trade_orders("SELL", symbol, maxage_trade_s)
    trade_orders_buy = inst.orders("BUY", maxage_trade_s)
    trade_orders_sell = inst.orders("SELL", maxage_trade_s)
    if not (trade_orders_buy or trade_orders_sell):
        print(f"No trade orders found for {symbol} in the last {maxage_trade_s} seconds.")
        return 
    buy_price, buy_time, can_buy = get_relevant_trade(trade_orders_buy, "BUY", threshold_s, symbol)
    sell_price, sell_time, can_sell = get_relevant_trade(trade_orders_sell, "SELL", threshold_s, symbol)

    position = get_position_stats(symbol, maxage_trade_s, api=inst.provider)
    # Referinta de pret pt castig: configurabila (TP_REFERENCE). Default "last" =
    # ultimul pret de cumparare (buy_price din get_relevant_trade); "average" = media pe maxage zile.
    if tp_ref == "average" and position["average_buy_price"] > 0:
        buy_price = position["average_buy_price"]
        print(f"POSITION (referinta=AVG {maxage_trade_s/86400:.0f}z) for {symbol} : {position}")
    else:
        print(f"POSITION (referinta=ultimul buy {buy_price}) for {symbol} : {position}")
    if position["average_sell_price"] > 0:
        sell_price = position["average_sell_price"]
        print(f"POSITION for {symbol} : {position}")

    threshold_all_s = 1 * 60 * 60 # 1 h
    if current_time_s - max(buy_time, sell_time)  < threshold_all_s:
        print(f"Trades too ... recente."
            f"Pass only {u.secondsToHours(current_time_s -  max(buy_time, sell_time)):.2f} h. Wait to pass {u.secondsToHours(threshold_all_s)} h.")
        can_trade = False
        
    
    # 2. Obtine pretul curent de pe piata (prin FACADA: HYPEUSDC -> HL spot,
    #    BTC/TAO USDC -> BinanceProvider.get_current_price = bapi, IDENTIC ca azi).
    current_price = inst.price()
    if current_price is None:
        print(f"No current price for {symbol} (piata inchisa / indisponibil) — skip")
        return
    print(f"Current price for {symbol}: {current_price}")
    avail_qty = inst.free() or 0.0   # TOATA cantitatea disponibila a instrumentului (None->0 pt Kraken/T212)

    # 3. Verifica ordinele de cumparare
    if trade_orders_buy:
        if not buy_price:
            print(f"No buy_price !!!!!")
            return
        price_increase = (current_price - buy_price) / buy_price
        price_decrease = (buy_price - current_price) / buy_price

        print(f"(increase: {price_increase * 100}%, decrease: {price_decrease * 100}%)")
        # 3.0. TP DUR: castig mare -> vinde o PROPORTIE din pozitie INDIFERENT de trend
        #      (coexista cu 3.1 de mai jos; backstop pt varfuri ratate de gate-ul de trend)
        if HARD_TP_ENABLED and price_increase >= hard_tp_pct and avail_qty > 0:
            if current_time_s - _hard_tp_last.get(symbol, 0) >= hard_tp_cd:
                hard_qty = round(avail_qty * hard_tp_frac, 4)
                print(f"[HARD-TP] {symbol} +{price_increase*100:.1f}% >= {hard_tp_pct*100:.0f}% "
                      f"-> vand {hard_tp_frac*100:.0f}% ({hard_qty}) INDIFERENT de trend")
                inst.place("SELL", current_price, hard_qty,
                    safeback_seconds=sbs, force=True, cancelorders=True, hours=2, pair=False)
                _hard_tp_last[symbol] = current_time_s
                return   # am vandut deja in acest tick; nu mai rula vanzarea de jos pe sold invechit
            else:
                print(f"[HARD-TP] {symbol} +{price_increase*100:.1f}% dar in cooldown (ultimul acum "
                      f"{u.secondsToHours(current_time_s - _hard_tp_last.get(symbol, 0)):.1f}h)")
        # 3.1. Verifica daca trebuie sa plasezi un ordin de vanzare (logica CURENTA, ramane)
        if price_increase > gain_threshold or u.are_close(price_increase, gain_threshold, target_tolerance_percent=1.0):
            if not is_trend_up(symbol):
                print(f"Price increased with {price_increase * 100}% by more than {gain_threshold * 100}% versus buy price and not trend up!")
                if can_sell and avail_qty > 0:
                    inst.place("SELL", current_price,
                        avail_qty, safeback_seconds=sbs, force=False, cancelorders=True, hours=2, pair=False)
                else:
                    print(f"No can sell (can_sell={can_sell}, avail_qty={avail_qty})")
                #po.place_SELL_order(symbol, current_price, qty)
                #po.place_order_smart("BUY", sym.btcsymbol, proposed_price, 0.017, safeback_seconds=16*3600+60,
                #    force=True, cancelorders=True, hours=1)
            else :
                print(f"No action taken, because trend is up!")
        elif price_decrease > lost_threshold or u.are_close(price_decrease, lost_threshold, target_tolerance_percent=1.0):
            if not is_trend_up(symbol):
                print(f"Price decreased with {price_decrease * 100}% by more than {lost_threshold * 100}% versus buy price and not trend up!")
                if can_sell and avail_qty > 0:
                    inst.place("SELL", current_price,
                        avail_qty, safeback_seconds=sbs, force=False, cancelorders=True, hours=2, pair=True)
                #po.place_SELL_order(symbol, current_price, qty)
                else:
                    print(f"No can sell (can_sell={can_sell}, avail_qty={avail_qty})")
            else:
                print(f"No action taken, because trend is up!")
        else:
            print(f"Nothing interesting")

    # 4. Verifica ordinele de vanzare
    if trade_orders_sell:     
        if not sell_price:
            print(f"No sell_price !!!!!")
            return
        price_decrease_versus_sell = (sell_price - current_price) / sell_price
        print(f"(price_decrease_versus_sell: {price_decrease_versus_sell * 100}%)")
        if price_decrease_versus_sell > gain_threshold or u.are_close(price_decrease_versus_sell, gain_threshold, target_tolerance_percent=1.0):
            if is_trend_up(symbol):
                print(f"Price decreased with {price_decrease_versus_sell * 100}% by more than {gain_threshold * 100}% versus sell price: Placing buy order")
                #api.cancel_orders_old_or_outlier("BUY", "BTCUSDT", qty, hours=0.5, price_difference_percentage=0.1)
                if can_buy:
                    inst.place("BUY", current_price + 0.5,
                        qty, safeback_seconds=sbs, cancelorders=True, hours=48, pair=False)
                else:
                   print("No can buy")
            else :
                print(f"No action taken, because trend is down!")

    return

    #except Exception as e:
    #    print(f"An error occurred while monitoring the price: {e}")

def main():
    # WS user-data bridge explicit (designul: fiecare proces își actualizează
    # memoria Order/Trade prin WS propriu + polling, fără re-citire de fișiere).
    import cacheManager as cm
    cm.enable_real_ws_event_sync()

    filename = "trades.json"
    
    maxage_trade_s =  4 * 24 * 3600  # Timpul maxim in care ordinele executate/filled sunt considerate recente (3 zile)
    interval = 60 * 4 #4 minute

    #api.get_binance_symbols(sym.taosymbol)

    # sell_recommendation vine din CacheInstantTrendManager (cross-process), nu din CSV.
    state_tracker.update_sell_recommendation()
    state_tracker.display_sell_recommendation()

    thread = threading.Thread(
        target=state_tracker.background_updater,
        name="SellRecommendationUpdater",
        daemon=True
    )
    thread.start()

    #for i in range(0, 5):
    #close_sell_orders = apitrades.get_trade_orders("SELL", sym.taosymbol, maxage_trade_s)
    close_sell_orders = apiorders.get_trade_orders("SELL", sym.taosymbol, maxage_trade_s)
    print(f"get_trade_orders:           Found {len(close_sell_orders)} close 'SELL' orders in the last {u.secondsToDays(maxage_trade_s)} days.")
    close_buy_orders = apiorders.get_trade_orders("BUY", sym.taosymbol, maxage_trade_s)
    print(f"get_trade_orders:           Found {len(close_buy_orders)} close 'BUY' orders in the last {u.secondsToDays(maxage_trade_s)} days.")
    print(f"close_buy_orders {close_buy_orders}")
    print(f"close_sell_orders {close_sell_orders}")
    
    #return
    
    #taosymbol_target_price = api.get_current_price(sym.taosymbol)
    #po.place_safe_order("BUY", sym.taosymbol, taosymbol_target_price - 10, 1)

    d = 14
    while True:

        #state_tracker.display_states()
        print_number_of_orders(maxage_trade_s)
        print_number_of_trades(maxage_trade_s)
        
        # PAS 4: itereaza instrumentele ENABLED din instruments.conf (namespace mt.*),
        # rutate EXPLICIT pe providerul lor. BTC/TAO (Binance) raman IDENTICE; HYPE/Kraken/
        # T212 intra cand le pui enabled=yes. Ordinele non-Binance raman DRY pana la portile
        # lor (HL_LIVE_ORDERS / KRAKEN_LIVE_ORDERS / T212_LIVE_ORDERS).
        try:
            _instruments = load_for("mt")   # doar enabled, cu params mt.*
        except Exception as _e:
            print(f"[instruments.conf] {_e} — sar peste acest ciclu")
            _instruments = {}
        for _inst in _instruments.values():
            print(f"-----{_inst.name} ({_inst.symbol}@{_inst.provider_label})------")
            try:
                monitor_price_and_trade(_inst, sbs=d*24*3600+60)
            except Exception as _e:
                print(f"[{_inst.name}] eroare in monitor: {_e}")
            print("--------------")
  
        with sell_lock:
            data = sell_recommendation[sym.btcsymbol]
        procent_desired_profit = data['procent_desired_profit']
        expired_duration = data['expired_duration']
        min_procent = data['min_procent']
        force_sell = data['force_sell']
        days_after_use_current_price = data['days_after_use_current_price']      
        
        #update_trades(trades, sym.btcsymbol, maxage_trade_s, procent_desired_profit, expired_duration, min_procent)
        #apply_sell_orders(trades, days_after_use_current_price, force_sell)
        #monitor_close_orders_by_age2(maxage_trade_s)
        time.sleep(60*0.8)  # Astept 1.8 minute.
        
        
if __name__ == "__main__":
    
     main()
    #test()
    # try:
        # main()
    # except Exception as e:
        # print(f"Eroare capturata: {e}")
    # finally:
        # print("Fortare inchidere...")
        # state_tracker.running = False
        # sys.exit(1)  # opreste toate daemon threads
    

    #confirmation candles

#Acum trend-ul se bazează pe:

#slope
#gradient

#Aș adăuga:

#confirmation periods
#multiple timeframe agreement
""" 
14. Cea mai mare problemă conceptuală

Ai:

if not is_trend_up(symbol):
    SELL

dar:

trend-ul poate fi lagging
piața crypto face fake reversals

Poți ajunge:

să vinzi bottom
să ratezi breakout

Ar trebui:

confidence score
multi indicator confirmation """
