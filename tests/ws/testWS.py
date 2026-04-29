import binanceapi as api
import websockets, asyncio, json

async def test_ws():
    key = api.client.stream_get_listen_key()   # <-- fix
    print(f"Listen key: {key}")
    url = f"wss://stream.binance.com:9443/ws/{key}"
    async with websockets.connect(url) as ws:
        print("Conectat! Plasează un order din app acum...")
        for _ in range(60):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=15)
                print(f"EVENT: {json.loads(msg)}")
            except asyncio.TimeoutError:
                print("... waiting for events ...")

asyncio.run(test_ws())
