import asyncio
import websockets
import json
import time
import hmac
import hashlib
from keys.apikeys import api_key, api_secret

def sign(payload: str) -> str:
    return hmac.new(
        api_secret.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

async def test_ws():
    url = "wss://ws-api.binance.com:443/ws-api/v3"
    
    async with websockets.connect(url, ping_interval=20) as ws:
        print("✅ Conectat la WebSocket API!")
        
        # Autentificare + subscribe user data stream
        timestamp = int(time.time() * 1000)
        params = f"apiKey={api_key}&timestamp={timestamp}"
        signature = sign(params)
        
        request = {
            "id": "user-data-stream",
            "method": "userDataStream.subscribe",
            "params": {
                "apiKey": api_key,
                "timestamp": timestamp,
                "signature": signature
            }
        }
        
        await ws.send(json.dumps(request))
        print("Subscribe trimis, aștept events... Plasează un order din app!")
        
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
                data = json.loads(msg)
                print(f"EVENT: {json.dumps(data, indent=2)}")
            except asyncio.TimeoutError:
                print("... waiting ...")

asyncio.run(test_ws())
