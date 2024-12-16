

####Binance
from binance.client import Client
from binance.exceptions import BinanceAPIException

####MYLIB
from apikeys import api_key, api_secret
client = Client(api_key, api_secret)