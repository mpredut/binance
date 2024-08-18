import pandas as pd
import requests
import datetime

def fetch_data():
    # API pentru datele istorice (Exemplu: Binance API)
    end_time = int(datetime.datetime.now().timestamp() * 1000)
    start_time = end_time - 30 * 24 * 60 * 60 * 1000  # Ultimele 30 de zile

    # Ajustăm pentru a obține date în mai multe cereri, deoarece Binance limitează numărul de date returnate
    df_list = []
    while start_time < end_time:
        url = f'https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m&startTime={start_time}&endTime={end_time}&limit=1000'
        response = requests.get(url)
        data = response.json()
        
        if not data:
            break
        
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'number_of_trades', 'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df['close'] = df['close'].astype(float)
        
        df_list.append(df)
        
        start_time = int(df.index[-1].timestamp() * 1000) + 60000  # Trece la următorul minut după ultimul timestamp
        
    full_df = pd.concat(df_list)
    return full_df

import matplotlib.pyplot as plt

def analyze_drops_with_distribution(df):
    drops = []
    total_intervals = len(df) - 2*60  # Intervale de 20 de minute

    for i in range(total_intervals):
        start_price = df['close'].iloc[i]
        end_price = df['close'].iloc[i + 2*60]
        drop_percent = (start_price - end_price) / start_price * 100
        drops.append(drop_percent)

    # Verificăm distribuția scăderilor
    plt.hist(drops, bins=50, edgecolor='black')
    plt.title('Distribuția scăderilor procentuale în 2*60 de minute')
    plt.xlabel('Procent de scădere')
    plt.ylabel('Frecvență')
    plt.show()

    count_drops_5 = sum(1 for drop in drops if drop >= 5)
    count_drops_3 = sum(1 for drop in drops if drop >= 3)
    count_drops_2 = sum(1 for drop in drops if drop >= 2)

    probability_5 = count_drops_5 / total_intervals * 100
    probability_3 = count_drops_3 / total_intervals * 100
    probability_2 = count_drops_2 / total_intervals * 100

    return count_drops_5, probability_5, count_drops_3, probability_3, count_drops_2, probability_2

# Fetch data and analyze
df = fetch_data()
# Analyze with distribution
count_drops_5, probability_5, count_drops_3, probability_3, count_drops_2, probability_2 = analyze_drops_with_distribution(df)

print(f"Număr de scăderi de 5% în 20 de minute: {count_drops_5}")
print(f"Probabilitatea unei scăderi de 5% în 20 de minute: {probability_5:.2f}%")
print(f"Număr de scăderi de 3% în 20 de minute: {count_drops_3}")
print(f"Probabilitatea unei scăderi de 3% în 20 de minute: {probability_3:.2f}%")
print(f"Număr de scăderi de 2% în 20 de minute: {count_drops_2}")
print(f"Probabilitatea unei scăderi de 2% în 20 de minute: {probability_2:.2f}%")


