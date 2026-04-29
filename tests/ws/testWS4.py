
import asyncio
import websockets
import json
from keys.apikeys import api_key

async def test_ws():
    url = "wss://ws-api.binance.com:443/ws-api/v3"
    
    async with websockets.connect(url, ping_interval=20) as ws:
        print("✅ Conectat!")
        
        request = {
            "id": "user-data-stream",
            "method": "userDataStream.subscribe",
            "params": {
                "apiKey": api_key
            }
        }
        
        await ws.send(json.dumps(request))
        print("Subscribe trimis, plasează un order din app!")
        
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
                data = json.loads(msg)
                print(f"EVENT: {json.dumps(data, indent=2)}")
            except asyncio.TimeoutError:
                print("... waiting ...")

asyncio.run(test_ws())
