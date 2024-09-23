import time
import requests
import pandas as pd

# Înlocuiește cu cheia ta API de la CoinMarketCap
API_KEY_CMC = "4d587781-722b-40a3-83f0-2436d45942f7"
url_cmc = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"


# Setări pentru cererea API către CoinMarketCap
headers_cmc = {
    'Accepts': 'application/json',
    'X-CMC_PRO_API_KEY': API_KEY_CMC,
}

# Funcție pentru obținerea datelor de la CoinMarketCap
def get_coinmarketcap_coins():
    coins = []
    start = 1  # Pornim de la prima monedă
    limit = 1000  # Maximul pe care îl putem cere per pagină

    while True:
        params_cmc = {
            'start': str(start),
            'limit': str(limit),
            'convert': 'USD'
        }
        time.sleep(5)
        response = requests.get(url_cmc, headers=headers_cmc, params=params_cmc)
        data = response.json()
        if data is None or 'data' not in data:
            break

        # Procesăm datele primite de la CoinMarketCap
        for coin in data['data']:
            name = coin['name']
            symbol = coin['symbol']
            launch_date = pd.to_datetime(coin['date_added'])
            price = coin['quote']['USD']['price']
            website_slug = coin['slug']  # Aceasta poate fi utilizată pentru identificare suplimentară
            change_24h = coin['quote']['USD']['percent_change_24h']
            change_7d = coin['quote']['USD']['percent_change_7d']
            coins.append({
                "name": name,
                "symbol": symbol,
                "launch_date": launch_date,
                "price": price,
                "website_slug": website_slug,
                "change_24h": change_24h,
                "change_7d": change_7d
            })

        if not data['data']:  # Dacă nu mai sunt date
            break
        start += limit  # Creștem pentru a aduce următoarea pagină

    print(f"Am extras {len(coins)} monezi")
    return pd.DataFrame(coins)

# Funcție pentru obținerea monedelor disponibile pe Binance cu prețuri
def get_binance_coins():
    url_binance = "https://api.binance.com/api/v3/ticker/price"
    response = requests.get(url_binance)
    data = response.json()
    
    # Extragem simbolurile monedelor de pe Binance și prețurile lor
    binance_data = {}
    for coin in data:
        symbol = coin['symbol'].replace('USDT', '')  # Simbolul fără USDT
        price = float(coin['price'])
        binance_data[symbol] = price
    return binance_data

# Funcție principală pentru a găsi monedele disponibile pe ambele platforme și a le sorta
def find_common_coins_and_sort(topn, df_cmc, binance_data):
    # Filtrăm monedele care sunt prezente atât pe CoinMarketCap cât și pe Binance
    common_coins = []
    
    for index, row in df_cmc.iterrows():
        symbol = row['symbol']
        price_cmc = row['price']
        
        if symbol in binance_data:
            price_binance = binance_data[symbol]
            # Compara prețul pentru a verifica similitudinea
            if abs(price_cmc - price_binance) / price_cmc < 0.05:  # Toleranță de 5%
                common_coins.append({
                    "name": row['name'],
                    "symbol": symbol,
                    "launch_date": row['launch_date'],
                    "price_cmc": price_cmc,
                    "price_binance": price_binance,
                    "change_24h": row['change_24h'],
                    "change_7d": row['change_7d'],
                    "website_slug": row['website_slug']
                })

    df_common = pd.DataFrame(common_coins)

    # Sortăm monedele după data lansării (cele mai noi primele)
    df_sorted_new = df_common.sort_values(by="launch_date", ascending=False).head(topn)
    
    # Sortăm monedele după creștere/scădere în ultimele 7 zile și în ultimele 24 de ore
    df_sorted_greatest_increase_7d = df_common.sort_values(by="change_7d", ascending=False).head(topn)
    df_sorted_greatest_decrease_7d = df_common.sort_values(by="change_7d", ascending=True).head(topn)
    df_sorted_greatest_increase_24h = df_common.sort_values(by="change_24h", ascending=False).head(topn)
    df_sorted_greatest_decrease_24h = df_common.sort_values(by="change_24h", ascending=True).head(topn)
    
    return df_sorted_new, df_sorted_greatest_increase_7d, df_sorted_greatest_decrease_7d, df_sorted_greatest_increase_24h, df_sorted_greatest_decrease_24h




# Convertim lista într-un DataFrame pandas
df_cmc = get_coinmarketcap_coins()
binance_coins = get_binance_coins()
    

# Sortăm monedele după data lansării (cele mai noi primele)
nb = 100
df_sorted_new = df_cmc.sort_values(by="launch_date", ascending=False).head(nb)

print("Cea mai nouă monedă lansată pe CoinMarketCap:")
print(df_sorted_new.iloc[0])  # Accesăm primul rând cu iloc

# Sortăm monedele după creștere/scădere în ultimele 7 zile și în ultimele 24 de ore
df_sorted_greatest_increase_7d = df_cmc.sort_values(by="change_7d", ascending=False).head(10)
df_sorted_greatest_decrease_7d = df_cmc.sort_values(by="change_7d", ascending=True).head(10)
df_sorted_greatest_increase_24h = df_cmc.sort_values(by="change_24h", ascending=False).head(10)
df_sorted_greatest_decrease_24h = df_cmc.sort_values(by="change_24h", ascending=True).head(10)

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

###########
# Apelăm funcția și afișăm rezultatele
df_top_10_new, df_top_10_increase_7d, df_top_10_decrease_7d, df_top_10_increase_24h, df_top_10_decrease_24h = find_common_coins_and_sort(10, df_cmc, binance_coins)

print("Primele 10 monede disponibile și pe CoinMarketCap, și pe Binance, sortate după noutate:")
print(df_top_10_new)

print("\nTop 10 creșteri pe 7 zile:")
print(df_top_10_increase_7d)

print("\nTop 10 scăderi pe 7 zile:")
print(df_top_10_decrease_7d)

print("\nTop 10 creșteri pe 24 de ore:")
print(df_top_10_increase_24h)

print("\nTop 10 scăderi pe 24 de ore:")
print(df_top_10_decrease_24h)