"""
trade_cooldown.py — gate anti rapid-fire pentru ordine.

Împiedică plasarea a două ordine pe ACELAȘI simbol la mai puțin de `cooldown_sec`
(implicit 180s = 3 min), indiferent de combinație (BUY/BUY, SELL/SELL, BUY/SELL).

Sigur cross-PROCES și cross-THREAD: un `fcntl.flock` exclusiv (pe un fd propriu)
serializează verificarea-și-rezervarea, deci chiar dacă mai multe procese/thread-uri
lansează simultan, doar UNUL trece, restul sunt blocate.

Flux la chokepoint (__place_order):
    ok, last = reserve_trade(side, symbol)
    if not ok: return None              # blocat de cooldown
    order = ...plasează...
    if order: update_binance_order_id(symbol, order_id)
    else:     release_trade(symbol)      # eșec → nu blocăm 3 min degeaba
"""
import os
import json
import time
import socket
import threading
import multiprocessing
import contextlib

try:
    import fcntl  # Unix (Linux/WSL) — bot-ul rulează pe Linux
    _HAVE_FCNTL = True
except ImportError:                      # pe Windows nu există → gate dezactivat (no-op)
    _HAVE_FCNTL = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "trade_cooldown.json")
LOCK_FILE = os.path.join(BASE_DIR, "trade_cooldown.lock")
DEFAULT_COOLDOWN_SEC = 180


class _FileLock:
    """Lock inter-PROCES și inter-THREAD: fcntl.flock(LOCK_EX) pe un fd propriu.
    Fiecare apel deschide propriul fd → un al doilea proces/thread blochează până
    la eliberare (flock e exclusiv între file descriptions diferite)."""
    def __init__(self, path=LOCK_FILE):
        self.path = path
        self._fd = None

    def __enter__(self):
        self._fd = open(self.path, "a+")
        if _HAVE_FCNTL:
            fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        try:
            if _HAVE_FCNTL:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            self._fd.close()


def _read():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write(state):
    tmp = f"{STATE_FILE}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)          # atomic


def reserve_trade(side, symbol, cooldown_sec=DEFAULT_COOLDOWN_SEC, client_order_id=None):
    """Verifică-și-rezervă ATOMIC dreptul de a plasa un ordin pe `symbol`.
    Returnează:
        (True,  entry)     → permis (rezervat acum)
        (False, last_entry)→ blocat (a fost un ordin pe `symbol` în ultimele cooldown_sec)
    """
    with _FileLock():
        state = _read()
        last = state.get(symbol)
        now = time.time()
        if last and (now - last.get("timestamp", 0)) < cooldown_sec:
            return False, last
        entry = {
            "timestamp": now,
            "side": side,
            "symbol": symbol,
            "pid": os.getpid(),
            "thread_id": threading.get_ident(),
            "process_name": multiprocessing.current_process().name,
            "hostname": socket.gethostname(),
            "client_order_id": client_order_id,
            "binance_order_id": None,
        }
        state[symbol] = entry
        _write(state)
        return True, entry


def release_trade(symbol):
    """Anulează rezervarea pt `symbol` (ex. ordinul a EȘUAT) → nu mai blocăm cooldown-ul."""
    with _FileLock():
        state = _read()
        if symbol in state:
            del state[symbol]
            _write(state)


def update_binance_order_id(symbol, order_id):
    """Completează orderId-ul Binance după plasarea cu succes."""
    with _FileLock():
        state = _read()
        if symbol in state:
            state[symbol]["binance_order_id"] = order_id
            _write(state)


class _Reservation:
    """Obiectul dat de `trade_slot`. allowed=False → blocat de cooldown.
    Apelează commit() DOAR dacă ordinul a fost plasat → rezervarea rămâne (cooldown
    activ). Fără commit, la ieșirea din `with` rezervarea e anulată (rollback)."""
    def __init__(self, allowed, info, symbol):
        self.allowed = allowed
        self.info = info
        self.symbol = symbol
        self._committed = False

    def commit(self, binance_order_id=None):
        self._committed = True
        if binance_order_id is not None:
            update_binance_order_id(self.symbol, binance_order_id)


@contextlib.contextmanager
def trade_slot(side, symbol, cooldown_sec=DEFAULT_COOLDOWN_SEC, client_order_id=None):
    """RAII / scope-based pentru cooldown (ca un guard C++):
        with trade_slot(side, symbol) as slot:
            if not slot.allowed:          # blocat de cooldown
                return
            order = ...plasează...
            if order:
                slot.commit(order_id)     # succes → rezervarea RĂMÂNE (cooldown activ)
            # altfel (eșec/excepție/uitat) → eliberare AUTOMATĂ la ieșire din `with`

    Lock-ul fcntl NU e ținut peste plasare (doar în reserve/release) → fără risc de
    deadlock; ce persistă e starea cooldown-ului, nu lock-ul."""
    allowed, info = reserve_trade(side, symbol, cooldown_sec, client_order_id)
    res = _Reservation(allowed, info, symbol)
    try:
        yield res
    finally:
        if allowed and not res._committed:
            release_trade(symbol)         # rollback: nimic plasat → nu blocăm cooldown-ul


def get_last_trade_age(symbol):
    """Vârsta (secunde) a ultimului ordin pe `symbol`, sau None dacă nu există."""
    last = _read().get(symbol)
    if not last or not last.get("timestamp"):
        return None
    return time.time() - last["timestamp"]


def describe_last_trade(symbol):
    last = _read().get(symbol)
    if not last or not last.get("timestamp"):
        return f"{symbol}: niciun ordin înregistrat"
    age = time.time() - last["timestamp"]
    return (f"{symbol}: ultim={last.get('side')} | age={age:.1f}s | "
            f"proc={last.get('process_name')} | tid={last.get('thread_id')} | "
            f"clientId={last.get('client_order_id')} | binId={last.get('binance_order_id')}")
