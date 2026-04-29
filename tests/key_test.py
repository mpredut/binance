
from binance.client import Client
from keys.apikeys import api_key, api_secret

# key_test.py
# Test direct cu requests
import requests

response = requests.post(
    'https://api.binance.com/api/v3/userDataStream',
    headers={'X-MBX-APIKEY': api_key}
)
print(response.json())

exit(0)

from binance.client import Client
from keys.apikeys import api_key, api_secret

client = Client(api_key, api_secret)

# Test 1 - nu necesita autentificare
print(client.get_server_time())

# Test 2 - necesita doar READ permission
print(client.get_account())

# Test 3 - abia apoi stream
#print(client.stream_get_listen_key())
# Incearca astea in loc de stream_get_listen_key()
print(client.stream_get_listen_key())  # spot - pica

# Alternative:
#print(client.futures_stream_get_listen_key())  # futures
#print(client.isolated_margin_stream_get_listen_key(symbol='BTCUSDT'))  # isolated margin

