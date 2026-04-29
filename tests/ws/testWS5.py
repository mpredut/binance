import asyncio
import websockets
import json
from keys.apikeys import api_key, api_secret
import time
import hmac
import hashlib

def sign(payload: str) -> str:
    return hmac.new(
        api_secret.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

async def test_ws():
    url = "wss://ws-api.binance.com:443/ws-api/v3"
    
    async with websockets.connect(url, ping_interval=20) as ws:
        print("✅ Conectat!")

        # Varianta 1 — signed session login mai întâi
        timestamp = int(time.time() * 1000)
        params = f"apiKey={api_key}&timestamp={timestamp}"
        signature = sign(params)

        login = {
            "id": "login",
            "method": "session.logon",
            "params": {
                "apiKey": api_key,
                "timestamp": timestamp,
                "signature": signature
            }
        }
        await ws.send(json.dumps(login))
        resp = await ws.recv()
        print(f"Login response: {resp}")

        # Varianta 2 — subscribe după login
        subscribe = {
            "id": "sub",
            "method": "userDataStream.subscribe"
        }
        await ws.send(json.dumps(subscribe))
        print("Subscribe trimis, plasează un order!")

        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
                print(f"EVENT: {json.dumps(json.loads(msg), indent=2)}")
            except asyncio.TimeoutError:
                print("... waiting ...")

asyncio.run(test_ws())
