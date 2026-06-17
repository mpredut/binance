"""trade_cooldown.py — gate anti rapid-fire pentru ORDINE (specializare peste lock.Cooldown).

Mecanismul generic (FileLock + Cooldown) trăiește în pachetul `lock/` și poate fi
reutilizat pentru orice operație, nu doar trade. Aici păstrăm DOAR API-ul istoric
(reserve_trade / trade_slot / release_trade / update_binance_order_id / ...) ca să nu
schimbăm chokepoint-ul `__place_order` din binance_api/bapi_placeorder.py.

Împiedică plasarea a două ordine pe ACELAȘI simbol la mai puțin de `cooldown_sec`
(implicit citit din trade_cooldown.conf → [cooldown] default_sec), indiferent de
combinație (BUY/BUY, SELL/SELL, BUY/SELL). Sigur cross-PROCES și cross-THREAD (fcntl.flock).

Flux la chokepoint (__place_order):
    with trade_slot(side, symbol) as slot:
        if not slot.allowed: return None     # blocat de cooldown
        order = ...plasează...
        if order: slot.commit(order_id)       # succes → cooldown rămâne activ
        # altfel (eșec/excepție/uitat) → release AUTOMAT la ieșire (rollback)
"""
import os
import time
import contextlib
import configparser

from .cooldown import Cooldown

BASE_DIR = os.path.dirname(os.path.abspath(__file__))          # = lock/
STATE_FILE = os.path.join(BASE_DIR, "trade_cooldown.json")     # stare runtime (gitignored)
LOCK_FILE = os.path.join(BASE_DIR, "trade_cooldown.lock")      # lock fcntl (gitignored)
CONF_FILE = os.path.join(BASE_DIR, "trade_cooldown.conf")      # config text/ini (trackat)


def _load_cooldown_sec(fallback=20):
    """Citește [cooldown] default_sec din trade_cooldown.conf (text/ini).
    Fallback pe `fallback` dacă fișierul/cheia lipsesc sau sunt invalide."""
    try:
        cp = configparser.ConfigParser()
        if cp.read(CONF_FILE):
            return cp.getint("cooldown", "default_sec", fallback=fallback)
    except Exception:
        pass
    return fallback


DEFAULT_COOLDOWN_SEC = _load_cooldown_sec()

# Singleton lazy: respectă reasignarea STATE_FILE/LOCK_FILE (testele le suprascriu),
# reconstruind Cooldown-ul doar dacă s-au schimbat căile.
_cd = None


def _cooldown():
    global _cd
    if _cd is None or _cd.state_path != STATE_FILE or _cd.lock_path != LOCK_FILE:
        _cd = Cooldown("trade", state_path=STATE_FILE, lock_path=LOCK_FILE)
    return _cd


def reserve_trade(side, symbol, cooldown_sec=DEFAULT_COOLDOWN_SEC, client_order_id=None):
    """Verifică-și-rezervă ATOMIC dreptul de a plasa un ordin pe `symbol`.
    (True, entry) → permis / (False, last_entry) → blocat (ordin în ultimele cooldown_sec)."""
    return _cooldown().reserve(symbol, cooldown_sec, side=side, symbol=symbol,
                               client_order_id=client_order_id, binance_order_id=None)


def release_trade(symbol):
    """Anulează rezervarea pt `symbol` (ex. ordinul a EȘUAT) → nu mai blocăm cooldown-ul."""
    _cooldown().release(symbol)


def update_binance_order_id(symbol, order_id):
    """Completează orderId-ul Binance după plasarea cu succes."""
    _cooldown().update(symbol, binance_order_id=order_id)


class _TradeReservation:
    """Adaptor peste lock.Reservation: commit(order_id) POZIȚIONAL → binance_order_id
    (păstrează semnătura așteptată de bapi_placeorder: `slot.commit(order.get("orderId"))`)."""

    def __init__(self, res):
        self._res = res

    @property
    def allowed(self):
        return self._res.allowed

    @property
    def info(self):
        return self._res.info

    def commit(self, binance_order_id=None):
        self._res.commit(binance_order_id=binance_order_id)


@contextlib.contextmanager
def trade_slot(side, symbol, cooldown_sec=DEFAULT_COOLDOWN_SEC, client_order_id=None):
    """RAII / scope-based pentru cooldown (ca un guard C++) — vezi docstring-ul modulului."""
    with _cooldown().slot(symbol, cooldown_sec, side=side, symbol=symbol,
                          client_order_id=client_order_id, binance_order_id=None) as res:
        yield _TradeReservation(res)


def get_last_trade_age(symbol):
    """Vârsta (secunde) a ultimului ordin pe `symbol`, sau None dacă nu există."""
    return _cooldown().last_age(symbol)


def describe_last_trade(symbol):
    last = _cooldown().get(symbol)
    if not last or not last.get("timestamp"):
        return f"{symbol}: niciun ordin înregistrat"
    age = time.time() - last["timestamp"]
    return (f"{symbol}: ultim={last.get('side')} | age={age:.1f}s | "
            f"proc={last.get('process_name')} | tid={last.get('thread_id')} | "
            f"clientId={last.get('client_order_id')} | binId={last.get('binance_order_id')}")
