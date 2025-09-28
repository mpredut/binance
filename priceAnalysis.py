
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
from multiprocessing import shared_memory
import shmutils as shmu

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
                                slope_tolerance: float = 0.0028, persistence_factor: float = 1.5
                               , draw: bool = True) -> Optional[dict]:
   
    data: List[Tuple[int, float]] = priceLstFor(symbol)
    if len(data) < 2:
        return None

    data = sorted(data, key=lambda x: x[0]) 
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
        print(f"[DEBUG] start{start}")
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
    
    if trend_ref_slope_h is None:
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

                
def write_all_trends(symbols, filename="priceanalysis.json"):      
    try:
        with open(filename, "w") as f:
            json.dump(all_trends, f, indent=2)
        print(f"[write_all_trends] Rezultatele au fost scrise în {filename}")
    except Exception as e:
        print(f"[write_all_trends][Eroare] Nu pot scrie fișierul {filename}: {e}")
    return all_trends


REFRESH_TREND = 60*1 # un minut 
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
                all_trends[symbol] = getTrendLongTerm(symbol, draw=False)
            write_all_trends(all_trends);

            print(f"write : {all_trends}")
            #shmu.shmWrite(shm, all_trends)
            time.sleep(REFRESH_TREND)
    except KeyboardInterrupt:
        print(f"Închidere manuală...")
    #except Exception as e:
        #print(f"Oprire ? ...{e}")
    #finally:
        #shm.close()
        #shm.unlink()
        
    #shm.close()
    #shm.unlink()
    

######################

def get_weight_for_cash_permission(symbol, T=14*24):
    import cacheManager as cm
    global last_timestamp
    """
    Primește un simbol și returnează prima pondere gaussiană bazată pe trendul curent.
    - T = 2 săptămâni în ore
    # """
    # data = shmu.shmRead(shm)
    # if data is None:
        # print(f"Nu există date în shared memory încă.")
        # return None
    data = cm.get_price_trend_cache_manager().cache
    if symbol not in data:
        print(f"Simbolul {symbol} nu există în trendurile citite.")
        return None

    trend = data[symbol]
    
    timestamp = trend['timestamp']
    #if(timestamp == last_timestamp.get(symbol)):
    if last_timestamp.get(symbol) is not None and timestamp == last_timestamp[symbol]:
        print(f"timestamp wrong in fc")
        return None
    last_timestamp[symbol] = timestamp
                
    last_period = trend['duration_seconds']
    direction = trend['direction']

    _, w = u.gaussian_full_shifted(T=T, last_period=last_period, trend=direction)
    return w[0]  # prima pondere


# mylock = threading.Lock()  # lock global
def get_weight_for_cash_permission_at_quant_time(symbol, order_type, T_quanta=14, quant_seconds=3600*24, draw=False):
    import cacheManager as cm
    global last_timestamp
    global last_w
    
    # data = shmu.shmRead(shm)
    # if data is None:
        # print(f"Nu există date în shared memory încă.")
        # return None
    data = cm.get_cache_manager("PriceTrend").cache
    if symbol not in data:
        print(f"Simbolul {symbol} nu există în trendurile citite.")
        return None

    print(f"Data from cache {data}")
    trend = data[symbol][0]
    
    timestamp = trend['timestamp']
    
    #with mylock:  # blocăm accesul la last_timestamp și last_w
    if last_timestamp.get(symbol) is not None and timestamp == last_timestamp[symbol]:
        print(f"not new timestamp,  use data from cache.")
        return last_w[symbol][0]
    #am mutat pt race condition    
    #last_timestamp[symbol] = timestamp
                
    # convertim last_period din secunde în număr de quanta
    last_period_quanta = trend['duration_seconds']  / quant_seconds
    direction = trend['direction']

    # apelăm gaussian_full_shifted cu T și last_period în aceeași unitate (quanta)
    t, w = u.gaussian_full_shifted(T=T_quanta, last_period=last_period_quanta, trend=direction)
    if(order_type.upper() == "SELL"):
        if(direction=="UP"):
            w = w / 2
        if(direction=="DOWN"):
            w = 2 * (1 - w)
              
    print(f"[{symbol}] primele 5 ponderi: {w[:5]}")
    sum_first_24 = w[:24].sum()
    print(f"Suma tuturor {len(w)} ponderi =", sum_first_24)

    # dacă vrei să vizualizezi
    if draw:
        plt.plot(t, w, label=symbol)
        plt.legend()
        plt.show()
    
    # returnăm prima pondere pentru primul quanta
    if len(w) == 0:
        print("Vectorul ponderilor este gol.")
        return None
   
    last_w[symbol] = w
    last_timestamp[symbol] = timestamp
    return w[0]


#shm = shmu.shmConnectForRead(shmu.shmname)
last_timestamp = {}
last_w = {}
