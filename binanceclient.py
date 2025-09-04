

####Binance
from binance.client import Client
from binance.exceptions import BinanceAPIException

####MYLIB
client = None

def getClient():
    global client
    if client is None:
        from apikeys import api_key, api_secret
        client = Client(api_key, api_secret)
    return client
   
client = getClient()
