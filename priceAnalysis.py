
import os
import math
import time
import json
import psutil

import numpy as np
from typing import List, Dict, Tuple, Optional

#for draw
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime
#from zoneinfo import ZoneInfo  # disponibil din Python 3.9+

#my import
import utils as u
import symbols as sym

### SHM import + my SHM import
#from multiprocessing import shared_memory
#import shmutils as shmu

price_cache_manager = None

def build_price_cache_manager():
    global price_cache_manager
    import cacheManager as cm
    price_cache_manager = cm.get_cache_manager("Price")  # dict per simbol

def priceLstFor(symbol: str) -> List[Tuple[int, float]]:

    if price_cache_manager is None:
        raise RuntimeError("Price cache manager nu a fost inițializat. Rulează build_price_cache_manager() mai întâi.")

    manager = price_cache_manager.get(symbol)
    if manager is None:
        return []

    # obține lista curentă din cache pentru simbol
    raw = manager.cache.get(symbol, [])
    #manager.save_state_to_file()
    
    return [(int(ts), float(p)) for ts, p in raw]


def drawPriceLst(timestamps, prices, trend_block_indices, symbol, trend_direction, duration_hours):
    import matplotlib
    print(matplotlib.get_backend())
    # Conversie timestamps -> datetime
    times = [datetime.fromtimestamp(ts) for ts in timestamps]

    #plt.clf()  # curăță figura curentă (nu creează alta)
    plt.figure(figsize=(12,5))
    plt.plot(times, prices, label='Price', color='blue')

    # Evidențiem blocurile de trend
    for start, end in trend_block_indices:
        plt.plot(times[start:end], prices[start:end], color='red', linewidth=2)

    plt.xlabel('Time')
    plt.ylabel('Price')
    plt.title(f"{symbol} - Trend {trend_direction}, durata {duration_hours:.2f}h")
    plt.legend()

    # Format data/ora pe axa X
    plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d %H:%M'))
    plt.gcf().autofmt_xdate()  # întoarce etichetele să nu se suprapună

    plt.savefig(f"plot_{symbol}.png")  # Salvează
    plt.show()
    plt.close()



def weighted_moving_average(prices: np.ndarray, window: int) -> np.ndarray:
    """
    Calculează media mobilă ponderată.
    Cele mai recente valori au greutăți mai mari.
    """
    wma = np.zeros_like(prices)
    weights = np.arange(1, window + 1)
    for i in range(window - 1, len(prices)):
        wma[i] = np.sum(prices[i - window + 1:i + 1] * weights) / np.sum(weights)
    return wma


# Medie mobilă ponderată (Weighted Moving Average – WMA)
def trend_wma(symbol: str, window_hours: int = 6):
    data = priceLstFor(symbol)
    if len(data) < 2:
        return None

    data = sorted(data, key=lambda x: x[0])
    timestamps, prices = zip(*data)
    timestamps = np.array(timestamps)
    prices = np.array(prices)

    delta = np.median(np.diff(timestamps))
    points_per_hour = int(3600 / delta)
    window = points_per_hour * window_hours

    wma_prices = weighted_moving_average(prices, window)

    # direcția trendului: comparăm ultimul preț WMA cu cel anterior
    if wma_prices[-1] > wma_prices[-2]:
        trend_direction = 'up'
    else:
        trend_direction = 'down'

    # vizualizare
    plt.figure(figsize=(12,5))
    plt.plot(timestamps, prices, label='Price', color='blue')
    plt.plot(timestamps, wma_prices, label=f'WMA {window_hours}h', color='red', linewidth=2)
    plt.xlabel('Timestamp')
    plt.ylabel('Price')
    plt.title(f"{symbol} - Trend WMA: {trend_direction}")
    plt.legend()
    plt.show()

    return {'direction': trend_direction}



#Holt’s Linear Trend
# from statsmodels.tsa.holtwinters import Holt
# def trend_holt(symbol: str, smoothing_level: float = 0.3, smoothing_slope: float = 0.1, forecast_hours: int = 1):
    # data = priceLstFor(symbol)
    # if len(data) < 2:
        # return None

    # data = sorted(data, key=lambda x: x[0])
    # timestamps, prices = zip(*data)
    # timestamps = np.array(timestamps)
    # prices = np.array(prices)

    # delta = np.median(np.diff(timestamps))
    # points_per_hour = int(3600 / delta)

    # model = Holt(prices).fit(smoothing_level=smoothing_level, smoothing_slope=smoothing_slope, optimized=False)
    # fitted = model.fittedvalues

    # # Forecast scurt pentru trend
    # forecast_points = forecast_hours * points_per_hour
    # future = model.forecast(forecast_points)

    # trend_direction = 'up' if future[-1] > fitted[-1] else 'down'

    # # vizualizare
    # plt.figure(figsize=(12,5))
    # plt.plot(timestamps, prices, label='Price', color='blue')
    # plt.plot(timestamps, fitted, label='Holt fit', color='red', linewidth=2)
    # plt.xlabel('Timestamp')
    # plt.ylabel('Price')
    # plt.title(f"{symbol} - Trend Holt: {trend_direction}")
    # plt.legend()
    # plt.show()

    # return {'direction': trend_direction, 'fitted': fitted, 'forecast': future}

def slope_tolerance_per_(symbol, price, 
                              base_tolerance = 0.0015, 
                              ):
  
    min_tol = 0.0005 
    max_tol = 2000.0
                              
    relative_tolerance = base_tolerance * price
    adaptive_tolerance = min(max(relative_tolerance, min_tol), max_tol)
    #print(f"[DEBUG] {symbol}: price={price}, base_tol={base_tolerance}, adaptive_tolerance={adaptive_tolerance}")
    return adaptive_tolerance       



def getTrendLongTerm(symbol: str, window_hours: int = 24, step_hours: int = 8,
                                slope_tolerance: float = 0.0028, persistence_factor: float = 1.5,
                                lookback_days=30, draw: bool = True) -> Optional[dict]:
   
    data: List[Tuple[int, float]] = priceLstFor(symbol)
    if len(data) < 2:
        return None
    data = sorted(data, key=lambda x: x[0])
    
    # Filtrează ultimele N zile
    cutoff_timestamp = time.time() - (lookback_days * 86400)
    data = [(ts, p) for ts, p in data if ts/1000 > cutoff_timestamp]
    
    
    timestamps, prices = zip(*data)
    timestamps = np.array(timestamps) / 1000  # conversie din ms în secunde
    prices = np.array(prices)
    
    delta = np.median(np.diff(timestamps))
    points_per_hour = int(3600 / delta) # cate secunde am intr-o ora ditribuite per puncte de pret
    window = points_per_hour * window_hours # numar de puncte per fereastra
    window = min(window, len(prices))       # window size is never larger than the number of price points:
    step = points_per_hour * step_hours     # numar de puncte per step
    
    print(f"[DEBUG] {symbol}: numar puncte={len(prices)}, window={window}, step={step}, delta(s)={delta}")
    print(f"[DEBUG] {symbol}: numar de ferestre={len(prices)/window}, numar de pasi in price {len(prices)/step}")
    print(f"[DEBUG] {symbol}: slope_tolerance={slope_tolerance}")
 
    last_slope_h = None
    sum_slope = 0
    trend_start_ts = timestamps[-1]
    trend_ref_slope_h = None
    trend_ref_count = 1
    trend_block = 0
    trend_block_ups = 0
    trend_block_indices = []
    
    trend_block_indices_test=[]
    
    for start in range(len(prices) - window, -1, -step):
        print(f"[DEBUG] start {start}")
        trend_block +=1
        end = start + window
        x_block = timestamps[start:end] - timestamps[start]
        y_block = prices[start:end]

        slope_s, intercept = np.polyfit(x_block, y_block, 1) # cu cat creste pe secunda - viteza slope
        
        trend_block_indices_test.append((0, window))
            
        #print(f"[DEBUG] {symbol}: start={start}, end={end}, slope={slope:.6f}")
        slope_h = slope_s * 3600 # slope per h
        if slope_h > 0 :
            trend_block_ups +=1

        #avg_price = np.mean(y_block)
        avg_price = prices[0]
        relative_tolerance = slope_tolerance_per_(symbol, avg_price, slope_tolerance) 

        print(f"[DEBUG] {symbol}: relative_tolerance={relative_tolerance}, slope_h={slope_h}, last_slope_h={last_slope_h}")
        #drawPriceLst(x_block, y_block, trend_block_indices, symbol, "up", slope_h)
     
        if trend_ref_slope_h is None or last_slope_h is None:
            trend_ref_slope_h = slope_h
            trend_start_ts = timestamps[start]
            last_slope_h = slope_h

        continue_trend = True
                    
        if(trend_ref_slope_h * slope_h < 0): # semn trend diferit
            if(len(trend_block_indices) == 0):
                continue
            avg_slope = sum_slope / len(trend_block_indices)
            print(f"[DEBUG] trendul curent difera {slope_h}. Se compara cu trend_ref_slope_h={trend_ref_slope_h} si avg_slope={avg_slope}")
            if abs(slope_h - trend_ref_slope_h) >= relative_tolerance: # diferență semificativa fata de trend start
                continue_trend = False;
            if abs(slope_h - avg_slope) >= relative_tolerance:  # diferență mare fata de medie
                continue_trend = False;
        else:
            continue_trend = True
                        
        if continue_trend:
            if (trend_ref_slope_h * slope_h < 0): # semn schimbat
                trend_ref_slope_h = slope_h
                trend_ref_count = 1
                print(f"CONTINUE ... ")
            else : # medie sau ceva 
                
                #w = 1 w < 1 => media veche contează mai puțin decât un singur număr nou 
                #trend_ref_slope_h = (w * trend_ref_slope_h + slope_h) / (w + 1)              
                trend_ref_slope_h =  (trend_ref_slope_h * trend_ref_count + slope_h) / (trend_ref_count + 1);
                trend_ref_count += 1
            
            sum_slope += slope_h            
            trend_block_indices.append((start, end))
            last_slope_h = slope_h
        else:
            # trendul s-a rupt
            print(f"BREAK!")
            break
               
    duration_seconds = timestamps[-1] - trend_start_ts
    duration_hours = duration_seconds / 3600
    estimated_future_hours = duration_hours * persistence_factor
    
    if duration_seconds <= 0:
        print(f"[{symbol}] duration_seconds={duration_seconds}, insuficient date pentru trend.")
        return None
    
    if trend_ref_slope_h is None:
        print(f"[{symbol}] trend_ref_slope_h este None, nu se poate determina direcția trendului.")
        return None        # Not enough data to calculate slope
    
    print(f"trend_block {trend_block} and trend_block_ups {trend_block_ups}")
    trend_direction = 'up' if trend_ref_slope_h > 0 else 'down'

    if draw:
        drawPriceLst(timestamps, prices, trend_block_indices, symbol, trend_direction, duration_hours)

    return {
        'timestamp': int(time.time()),
        'direction': trend_direction,
        'start_timestamp': trend_start_ts,
        'duration_seconds': duration_seconds,
        'estimated_future_hours': estimated_future_hours
    }

            

def format_duration(seconds):
    """Convertește secunde în format citibil: Xd Yh Zm"""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    
    return " ".join(parts) if parts else "0m"


def format_timestamp(ts):
    """Convertește timestamp în format citibil"""
    return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


# Minim de puncte într-o fereastră ca slope-ul să fie credibil; sub el = gap.
MIN_POINTS_PER_WINDOW = 4


def detect_long_term_trend(timestamps, prices, window_hours=24, step_hours=8,
                           min_consecutive_blocks=3, noise_tolerance=2,
                           min_points_per_window=MIN_POINTS_PER_WINDOW,
                           detection_lag_hours=0.0, mk_alpha=None):
    """Detectează trendul pe ferestre definite în TIMP (nu în număr de puncte),
    deci robust la densitate neuniformă și găuri — fără să modifice datele brute.

    timestamps: secunde, sortate crescător. Fereastra acoperă [t, t+window_hours];
    pasul e step_hours reali. O fereastră cu < min_points_per_window puncte = gap
    și OPREȘTE trendul acolo (nu inventăm date peste gol).

    Întoarce dict {direction, start_timestamp, duration_seconds,
    estimated_future_hours, current_slope_h, blocks(perechi de indici pt desen)} sau None.
    """
    timestamps = np.asarray(timestamps, dtype=float)
    prices = np.asarray(prices, dtype=float)
    if len(timestamps) < 2:
        return None

    t_end, t_first = timestamps[-1], timestamps[0]
    window_sec = window_hours * 3600.0
    step_sec = step_hours * 3600.0

    def slope_h(t_lo, t_hi):
        """slope/oră pe punctele din [t_lo, t_hi) + (lo, hi) indici; None dacă gap."""
        lo = int(np.searchsorted(timestamps, t_lo, "left"))
        hi = int(np.searchsorted(timestamps, t_hi, "left"))
        if hi - lo < min_points_per_window:
            return None, (lo, hi)
        x, y = timestamps[lo:hi], prices[lo:hi]
        s, _ = np.polyfit(x - x[0], y, 1)
        return s * 3600.0, (lo, hi)

    cur, cur_idx = slope_h(t_end - window_sec, t_end + 1.0)
    if cur is None:
        return None                                  # date recente insuficiente
    if mk_alpha:
        # filtru Mann-Kendall: panta ferestrei CURENTE trebuie sa fie un trend
        # SEMNIFICATIV statistic, nu zgomot — altfel nu raportam directie
        from trend_stats import mann_kendall
        _, _, p_mk = mann_kendall(prices[cur_idx[0]:cur_idx[1]])
        if p_mk > mk_alpha:
            return None
    current_sign = np.sign(cur) or 1.0

    blocks = [cur_idx]
    consecutive, noise = 1, 0
    confirm_lo = cur_idx[0]      # cel mai vechi punct care CONFIRMA directia curenta
    confirm_pos = 0              # pozitia in blocks a ultimului bloc confirmant
    t_ws = t_end - window_sec - step_sec
    while t_ws >= t_first:
        s, idx = slope_h(t_ws, t_ws + window_sec)
        if s is None:
            break                                    # gap → nu confirmăm peste gol
        if np.sign(s) == current_sign:
            blocks.append(idx); consecutive += 1; noise = 0
            confirm_lo = idx[0]; confirm_pos = len(blocks) - 1
        elif noise < noise_tolerance:
            noise += 1; blocks.append(idx)           # tentativ: poate trendul continua dincolo de zgomot
        else:
            break                                    # zgomot peste toleranta → trendul s-a terminat aici
        t_ws -= step_sec

    # fara minim de blocuri CONFIRMATE in directia curenta nu exista trend coerent:
    # un bounce de o zi contra unei scaderi de 4 zile NU e "trend UP de 4 zile".
    if consecutive < min_consecutive_blocks:
        return None

    blocks = blocks[:confirm_pos + 1]                # coada de zgomot neconfirmata nu face parte din trend
    # durata = intervalul CONFIRMAT + lag-ul de detectie (trendul incepe in realitate
    # INAINTE ca detectorul sa-l confirme — corectie explicita, ex. ~2 zile).
    # PLAFONAT la span-ul datelor: durata nu poate depasi cat istoric avem.
    confirmed_start = float(timestamps[confirm_lo])
    duration_seconds = (t_end - confirmed_start) + detection_lag_hours * 3600.0
    duration_seconds = min(duration_seconds, t_end - t_first)
    trend_start_ts = t_end - duration_seconds
    if duration_seconds <= 0:
        return None
    return {
        'direction': 'up' if current_sign > 0 else 'down',
        'start_timestamp': float(trend_start_ts),
        'duration_seconds': float(duration_seconds),
        'estimated_future_hours': float(duration_seconds / 3600.0 * 0.5),
        'current_slope_h': float(cur),
        'blocks': blocks,
    }


def getTrendLongTerm_fixed(symbol: str, window_hours: int = 24, step_hours: int = 8,
                           min_consecutive_blocks: int = 3,
                           noise_tolerance: int = 2,  # ← NOU: permite 2 blocuri zgomot
                           lookback_days: int = 30,
                           draw: bool = True,
                           min_points_per_window: int = MIN_POINTS_PER_WINDOW,
                           detection_lag_hours: float = 48.0,
                           mk_alpha: float = 0.05) -> Optional[dict]:
    # detection_lag_hours: trendul incepe in realitate INAINTE sa-l confirme
    # detectorul — euristica ta empirica: ~2 zile. Explicit, nu ascuns in formula.
    # mk_alpha: filtru Mann-Kendall pe fereastra curenta (trendul trebuie sa fie
    # semnificativ statistic, nu zgomot); None = dezactivat.
    data: List[Tuple[int, float]] = priceLstFor(symbol)
    if len(data) < 2:
        return None

    data = sorted(data, key=lambda x: x[0])
    
    # Filtrează ultimele N zile
    cutoff_timestamp = time.time() - (lookback_days * 86400)
    data = [(ts, p) for ts, p in data if ts/1000 > cutoff_timestamp]
    
    if len(data) < 2:
        print(f"[{symbol}] Insuficiente date în ultimele {lookback_days} zile")
        return None
    
    timestamps, prices = zip(*data)
    timestamps = np.array(timestamps) / 1000      # ms → secunde
    prices = np.array(prices)

    # Calcul pe ferestre definite în TIMP (robust la densitate neuniformă + găuri).
    res = detect_long_term_trend(
        timestamps, prices, window_hours=window_hours, step_hours=step_hours,
        min_consecutive_blocks=min_consecutive_blocks, noise_tolerance=noise_tolerance,
        min_points_per_window=min_points_per_window,
        detection_lag_hours=detection_lag_hours, mk_alpha=mk_alpha)

    if res is None:
        print(f"[{symbol}] Trend indeterminabil (date insuficiente, gap sau nesemnificativ MK).")
        return None

    # regimul seriei (Hurst): persistent = trend-following favorizat;
    # mean-reverting = trendurile mor repede (informativ, atasat rezultatului)
    from trend_stats import hurst_rs, hurst_regime
    h = hurst_rs(prices)
    res['hurst'] = h
    res['regime'] = hurst_regime(h)
    print(f"[{symbol}] Hurst={h:.2f} ({res['regime']})" if h else f"[{symbol}] Hurst: serie prea scurta")

    direction = res['direction']
    emoji = "📈" if direction == 'up' else "📉"
    dur = res['duration_seconds']
    print(f"\n{'='*60}")
    print(f"[{symbol}] Trend {emoji} {direction.upper()} | slope/h={res['current_slope_h']:.4f}")
    print(f"  Puncte (ultimele {lookback_days}z): {len(prices)} | fereastră={window_hours}h "
          f"pas={step_hours}h | blocuri={len(res['blocks'])}")
    print(f"  Start: {format_timestamp(res['start_timestamp'])} | "
          f"Durată: {format_duration(dur)} ({dur/86400:.1f} zile)")
    print(f"{'='*60}\n")

    if draw:
        drawPriceLst(timestamps, prices, res['blocks'], symbol, direction, dur / 3600.0)

    return {
        'timestamp': int(time.time()),
        'direction': direction,
        'start_timestamp': res['start_timestamp'],
        'duration_seconds': dur,
        'estimated_future_hours': res['estimated_future_hours'],
    }

# Și pentru write_all_trends, adaugă formatare:
def write_all_trends(all_trends, filename="priceanalysis.json"):
    """Scrie rezultatele în JSON + afișare human-readable"""
    
    print("\n" + "="*80)
    print("SUMAR TRENDURI".center(80))
    print("="*80)
    
    for symbol, trend_data in all_trends.items():
        if trend_data is None:
            print(f"\n{symbol}: ❌ Fără date suficiente")
            continue
            
        direction = trend_data['direction']
        emoji = "📈" if direction == 'up' else "📉"
        
        start_str = format_timestamp(trend_data['start_timestamp'])
        duration_str = format_duration(trend_data['duration_seconds'])
        duration_days = trend_data['duration_seconds'] / 86400
        
        future_hours = trend_data['estimated_future_hours']
        future_str = format_duration(future_hours * 3600)
        future_days = future_hours / 24
        
        print(f"\n{symbol}")
        print(f"  {emoji} {direction.upper()}")
        print(f"  Start:    {start_str}")
        print(f"  Durată:   {duration_str} ({duration_days:.1f} zile)")
        print(f"  Estimat:  ~{future_str} ({future_days:.1f} zile)")
    
    print("\n" + "="*80 + "\n")
    
    try:
        with open(filename, "w") as f:
            json.dump(all_trends, f, indent=2)
        print(f"✅ Rezultatele au fost scrise în {filename}")
    except Exception as e:
        print(f"❌ Eroare scriere {filename}: {e}")
    
    return all_trends
    
    
def write_all_trends_old(symbols, filename="priceanalysis.json"):      
    try:
        with open(filename, "w") as f:
            json.dump(all_trends, f, indent=2)
        print(f"[write_all_trends] Rezultatele au fost scrise în {filename}")
    except Exception as e:
        print(f"[write_all_trends][Eroare] Nu pot scrie fișierul {filename}: {e}")
    return all_trends



def get_weight_for_cash_permission_at_quant_time(symbol, order_type, T_quanta=None, quant_seconds=3600*24, draw=False):
    """T_quanta=None (implicit) = AUTO: T estimat EMPIRIC din istoricul monedei
    (hibrid cu prior-ul 14, favorizand empiricul cand avem episoade destule),
    specializat per simbol si tinut in cache pe disc (trend_survival.estimate_T).
    Poti da explicit T_quanta=14 ca sa fortezi comportamentul vechi."""
    import cacheManager as cm
    global last_timestamp
    global last_w

    if T_quanta is None:
        try:
            from trend_survival import estimate_T
            est = estimate_T(symbol)
            T_quanta = est["T"]
            print(f"[{symbol}] T AUTO (empiric hibrid): {T_quanta} zile  "
                  f"(n={est['n']} episoade, w_empiric={est['w']}, "
                  f"mediana={est.get('median_d')}z, P90={est.get('p90_d')}z)")
        except Exception as e:
            T_quanta = 14
            print(f"[{symbol}] estimarea T a esuat ({e}) — folosesc prior T=14")

    all_trend_data = cm.get_cache_manager("PriceLongTrend").cache
    if symbol not in all_trend_data:
        print(f"Simbolul {symbol} nu există în trendurile citite.")
        return None
    trend = all_trend_data[symbol][0]
    if trend is None:
        print(f" No trend in cache for symbol {symbol}.")
        return None
    
    duration_days = trend["duration_seconds"] / 86400
    print(f"Trend citit din manager cache pentru simbolul {symbol}: {trend}")
    print(f"   Start trend:     {format_timestamp(trend["start_timestamp"])}")
    print(f"   Durată:          {format_duration(trend["duration_seconds"])} ({duration_days:.1f} zile)")
    timestamp = trend['timestamp']
    # cheia memo include order_type (BUY/SELL au ponderi total diferite) si T
    # (T-ul auto se poate schimba la reestimare — nu servim ponderi pt alt T)
    memo_key = (symbol, order_type.upper(), T_quanta)
    if timestamp == last_timestamp.get(memo_key):
        cached_w = last_w.get(memo_key)
        if cached_w is not None and len(cached_w) > 0 and not np.isnan(cached_w[0]):
            print(f"not new timestamp, use weight from mem cache.")
            return float(cached_w[0])

    trend_len_quanta = trend.get('duration_seconds', 0) / quant_seconds
    if trend_len_quanta <= 0:
        print(f"[{symbol}] duration_seconds invalid, return None")
        return None

    direction = trend['direction']

    t, w = get_trade_weight(
        T=T_quanta,
        trend_len=trend_len_quanta,
        trend=direction,
        order_type=order_type
    )

    if len(w) == 0:
        print(f"[{symbol}] w gol, return None")
        return None

    current_weight = float(w[0])  # ← w[0] e deja ponderea curentă (slice)

    if np.isnan(current_weight) or current_weight <= 0:
        print(f"[{symbol}] Pondere invalidă: {current_weight}, return None")
        return None

    print(f"[{symbol}] primele 5 ponderi: {w[:5]}")
    print(f"Suma tuturor {len(w)} ponderi = {w.sum():.4f}")

    if draw:
        plt.plot(t, w, label=symbol)
        plt.legend()
        plt.show()

    last_w[memo_key] = w        # w e deja slice de la current_pos
    last_timestamp[memo_key] = timestamp
    return current_weight     # w[0] = ponderea pentru acum

last_timestamp = {}
last_w = {}


#Zona 1: 0 → T          = gaussian (confident la mijloc, nesigur la capete)
#Zona 2: T → T*(1+proc) = trend depășit dar persistent → pondere mare (0.86)
#Zona 3: > T*(1+proc)   = trend foarte bătrân → poate fi orice → conservator (0.22)

#ALINIAT (BUY+UP sau SELL+DOWN): gaussiana scalata la VARF=peak_weight
#mijloc → 0.95 (tranzacționezi maxim)   capete → ~0.17 (tranzacționezi puțin)

#CONTRA-TREND (SELL+UP sau BUY+DOWN): inversul gaussienei GLOBALE
#mijloc → 0.02 (nu tranzacționezi)      AMBELE capete → ~0.13-0.15

def get_trade_weight(T, trend_len, trend, order_type,
                     exceed_percent=0.4, max_against_trend=0.15,
                     peak_weight=0.95, min_weight=0.02, lindy_plateau=True):
    aligned = (
        (order_type.upper() == "BUY"  and trend == "up") or
        (order_type.upper() == "SELL" and trend == "down")
    )

    T_extended = T * (1 + exceed_percent)

    # ZONA 2: trend depășit dar persistent → momentum puternic in directia lui
    if T < trend_len <= T_extended:
        w_val = 0.86 if aligned else max_against_trend
        print(f"[DEBUG] Zona 2: trend_len={trend_len:.2f} depășește T={T} dar e sub T_extended={T_extended}. Aligned={aligned}, return {w_val}  ")
        return np.array([0.0]), np.array([w_val])

    # ZONA 3: trend foarte bătrân → conservator IN AMBELE directii
    if trend_len > T_extended:
        w_val = 0.22 if aligned else max_against_trend
        print(f"[DEBUG] Zona 3: trend_len={trend_len:.2f} e peste T_extended={T_extended}. Aligned={aligned}, return {w_val} ")
        return np.array([0.0]), np.array([w_val])

    # ZONA 1: gaussiana pe T întreg, slice de la vârsta curentă a trendului.
    # idx plafonat la T-1: la trend_len == T slice-ul nu mai e gol (cusatura cu Zona 2).
    idx = min(int(trend_len), T - 1)
    t_full, w_full = u.gaussian_weights_from_idx(T=T, idx=0)
    if len(w_full) == 0:
        print(f"[DEBUG] Zona 1: gaussian_weights_from_idx a returnat gol. return [0.05]")
        return np.array([0.0]), np.array([0.05])
    # utils normalizeaza ca DISTRIBUTIE (suma=1, varf ~0.11) — pt ponderi de trading
    # scalam la VARF: mijlocul curbei = peak_weight, nu ~0.11 (bug-ul vechi de scara,
    # care facea Zona 1 de 8-40x mai mica decat Zona 2)
    w01_full = w_full / w_full.max()                  # 0..1, varful = 1
    if lindy_plateau:
        # IPOTEZA (Marius) VALIDATA EMPIRIC (trend_survival.py pe BTC 700z + TAO 450z):
        # P(trendul mai tine o zi | a tinut t zile) ramane ~0.65-0.75 si DUPA mijloc
        # (efect Lindy), NU scade cum presupune coada dreapta a gaussienei.
        # => dupa varf ne purtam ca la mijloc: PLAFON la varf, nu coborare.
        peak_i = int(np.argmax(w01_full))
        w01_full = w01_full.copy()
        w01_full[peak_i:] = 1.0
    t_seq, w01 = t_full[idx:], w01_full[idx:]
    print(f"[DEBUG] Zona 1: trend_len={trend_len:.2f}, slice de la idx={idx} până la T={T}. Aligned={aligned}, gauss01[0]={w01[0]:.4f}")

    if aligned:
        w_seq = w01 * peak_weight
    else:
        # inversul curbei GLOBALE (nu al slice-ului — bug-ul vechi dadea 0.02 la
        # capatul batran in loc de ~0.15): mijloc -> min_weight, capete -> max_against_trend
        print(f"[DEBUG] Order type {order_type} nu e aliniat cu trend {trend}, invers global, max_against_trend={max_against_trend}")
        w_seq = min_weight + (1.0 - w01) * (max_against_trend - min_weight)

    return t_seq, w_seq  # slice [idx..T-1]
    
    
    
    
UPDATE_AND_REFRESH_TREND = 60*1 # un minut 
#UPDATE_AND_REFRESH_TREND = PRICETREND_SYNC_INTERVAL_SEC*2
#
#
if __name__ == "__main__":
    #shm = shmu.shmConnectForWrite(shmu.shmname)
    build_price_cache_manager()
    symbols = sym.symbols
    try:
        while True:
            process = psutil.Process(os.getpid())
            print("Memorie folosită (MB):", process.memory_info().rss / 1024**2)
            
            all_trends = {}
            for symbol in symbols:
                #all_trends[symbol] = getTrendLongTerm(symbol,lookback_days=30, draw=True)
                all_trends[symbol] = getTrendLongTerm_fixed(symbol, 
                                            window_hours=16,
                                            step_hours=8,
                                            min_consecutive_blocks=3,
                                            noise_tolerance=2,  # ← permite 2 blocuri UP în trendul DOWN
                                            lookback_days=30,
                                            draw=True)
                #get_weight_for_cash_permission_at_quant_time(symbol, T_quanta=275, order_type="BUY", draw=True)
            write_all_trends(all_trends);

            print(f"write : {all_trends}")
            #shmu.shmWrite(shm, all_trends)
            time.sleep(UPDATE_AND_REFRESH_TREND)
    except KeyboardInterrupt:
        print(f"Închidere manuală...")
    #except Exception as e:
        #print(f"Oprire ? ...{e}")
    #finally:
        #shm.close()
        #shm.unlink()
        
    

######################
