from binance.client import Client
from binance.websockets import BinanceSocketManager
from apikeys import api_key, api_secret

# Initialize the client
client = Client(api_key, api_secret, tld='us')

# Function to process incoming messages
def process_message(msg):
    if msg['e'] == 'error':
        print(f"Error: {msg['m']}")
    else:
        print(f"Symbol: {msg['s']} Price: {msg['c']}")

# Initialize the BinanceSocketManager
bm = BinanceSocketManager(client)

# Start a socket to get updates for a specific symbol (e.g., BTCUSDT)
conn_key = bm.start_symbol_ticker_socket('BTCUSDT', process_message)

# Start the socket manager
bm.start()

# Keep the script running to maintain the WebSocket connection
import time
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    bm.stop_socket(conn_key)
    bm.close()

