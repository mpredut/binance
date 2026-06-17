"""Cooldown — gate GENERIC „o operație pe <key> cel mult o dată la <ttl>s".

Verificare-și-rezervare ATOMICĂ (FileLock) + stare persistată pe disc + slot RAII.
Reutilizabil pentru orice resursă (ordine de trade, alerte, sync-uri etc.), nu doar
trading. Lock-ul NU e ținut peste operația propriu-zisă — doar peste reserve/release
(microsecunde) → fără deadlock, fără I/O lung sub lock; ce persistă e starea, nu lock-ul.

    cd = Cooldown("trade", state_path=..., lock_path=...)
    with cd.slot("BTCUSDC", 180, side="BUY") as s:
        if not s.allowed:                 # blocat de cooldown
            return
        ...fă operația...
        if ok:
            s.commit(order_id=123)        # succes → rezervarea RĂMÂNE (cooldown activ)
        # altfel (eșec/excepție/uitat) → release AUTOMAT la ieșire (rollback)
"""
import os
import json
import time
import socket
import threading
import multiprocessing
import contextlib

from .file_lock import FileLock


class Reservation:
    """Obiectul dat de `Cooldown.slot` (stil RAII / guard C++).
    allowed=False → blocat de cooldown. commit(**fields) DOAR dacă operația a reușit
    → rezervarea rămâne (cooldown activ) și scrie câmpurile extra. Fără commit, la
    ieșirea din `with` rezervarea e anulată (rollback)."""

    def __init__(self, cooldown, allowed, info, key):
        self._cd = cooldown
        self.allowed = allowed
        self.info = info
        self.key = key
        self._committed = False

    def commit(self, **fields):
        self._committed = True
        if fields:
            self._cd.update(self.key, **fields)


class Cooldown:
    """Gate generic anti rapid-fire, cross-PROCES + cross-THREAD, persistat pe disc."""

    def __init__(self, name, state_path=None, lock_path=None, base_dir=None):
        self.name = name
        base = base_dir or os.getcwd()
        self.state_path = state_path or os.path.join(base, f"cooldown_{name}.json")
        self.lock_path = lock_path or os.path.join(base, f"cooldown_{name}.lock")

    # ── stocare ──────────────────────────────────────────────────────────────
    def _read(self):
        try:
            with open(self.state_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _write(self, state):
        tmp = f"{self.state_path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, self.state_path)          # atomic

    # ── API ──────────────────────────────────────────────────────────────────
    def reserve(self, key, ttl, **meta):
        """Verifică-și-rezervă ATOMIC dreptul de a opera pe `key`.
        (True, entry) → permis (rezervat acum) / (False, last) → blocat (< ttl sec)."""
        with FileLock(self.lock_path):
            state = self._read()
            last = state.get(key)
            now = time.time()
            if last and (now - last.get("timestamp", 0)) < ttl:
                return False, last
            entry = {
                "timestamp": now,
                "key": key,
                "pid": os.getpid(),
                "thread_id": threading.get_ident(),
                "process_name": multiprocessing.current_process().name,
                "hostname": socket.gethostname(),
            }
            entry.update(meta)
            state[key] = entry
            self._write(state)
            return True, entry

    def release(self, key):
        """Anulează rezervarea pt `key` (ex. operația a EȘUAT) → nu mai blocăm ttl-ul."""
        with FileLock(self.lock_path):
            state = self._read()
            if key in state:
                del state[key]
                self._write(state)

    def update(self, key, **fields):
        """Completează câmpuri pe rezervarea existentă (ex. id-ul rezultat)."""
        with FileLock(self.lock_path):
            state = self._read()
            if key in state:
                state[key].update(fields)
                self._write(state)

    def get(self, key):
        return self._read().get(key)

    def last_age(self, key):
        """Vârsta (secunde) a ultimei rezervări pe `key`, sau None."""
        last = self._read().get(key)
        if not last or not last.get("timestamp"):
            return None
        return time.time() - last["timestamp"]

    @contextlib.contextmanager
    def slot(self, key, ttl, **meta):
        """RAII / scope-based: rezervă la intrare, rollback automat la ieșire fără commit.
        Lock-ul fcntl NU e ținut peste corpul `with` (doar în reserve/release)."""
        allowed, info = self.reserve(key, ttl, **meta)
        res = Reservation(self, allowed, info, key)
        try:
            yield res
        finally:
            if allowed and not res._committed:
                self.release(key)                 # rollback: nimic făcut → nu blocăm
