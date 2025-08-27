
import time
import json
from multiprocessing import shared_memory

import numpy as np
import matplotlib.pyplot as plt

def gaussian_full_shifted1(T, last_period, trend="down", steps=None):
    remaining = max(T - last_period, 1)

    if steps is None:
        steps = remaining

    t = np.linspace(0, remaining-1, steps)

    mu = (remaining - 1) / 2
    sigma = remaining / 4

    w = np.exp(-0.5 * ((t - mu) / sigma) ** 2)

    if trend == "down":
        w_normalized = w / w.max()
        w = 1 - w_normalized
        w = w / w.sum()
    else:
        w = w / w.sum()

    return t, w


def gaussian_full_shifted(T, last_period, trend="down", steps=None):
    remaining = int(max(T - last_period, 1))

    if steps is None:
        steps = remaining
    else:
        steps = int(steps)

    t = np.linspace(0, remaining - 1, steps)

    mu = (remaining - 1) / 2
    sigma = remaining / 4

    w = np.exp(-0.5 * ((t - mu) / sigma) ** 2)

    if trend == "down":
        w_normalized = w / w.max()
        w = 1 - w_normalized
        w = w / w.sum()
    else:
        w = w / w.sum()

    return t, w



BUF_SIZE = 1024
shm = None

while shm is None:
    try:
        shm = shared_memory.SharedMemory(name="trend_data")
    except FileNotFoundError:
        print("Shared memory nu există încă. Aștept...")
        time.sleep(1)

print("Conectat la shared memory!")

def read_trends():
    length = int.from_bytes(shm.buf[:4], "little")
    if length == 0:
        return None  # nimic scris încă
    raw = bytes(shm.buf[4:4+length])
    return json.loads(raw.decode("utf-8"))
    


def get_symbol_first_weight(symbol, T=14*24):
    """
    Primește un simbol și returnează prima pondere gaussiană bazată pe trendul curent.
    - T = 2 săptămâni în ore
    """
    data = read_trends()
    if data is None:
        raise ValueError("Nu există date în shared memory încă.")
    if symbol not in data:
        raise ValueError(f"Simbolul {symbol} nu există în trendurile citite.")

    trend = data[symbol]
    last_period = trend['duration_hours']
    direction = trend['direction']

    _, w = gaussian_full_shifted(T=T, last_period=last_period, trend=direction)
    return w[0]  # prima pondere



try:
    while True:
         
        data = read_trends()

        if data is None:
            print("⚠️ Nimic scris încă în shared memory...")
            continue
            
        for symbol, trend in data.items():
            direction = trend['direction']
            duration_h = trend['duration_hours']

            print(f"[{symbol}] direction = {direction}, duration_hours = {duration_h} h")

            # folosim funcția gaussiană
            t, w = gaussian_full_shifted(T=3*24, last_period=duration_h, trend=direction)

            # exemplu: afișăm suma ponderilor și primele valori
            print(f"[{symbol}] suma ponderilor = {w.sum():.2f}")
            print(f"[{symbol}] primele 5 ponderi: {w[:5]}")

            # dacă vrei să vizualizezi
            plt.plot(t, w, label=symbol)
            
            print(f"get_symbol_first_weight= {get_symbol_first_weight(symbol)}");
        
        plt.legend()
        plt.show()

        time.sleep(10)
    
    

except KeyboardInterrupt:
    print("Oprire manuală...")
finally:
    shm.close()
    shm.unlink()
