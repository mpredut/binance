import time
from binance.client import Client
from keys.apikeys import api_key, api_secret

client = Client(api_key, api_secret)

def get_btc_price():
    return client.get_symbol_ticker(symbol="BTCUSDT")['price']

def get_account_balance():
    account = client.get_account()
    balances = {b['asset']: b['free'] for b in account['balances'] if float(b['free']) > 0}
    return balances

def get_open_orders():
    return client.get_open_orders(symbol="BTCUSDT")

while True:
    print("BTC:", get_btc_price())
    print("Balances:", get_account_balance())
    print("Orders:", get_open_orders())
    time.sleep(1)
