import asyncio
import asyncio
import requests
import websockets
import json
import binanceapi as api
from binance import AsyncClient, BinanceSocketManager
from apikeys import api_key, api_secret

def get_listen_key(api_key):
    resp = requests.post(
        "https://api3.binance.com/api/v3/userDataStream",
        headers={"X-MBX-APIKEY": api_key}
    )
    resp.raise_for_status()
    return resp.json()["listenKey"]


async def test_ws():
    # AsyncClient separat — nu reutiliza clientul sync
    #async_client = await AsyncClient.create(
    #    api_key=api.client.API_KEY,
    #    api_secret=api.client.API_SECRET,
    #)

    get_listen_key(api_key)

    async_client = await AsyncClient.create(
        api_key=api_key,
        api_secret=api_secret,
        requests_params={"timeout": 20},
        tld="com",
    )
    
    bm = BinanceSocketManager(async_client)
    
    print("Conectare la User Data Stream...")
    async with bm.user_socket() as stream:
        print("✅ Conectat! Plasează un order din app acum...")
        while True:
            event = await stream.recv()
            print(f"EVENT: {event}")

asyncio.run(test_ws())
