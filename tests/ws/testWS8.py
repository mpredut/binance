import nacl.signing
import base64, time, json, asyncio, websockets
from apikeys import api_key_ws


# Extragere corectă din PKCS8 DER
with open("ed25519_private.pem", "r") as f:
    pem_data = f.read()

import re
b64 = re.search(r"-----BEGIN PRIVATE KEY-----(.+?)-----END PRIVATE KEY-----", pem_data, re.DOTALL)
der_bytes = base64.b64decode(b64.group(1).strip())

# PKCS8 Ed25519 = 48 bytes total, seed-ul e ultimii 32
# dar uneori e la offset 16, verifică ambele
print(f"DER length: {len(der_bytes)}")
seed = der_bytes[-32:]  
signing_key = nacl.signing.SigningKey(seed)

# Verificare — public key trebuie să coincidă cu ce ai în Binance
verify_key = signing_key.verify_key
pub_b64 = base64.b64encode(bytes(verify_key)).decode()
print(f"Public key raw base64: {pub_b64}")

def sign_ed25519(payload: str) -> str:
    signed = signing_key.sign(payload.encode())
    return base64.b64encode(signed.signature).decode()

async def test_ws():
    url = "wss://ws-api.binance.com:443/ws-api/v3"
    async with websockets.connect(url, ping_interval=20) as ws:
        print("✅ Conectat!")
        timestamp = int(time.time() * 1000)
        params_str = f"apiKey={api_key_ws}&timestamp={timestamp}"
        print(f"Signing: {params_str}")
        signature = sign_ed25519(params_str)
        print(f"Signature: {signature[:20]}...")

        login = {
            "id": "login",
            "method": "session.logon",
            "params": {
                "apiKey": api_key_ws,
                "timestamp": timestamp,
                "signature": signature
            }
        }
        await ws.send(json.dumps(login))
        resp = json.loads(await ws.recv())
        print(f"Login: {json.dumps(resp, indent=2)}")

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
