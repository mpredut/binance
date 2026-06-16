####Binance
import time
import threading

from binance.client import Client
from binance.exceptions import BinanceAPIException

####MYLIB
client = None

# Marjă de siguranță: ținem timestamp-ul nostru puțin SUB timpul serverului ca să nu
# declanșăm -1021 (timestamp ahead) nici la jitter; rămâne mult sub recvWindow (5s).
TIME_SAFETY_MARGIN_MS = 1000
TIME_RESYNC_INTERVAL_SEC = 5 * 60

_resync_started = False


def sync_time(safety_margin_ms=TIME_SAFETY_MARGIN_MS):
    """Sincronizează `client.timestamp_offset` cu timpul serverului Binance.
    Corectează clock-skew-ul local (tipic în WSL) care cauzează
    APIError(-1021): 'Timestamp for this request was 1000ms ahead of server's time'.
    Endpoint public (neparafat) → nu depinde el însuși de timestamp."""
    if client is None:
        return None
    try:
        server_ms = client.get_server_time()["serverTime"]
        local_ms = int(time.time() * 1000)
        client.timestamp_offset = server_ms - local_ms - safety_margin_ms
        return client.timestamp_offset
    except Exception as e:
        print(f"[bapi_client] sync_time failed: {e}")
        return None


def _start_periodic_resync():
    """Thread daemon care re-sincronizează periodic (ceasul WSL driftează în timp)."""
    global _resync_started
    if _resync_started:
        return
    _resync_started = True

    def loop():
        while True:
            time.sleep(TIME_RESYNC_INTERVAL_SEC)
            sync_time()

    threading.Thread(target=loop, name="BinanceTimeResync", daemon=True).start()


def getClient():
    global client
    if client is None:
        from keys.apikeys import api_key, api_secret
        client = Client(api_key, api_secret)
        sync_time()                 # aliniere inițială la timpul serverului
        _start_periodic_resync()    # menținere în timp
    return client


getClient()
