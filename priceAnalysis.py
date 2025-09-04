
import time
import json
from multiprocessing import shared_memory

import numpy as np
import matplotlib.pyplot as plt

#my import
import utils as u
import shmutils as shmu




import os
import math
import time
import json
import psutil

from datetime import datetime
from typing import List, Dict, Tuple, Optional

import matplotlib.pyplot as plt
import numpy as np
from multiprocessing import shared_memory

### my import
import symbols as sym
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
    manager.save_state()
    
    return [(int(ts), float(p)) for ts, p in raw]


def drawPriceLst(timestamps, prices, trend_block_indices, symbol, trend_direction, duration_hours):
    # Vizualizare
    #plt.clf()  # curăță figura curentă (nu creează alta)
    plt.figure(figsize=(12,5))
    plt.plot(timestamps, prices, label='Price', color='blue')

    # Evidențiem blocurile de trend
    for start, end in trend_block_indices:
        plt.plot(timestamps[start:end], prices[start:end], color='red', linewidth=2)

    plt.xlabel('Timestamp')
    plt.ylabel('Price')
    plt.title(f"{symbol} - Trend {trend_direction}, durata {duration_hours:.2f}h")
    plt.legend()
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



def getTrendLongTerm(symbol: str, window_hours: int = 3, step_hours: int = 1,
                                slope_tolerance: float = 1e-5, persistence_factor: float = 1.5
                               , draw: bool = True) -> Optional[dict]:
   
    data: List[Tuple[int, float]] = priceLstFor(symbol)
    if len(data) < 2:
        return None

    data = sorted(data, key=lambda x: x[0]) 
    timestamps, prices = zip(*data)
    timestamps = np.array(timestamps) / 1000  # conversie din ms în secunde
    prices = np.array(prices)

    delta = np.median(np.diff(timestamps))
    points_per_hour = int(3600 / delta)
    window = points_per_hour * window_hours
    window = min(window, len(prices))  #window size is never larger than the number of price points:
    step = points_per_hour * step_hours
    
    print(f"[DEBUG] {symbol}: număr puncte={len(prices)}, window={window}, step={step}")

    last_slope = None
    trend_start_ts = timestamps[-1]
    trend_blocks = 0
    trend_block_indices = []

    for start in range(len(prices) - window, -1, -step):
        end = start + window
        x_block = timestamps[start:end] - timestamps[start]
        y_block = prices[start:end]

        slope, intercept = np.polyfit(x_block, y_block, 1)
        #print(f"[DEBUG] {symbol}: start={start}, end={end}, slope={slope:.6f}")

        if last_slope is None or abs(slope - last_slope) <= slope_tolerance:
            trend_start_ts = timestamps[start]
            trend_blocks += 1
            trend_block_indices.append((start, end))
            last_slope = slope
        else:
            break

    duration_seconds = timestamps[-1] - trend_start_ts
    duration_hours = duration_seconds / 3600
    estimated_future_hours = duration_hours * persistence_factor
    
    if last_slope is None:
        # Not enough data to calculate slope
        return None
    trend_direction = 'up' if last_slope > 0 else 'down'

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
    except Exception as e:
        print(f"Oprire ? ...{e}")
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


def get_weight_for_cash_permission_at_quant_time(symbol, T_quanta=14, quant_seconds=3600*24, draw=False):
    import cacheManager as cm
    global last_timestamp
    global last_w
    
    # data = shmu.shmRead(shm)
    # if data is None:
        # print(f"Nu există date în shared memory încă.")
        # return None
    data = cm.get_price_trend_cache_manager().cache
    if symbol not in data:
        print(f"Simbolul {symbol} nu există în trendurile citite.")
        return None

    print(f"Data from cache {data}")
    trend = data[symbol]
    
    timestamp = trend['timestamp']
    if last_timestamp.get(symbol) is not None and timestamp == last_timestamp[symbol]:
        print(f"not new timestamp,  use data from cache.")
        return last_w[symbol]
        
    last_timestamp[symbol] = timestamp
                
    # convertim last_period din secunde în număr de quanta
    last_period_quanta = trend['duration_seconds']  / quant_seconds
    direction = trend['direction']

    # apelăm gaussian_full_shifted cu T și last_period în aceeași unitate (quanta)
    t, w = u.gaussian_full_shifted(T=T_quanta, last_period=last_period_quanta, trend=direction)
    
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
    return w[0]


#shm = shmu.shmConnectForRead(shmu.shmname)
last_timestamp = {}
last_w = {}