import os
import time
import math
import random
import platform

from datetime import datetime, timedelta

from binance.client import Client
from binance.exceptions import BinanceAPIException

from apikeys import api_key, api_secret



def beep(n):
    for _ in range(n):
        if platform.system() == 'Windows':
            import winsound
            winsound.Beep(440, 500)  # frecvența de 440 Hz, durata de 500 ms
        else:
            # Aici putem folosi o comanda de beep - nu  merge pt orice android
            os.system('echo "\007"')
        time.sleep(3)

client = Client(api_key, api_secret)

symbol = 'BTCUSDT'

# Bugetul inițial
budget = 1000  # USDT
order_cost_btc = 0.00004405  # BTC
max_threshold = 0.015
price_change_threshold = 0.007  # Pragul de schimbare a prețului, 0.7%
interval_time = 2 * 3600 # 2 h * 3600 seconds.
interval_time = 97 * 79


def get_interval_time(valoare_prestabilita=interval_time, marja_aleatoare=10):
    # Generarea unei valori aleatoare în intervalul [-marja_aleatoare, marja_aleatoare]
    valoare_aleatoare = random.uniform(-marja_aleatoare, marja_aleatoare)
    interval = abs(valoare_prestabilita + valoare_aleatoare)
    
    return interval
    
def get_quantity_precision(symbol):
    try:
        info = client.get_symbol_info(symbol)
        for filter in info['filters']:
            if filter['filterType'] == 'LOT_SIZE':
                step_size = filter['stepSize']
                precision = -int(round(-math.log10(float(step_size)), 0))
                return precision
    except BinanceAPIException as e:
        print(f"Eroare la obținerea preciziei cantității: {e}")
    return 8  # Valoare implicită

precision = get_quantity_precision(symbol)
precision = 8
print(f"Precision is {precision}")

