import nacl.signing
import base64, time, json, asyncio, websockets
from keys.apikeys import api_key

with open("ed25519_private.pem", "r") as f:
    pem_data = f.read()

# Extrage raw bytes din PEM
import re
b64 = re.search(r"-----BEGIN PRIVATE KEY-----(.+?)-----END PRIVATE KEY-----", pem_data, re.DOTALL)
raw = base64.b64decode(b64.group(1).strip())
# ultimii 32 bytes sunt cheia Ed25519 raw
signing_key = nacl.signing.SigningKey(raw[-32:])

def sign_ed25519(payload: str) -> str:
    signed = signing_key.sign(payload.encode())
    return base64.b64encode(signed.signature).decode()

async def test_ws():
    url = "wss://ws-api.binance.com:443/ws-api/v3"
    async with websockets.connect(url, ping_interval=20) as ws:
        print("✅ Conectat!")
        timestamp = int(time.time() * 1000)
        params_str = f"apiKey={api_key}&timestamp={timestamp}"
        signature = sign_ed25519(params_str)

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
        resp = json.loads(await ws.recv())
        print(f"Login response: {json.dumps(resp, indent=2)}")

        if resp.get("status") == 200:
            await ws.send(json.dumps({"id": "sub", "method": "userDataStream.subscribe"}))
            print("Subscribed! Plasează un order...")
            while True:
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    print(f"EVENT: {json.loads(msg)}")
                except asyncio.TimeoutError:
                    print("... waiting ...")

asyncio.run(test_ws())
