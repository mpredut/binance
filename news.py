import time
import requests
import pandas as pd

# Înlocuiește cu cheia ta API de la CoinMarketCap
API_KEY = "4d587781-722b-40a3-83f0-2436d45942f7"
url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

# Parametrii cererii API
params_test = {
    'start': '1',
    'limit': '2001',  # Poți ajusta numărul de monede returnate
    'convert': 'USD'
}

headers = {
    'Accepts': 'application/json',
    'X-CMC_PRO_API_KEY': API_KEY,
}


# Înlocuiește cu cheia ta API de la CoinMarketCap
API_KEY = "cheia_ta_api_coinmarketcap"
url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"

# Inițializăm variabile pentru a stoca toate monedele
coins = []
start = 1  # Pornim de la prima monedă
limit = 1000  # Maximul pe care îl putem cere per pagină

while True:
    # Parametrii cererii API pentru a obține monedele în mod paginat
    params = {
        'start': str(start),
        'limit': str(limit),
        'convert': 'USD'
    }
    time.sleep(5)
    # Trimiterea cererii la API
    response = requests.get(url, headers=headers, params=params)
    data = response.json()
    if data is None:
        break
    if 'data' not in data:
        print("Nu există 'data' în răspunsul API. Iată răspunsul complet:")
        print(data)
        break
    # Verificăm dacă mai sunt date de procesat
    if not data['data']:
        break
        
    for coin in data['data']:
        name = coin['name']
        symbol = coin['symbol']
        launch_date = pd.to_datetime(coin['date_added'])
        change_24h = coin['quote']['USD']['percent_change_24h']
        change_7d = coin['quote']['USD']['percent_change_7d']
        coins.append({
            "name": name,
            "symbol": symbol,
            "launch_date": launch_date,
            "change_24h": change_24h,
            "change_7d": change_7d
        })

    # Creștem start-ul pentru următoarea pagină
    start += limit

print(f"Am extras {len(coins)} monezi");

# Convertim lista într-un DataFrame pandas
df = pd.DataFrame(coins)

# Sortăm monedele după data lansării (cele mai noi primele)
nb = 100
df_sorted_new = df.sort_values(by="launch_date", ascending=False).head(nb)

print("Cea mai nouă monedă lansată pe CoinMarketCap:")
print(df_sorted_new.iloc[0])  # Accesăm primul rând cu iloc

# Sortăm monedele după creștere/scădere în ultimele 7 zile și în ultimele 24 de ore
df_sorted_greatest_increase_7d = df.sort_values(by="change_7d", ascending=False).head(10)
df_sorted_greatest_decrease_7d = df.sort_values(by="change_7d", ascending=True).head(10)
df_sorted_greatest_increase_24h = df.sort_values(by="change_24h", ascending=False).head(10)
df_sorted_greatest_decrease_24h = df.sort_values(by="change_24h", ascending=True).head(10)

# Afișăm rezultatele
print(f"Primele {nb} monede noi:")
pd.set_option('display.max_rows', 100)  # Afișăm până la 100 de rânduri
print(df_sorted_new)

print("\nTop 10 creșteri pe 7 zile:")
print(df_sorted_greatest_increase_7d)

print("\nTop 10 scăderi pe 7 zile:")
print(df_sorted_greatest_decrease_7d)

print("\nTop 10 creșteri pe 24 de ore:")
print(df_sorted_greatest_increase_24h)

print("\nTop 10 scăderi pe 24 de ore:")
print(df_sorted_greatest_decrease_24h)

