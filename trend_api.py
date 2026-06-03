"""
trend_api — punct neutru de acces la trendul curent (instant + general).

TrendCoordinator (tradeall.py) publică aici, la fiecare evaluare, snapshot-ul
de trend per simbol. Orice modul (ex. bapi_placeorder) poate interoga rapid
(O(1)) trendul fără să depindă de tradeall → fără import circular.

Folosire tipică (întârziere oportunistă a plasării ordinului):
    import trend_api
    trend_api.wait_for_favorable_entry("BUY", "BTCUSDT", max_wait_sec=60)
    # ... abia apoi plasează ordinul, la un preț potențial mai bun.
"""
import time
import threading

# Snapshot-ul publicat de TrendCoordinator are cel puțin câmpurile:
#   final_trend, growth_coefficient, slope_full, gradient_recent,
#   slope_small, slope_big, slope_max_min, pos, current_price, ts
_trend_cache = {}
_lock = threading.Lock()

# Dacă snapshot-ul e mai vechi decât atât, nu mai întârziem (date stale).
TREND_STALE_SEC = 15.0


# ── Publicare / citire ───────────────────────────────────────────────────────

def publish_trend(symbol: str, snapshot: dict) -> None:
    """Snapshot COMPLET, după o evaluare completă (throttled). Suprascrie tot."""
    with _lock:
        _trend_cache[symbol] = dict(snapshot)


def update_instant(symbol: str, **fields) -> None:
    """Update RAPID (per tick WS) — doar câmpurile fierbinți (gradient_recent,
    final_trend, current_price, ts). Merge peste snapshot-ul existent ca să
    păstreze câmpurile bogate de la ultima evaluare completă.

    Ăsta e canalul de latență mică pentru gate-ul de buy/sell."""
    with _lock:
        snap = dict(_trend_cache.get(symbol) or {})
        snap.update(fields)
        snap["symbol"] = symbol
        _trend_cache[symbol] = snap


def get_trend_snapshot(symbol: str):
    """O(1). Returnează ultimul snapshot de trend sau None."""
    with _lock:
        return _trend_cache.get(symbol)


def get_all_trends() -> dict:
    with _lock:
        return dict(_trend_cache)


def clear() -> None:
    with _lock:
        _trend_cache.clear()


# ── Decizie de întârziere (oportunistă: aștept preț mai bun) ──────────────────

def is_favorable_to_wait(side: str, symbol: str, now: float = None) -> bool:
    """True dacă merită să mai AȘTEPTĂM (trendul ne aduce un preț mai bun).

    BUY : prețul încă scade  (gradient_recent < 0) → așteptăm să cumpărăm mai ieftin.
    SELL: prețul încă urcă    (gradient_recent > 0) → așteptăm să vindem mai scump.

    Returnează False dacă nu există snapshot, e stale, sau trendul nu mai e favorabil.
    """
    snap = get_trend_snapshot(symbol)
    if snap is None:
        return False
    now = now if now is not None else time.time()
    if now - snap.get("ts", 0) > TREND_STALE_SEC:
        return False

    g = snap.get("gradient_recent", 0.0)
    side = side.upper()
    if side == "BUY":
        return g < 0
    if side == "SELL":
        return g > 0
    return False


def wait_for_favorable_entry(side: str, symbol: str,
                             max_wait_sec: float = 60.0,
                             poll_sec: float = 0.2,
                             sleep_fn=time.sleep) -> float:
    """Blochează cât timp trendul e favorabil (preț încă în direcția dorită),
    până la `max_wait_sec`. Returnează numărul de secunde așteptate.

    Plasarea ordinului se face DUPĂ acest apel, la prețul (potențial mai bun)
    de atunci. `sleep_fn` e injectabil pentru testare.
    """
    deadline = time.time() + max_wait_sec
    waited = 0.0
    next_dot = 1.0
    while time.time() < deadline and is_favorable_to_wait(side, symbol):
        sleep_fn(poll_sec)
        waited += poll_sec
        if waited >= next_dot:            # heartbeat vizual ~1/secundă
            print(".", end="", flush=True)
            next_dot += 1.0
    if waited > 0:
        print()                           # newline după șirul de puncte
    return waited
