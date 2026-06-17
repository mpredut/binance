"""lock — primitive de sincronizare locale, reutilizabile (nu doar pentru trade).

FileLock  — mutex cross-PROCES + cross-THREAD (fcntl.flock pe un fd propriu).
Cooldown  — gate „nu repeta operația pe <key> mai des de <ttl>s", cu rezervare
            atomică + slot RAII, persistat pe disc. Generic peste orice resursă.

Vezi `trade_cooldown.py` (rădăcină) pentru specializarea pe ordine de trading.
"""
from .file_lock import FileLock
from .cooldown import Cooldown, Reservation

__all__ = ["FileLock", "Cooldown", "Reservation"]
