"""
trend_api — cache de trend partajat ÎNTRE PROCESE (instant + general).

Arhitectură multi-proces:
  - 1 WRITER : tradeall.py (TrendCoordinator) publică snapshot-urile.
  - N READERI: rtrade.py, monitortrades.py, assetguardian.py, bapi_placeorder...
    citesc trendul pentru gate-ul de buy/sell.

Partajarea se face printr-un fișier JSON mic (cache_instant_trend.json), la fel
ca pattern-ul din cacheManager. Single-writer / multi-reader → fără conflicte.
Pentru un fișier mic, citit la cerere, fișierul e suficient de rapid (sub-ms);
redis/socket ar adăuga infrastructură fără câștig real la scara asta.

Folosire (gate oportunist — așteptăm preț mai bun cât timp trendul e favorabil):
    import trend_api
    trend_api.wait_for_favorable_entry("BUY", "BTCUSDT", max_wait_sec=3600)
"""
import os
import json
import time
import threading

TREND_FILE = "cache_instant_trend.json"

# Sub acest prag (relativ la preț) gradientul e considerat ZGOMOT → așteptăm
# până avem vizibilitate clară de trend.
FAVORABLE_REL_EPS = 1e-5   # 0.001% din preț per sample
FAVORABLE_ABS_EPS = 0.0    # floor absolut

# Snapshot mai vechi de atât → nu mai întârziem (date stale).
TREND_STALE_SEC = 15.0


# ════════════════════════════════════════════════════════════════════════════
# InstantTrendCache — store file-backed, partajat între procese.
# ════════════════════════════════════════════════════════════════════════════
class InstantTrendCache:
    def __init__(self, filename=TREND_FILE):
        self.filename = filename
        self._mem = {}
        self._lock = threading.Lock()
        self._file_mtime = None
        self._file_cache = None

    # ── scriere (writer) ─────────────────────────────────────────────────────

    def _write_file(self):
        try:
            tmp = self.filename + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._mem, f)
            os.replace(tmp, self.filename)   # atomic
        except Exception as e:
            print(f"[trend_api] eroare scriere {self.filename}: {e}")

    def publish(self, symbol, snapshot):
        """Snapshot COMPLET (replace) — de la o evaluare completă (throttled)."""
        with self._lock:
            self._mem[symbol] = dict(snapshot)
            self._write_file()

    def update_instant(self, symbol, **fields):
        """Update RAPID (merge) — câmpurile fierbinți la fiecare tick WS.
        Păstrează câmpurile bogate de la ultimul publish."""
        with self._lock:
            base = dict(self._mem.get(symbol) or self._read_file().get(symbol) or {})
            base.update(fields)
            base["symbol"] = symbol
            self._mem[symbol] = base
            self._write_file()

    # ── citire (reader, cross-process) ───────────────────────────────────────

    def _read_file(self):
        """Citește fișierul partajat, cu cache pe mtime_ns (rezoluție nanosecundă,
        ca să nu rateze scrieri din aceeași secundă)."""
        try:
            mtime = os.stat(self.filename).st_mtime_ns
        except OSError:
            return {}
        if mtime == self._file_mtime and self._file_cache is not None:
            return self._file_cache
        try:
            with open(self.filename, "r") as f:
                data = json.load(f)
        except Exception:
            return self._file_cache or {}
        self._file_mtime = mtime
        self._file_cache = data
        return data

    def get(self, symbol):
        """Writer: _mem e autoritar (mereu proaspăt). Reader (alt proces, _mem gol):
        citește din fișierul partajat."""
        with self._lock:
            if symbol in self._mem:
                return dict(self._mem[symbol])
        return self._read_file().get(symbol)

    def get_all(self):
        with self._lock:
            if self._mem:
                return {s: dict(v) for s, v in self._mem.items()}
        return dict(self._read_file())

    def clear(self):
        with self._lock:
            self._mem.clear()
            self._file_mtime = None
            self._file_cache = None
            try:
                if os.path.exists(self.filename):
                    os.remove(self.filename)
            except Exception:
                pass


# ── Singleton + API la nivel de modul ────────────────────────────────────────

_cache = InstantTrendCache()


def set_trend_file(filename):
    """Schimbă fișierul partajat (ex. în teste)."""
    global _cache
    _cache = InstantTrendCache(filename)


def publish_trend(symbol, snapshot):
    _cache.publish(symbol, snapshot)


def update_instant(symbol, **fields):
    _cache.update_instant(symbol, **fields)


def get_trend_snapshot(symbol):
    return _cache.get(symbol)


def get_all_trends():
    return _cache.get_all()


def clear():
    _cache.clear()


# ── Decizie de întârziere (oportunistă: aștept preț mai bun) ──────────────────

def _epsilon(snapshot):
    # Preferă epsilon-ul INFORMAT din volatilitate (publicat de writer), dacă există;
    # altfel fallback pe pragul relativ la preț.
    eps = snapshot.get("epsilon")
    if eps is not None and eps > 0:
        return float(eps)
    price = abs(snapshot.get("current_price") or 0.0)
    return max(FAVORABLE_ABS_EPS, price * FAVORABLE_REL_EPS)


def is_favorable_to_wait(side, symbol, now=None):
    """True dacă merită să mai AȘTEPTĂM.

    Zgomot (|gradient_recent| <= epsilon): True → așteptăm vizibilitate clară.
    Trend clar:
        BUY : favorabil cât timp prețul SCADE (g < -eps) → cumpărăm mai ieftin;
              dacă prețul URCĂ clar (g > eps) → plasăm acum (înainte să fie mai scump).
        SELL: invers.
    """
    snap = get_trend_snapshot(symbol)
    if snap is None:
        return False
    now = now if now is not None else time.time()
    if now - snap.get("ts", 0) > TREND_STALE_SEC:
        return False

    g = snap.get("gradient_recent", 0.0)
    eps = _epsilon(snap)
    if abs(g) <= eps:
        return True   # zgomot → așteptăm până se clarifică trendul

    side = side.upper()
    if side == "BUY":
        return g < 0
    if side == "SELL":
        return g > 0
    return False


def wait_for_favorable_entry(side, symbol, max_wait_sec=3600.0,
                             poll_sec=0.2, sleep_fn=time.sleep):
    """Blochează cât timp trendul e favorabil (preț încă în direcția dorită),
    până la max_wait_sec. Heartbeat vizual (.) la ~1s. Returnează secundele așteptate."""
    deadline = time.time() + max_wait_sec
    waited = 0.0
    next_dot = 1.0
    while time.time() < deadline and is_favorable_to_wait(side, symbol):
        sleep_fn(poll_sec)
        waited += poll_sec
        if waited >= next_dot:
            print(".", end="", flush=True)
            next_dot += 1.0
    if waited > 0:
        print()
    return waited
