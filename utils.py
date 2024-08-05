import os
import time
import math
import random

from datetime import datetime, timedelta

from binance.client import Client
from binance.exceptions import BinanceAPIException

from apikeys import api_key, api_secret

def beep(n):
    for _ in range(n):
        os.system('tput bel')
        time.sleep(3)  # Pauză de 3 secunde între bipe

client = Client(api_key, api_secret)

# Simbolul pentru perechea de tranzacționare
symbol = 'BTCUSDT'

# Bugetul inițial
budget = 1000  # USDT
order_cost_btc = 0.00004405  # BTC
price_change_threshold = 0.007  # Pragul de schimbare a prețului, 0.7%
#price_change_threshold = 0.07  # Pragul de schimbare a prețului, 7%
interval_time = 2 * 3600 # 2 h * 3600 seconds.
interval_time = 1 * 79

def get_interval_time(valoare_prestabilita=interval_time, marja_aleatoare=100):
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

