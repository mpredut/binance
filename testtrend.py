
import time
import json
from multiprocessing import shared_memory

import numpy as np
import matplotlib.pyplot as plt

#my import
import utils as u
import shmutils as shmu






def get_weight_for_cash_permission(symbol, T=14*24):
    global last_timestamp
    """
    Primește un simbol și returnează prima pondere gaussiană bazată pe trendul curent.
    - T = 2 săptămâni în ore
    """
    data = shmu.shmRead(shm)
    if data is None:
        print(f"Nu există date în shared memory încă.")
        return None
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


def get_weight_for_cash_permission_at_quant_time(symbol, T_quanta=14, quant_seconds=3600*24):
   
    global last_timestamp
    
    data = shmu.shmRead(shm)
    if data is None:
        print(f"Nu există date în shared memory încă.")
        return None
    if symbol not in data:
        print(f"Simbolul {symbol} nu există în trendurile citite.")
        return None

    trend = data[symbol]
    
    timestamp = trend['timestamp']
    if last_timestamp.get(symbol) is not None and timestamp == last_timestamp[symbol]:
        print(f"timestamp wrong in fc")
        return None
    last_timestamp[symbol] = timestamp
                
    # convertim last_period din secunde în număr de quanta
    last_period_quanta = trend['duration_seconds']  / quant_seconds
    direction = trend['direction']

    # apelăm gaussian_full_shifted cu T și last_period în aceeași unitate (quanta)
    _, w = u.gaussian_full_shifted(T=T_quanta, last_period=last_period_quanta, trend=direction)
    
    # returnăm prima pondere pentru primul quanta
    if len(w) == 0:
        print("Vectorul ponderilor este gol.")
        return None
    
    return w[0]


shm = shmu.shmConnectForRead(shmu.shmname)
last_timestamp = {}

try:
    while True:
         
        data = shmu.shmRead(shm)

        if data is None:
            print("⚠️ Nimic scris încă în shared memory...")
            shm = shmu.shmConnectForRead(shmname)
            time.sleep(1)  # mică pauză să nu blocheze CPU
            continue
        
        last_timestamp = {}
        for symbol, trend in data.items():
            timestamp = trend['timestamp']
            if(timestamp == last_timestamp.get(symbol)):
                shm = shmu.shmConnectForRead(shmname)
                break
            last_timestamp[symbol] = timestamp
            
            direction = trend['direction']
            last_period = trend['duration_seconds']
            
            print(f"[{symbol}] timestamp = {u.timeToHMS(timestamp)}")
            print(f"[{symbol}] direction = {direction}, duration_hours = {last_period/3600} h")

            # folosim funcția gaussiană
            t, w = u.gaussian_full_shifted(T=15*24, last_period=last_period, trend=direction)

            # exemplu: afișăm suma ponderilor și primele valori
            print(f"[{symbol}] suma ponderilor = {w.sum():.2f}")
            print(f"[{symbol}] primele 5 ponderi: {w[:5]}")
            sum_first_24 = w[:24].sum()
            print(f"Suma primelor 24 ponderi =", sum_first_24)

            # dacă vrei să vizualizezi
            plt.plot(t, w, label=symbol)
            
            #gw = get_weight_for_cash_permission(symbol)
            gw = get_weight_for_cash_permission_at_quant_time(symbol)
            if gw is None:
                shm = shmu.shmConnectForRead(shmu.shmname)
                break
            print(f"get_weight_for_cash_permission= {gw}");
        
        plt.legend()
        #plt.show()

        time.sleep(10)
    
    

except KeyboardInterrupt:
    print(f"Oprire manuală...")
    shm.close()
#except :
#    print(f"Oprire ? ...")
finally:
    shm.close()
    #shm.unlink()
shm.close()