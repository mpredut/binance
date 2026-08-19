####Binance
import time
import threading

from binance.client import Client
from binance.exceptions import BinanceAPIException

####MYLIB
_client = None

# Marjă de siguranță: ținem timestamp-ul nostru puțin SUB timpul serverului ca să nu
# declanșăm -1021 (timestamp ahead) nici la jitter; rămâne mult sub recvWindow (5s).
TIME_SAFETY_MARGIN_MS = 1000
TIME_RESYNC_INTERVAL_SEC = 5 * 60

# Rezilienta la blip-uri DNS/retea (11 aug): un apel REST FARA timeout poate ATARNA la infinit
# cand DNS-ul pica un moment -> bucla de update din cacheManager se blocheaza -> cache stale ->
# watchdog reporneste cacheManager (vazut 8 aug 6am). Timeout scurt = esec RAPID (exceptie prinsa
# de apelanti, bucla continua). REQUEST_TIMEOUT_SEC pe TOATE apelurile.
REQUEST_TIMEOUT_SEC = 10

_resync_started = False


def _install_retry(cl):
    """Retry central pe blip-uri tranzitorii (DNS/conn/5xx/429) — DOAR pe metode idempotente
    (GET/HEAD/...), NICIODATA pe POST (plasare ordine): daca un POST reuseste dar raspunsul se
    pierde, un retry ar DUBLA ordinul (bani reali). allowed_methods NEsetat = default urllib3 =
    doar idempotente, exclude POST. Impreuna cu timeout-ul: blip -> esec rapid + reincercare
    interna pe GET-uri, nu hang."""
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry = Retry(total=3, connect=3, read=3, backoff_factor=1.0,
                      status_forcelist=[429, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        cl.session.mount("https://", adapter)
        cl.session.mount("http://", adapter)
    except Exception as e:  # noqa: BLE001 — daca esueaza, ramanem doar pe timeout (tot ajuta)
        print(f"[bapi_client] _install_retry esuat (ignor, ramane timeout): {e}")


def sync_time(safety_margin_ms=TIME_SAFETY_MARGIN_MS):
    """Sincronizează `client.timestamp_offset` cu timpul serverului Binance.
    Corectează clock-skew-ul local (tipic în WSL) care cauzează
    APIError(-1021): 'Timestamp for this request was 1000ms ahead of server's time'.
    Endpoint public (neparafat) → nu depinde el însuși de timestamp."""
    if _client is None:
        return None
    try:
        server_ms = _client.get_server_time()["serverTime"]
        local_ms = int(time.time() * 1000)
        _client.timestamp_offset = server_ms - local_ms - safety_margin_ms
        return _client.timestamp_offset
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
    global _client
    if _client is None:
        from keys.apikeys import api_key, api_secret
        _client = Client(api_key, api_secret, requests_params={"timeout": REQUEST_TIMEOUT_SEC})
        _install_retry(_client)      # retry pe GET-uri (NU pe POST/ordere) la blip tranzitoriu
        sync_time()                 # aliniere inițială la timpul serverului
        _start_periodic_resync()    # menținere în timp
    return _client


class _LazyClientProxy:
    """Păstrează API-ul `client.*`, dar construiește clientul la primul apel real."""
    def __getattr__(self, name):
        return getattr(getClient(), name)

    def __setattr__(self, name, value):
        setattr(getClient(), name, value)


client = _LazyClientProxy()
