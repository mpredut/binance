# order_retry.py
"""Provider-agnostic persistent queue for retrying failed placement attempts.

Any process calling ``Instrument.place`` may enqueue a rejected or failed attempt;
``order_retry_worker.py`` is intended to be the single consumer. Records are stored
as JSONL under ``cachedb`` and queue mutations are serialized with a cross-process
file lock.

Each record preserves the symbol, side, quantity, placement options, requested and
reference prices, timestamps, and attempt count. A retry recalculates its placement
from the current market price and proceeds only when ``price_gate_ok`` accepts that
price. Deduplication retains one pending record per symbol and side, refreshing its
target while preserving its original age.

This is not a transactional outbox. Claiming removes a record before venue submission,
so a worker crash in that interval can lose the intent. Also, queue admission reflects
``Instrument.place`` failure, not a fresh evaluation of the strategy signal; the worker
relies on the placement guards and the stored price constraint. Configuration lives in
``order_retry_config.env``.
"""
import os
import json
import time
import uuid
import tempfile

from lock import FileLock

from botcore import load_dotenv as _load_dotenv
_load_dotenv("order_retry_config.env")

RETRY_ENABLED = os.environ.get("RETRY_ENABLED", "true").strip().lower() == "true"
RETRY_INTERVAL_SEC = float(os.environ.get("RETRY_INTERVAL_SEC", "300"))
RETRY_TTL_SEC = float(os.environ.get("RETRY_TTL_SEC", str(24 * 3600)))
RETRY_MAX_ATTEMPTS = int(float(os.environ.get("RETRY_MAX_ATTEMPTS", "0")))
# Retry only at an equal or more favorable current price than the original request.
RETRY_PRICE_TOL = float(os.environ.get("RETRY_PRICE_TOL", "0.002"))
# Deduplication retains one pending record per symbol and side and refreshes its target.
RETRY_DEDUP = os.environ.get("RETRY_DEDUP", "true").strip().lower() == "true"
# Hard queue-size bound; zero disables the bound.
RETRY_MAX_QUEUE = int(float(os.environ.get("RETRY_MAX_QUEUE", "500")))

_ROOT = os.path.dirname(os.path.abspath(__file__))
QUEUE_FILE = os.path.join(_ROOT, "cachedb", "order_retry_queue.jsonl")
LOCK_FILE = os.path.join(_ROOT, "cachedb", "order_retry_queue.lock")


def _ensure_dir():
    os.makedirs(os.path.dirname(QUEUE_FILE), exist_ok=True)


def _read_nolock():
    """Read the queue while the caller holds the lock; skip malformed lines."""
    if not os.path.exists(QUEUE_FILE):
        return []
    items = []
    with open(QUEUE_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
    return items


def enqueue(symbol, side, qty, place_kwargs=None, requested_price=None, ref_price=None,
            now=None, created_ts=None, attempts=0, last_attempt_ts=0.0):
    """Add or refresh a failed placement attempt while holding the queue lock.

    With deduplication enabled, one record is retained per symbol and side. A match
    receives the newest target price, quantity, and options while preserving the
    oldest creation time and highest attempt counters. Returns the new or retained
    record ID, or ``None`` when retries are disabled or the queue is full.
    """
    if not RETRY_ENABLED:
        return None
    now = now if now is not None else time.time()
    side_u = (side or "").upper()
    cts = float(created_ts) if created_ts is not None else now
    rec = {
        "id": uuid.uuid4().hex,
        "symbol": symbol,
        "side": side_u,
        "qty": qty,
        "place_kwargs": dict(place_kwargs or {}),
        "requested_price": requested_price,
        "ref_price": ref_price,
        "created_ts": cts,
        "attempts": int(attempts),
        "last_attempt_ts": float(last_attempt_ts),
    }
    _ensure_dir()
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        if RETRY_DEDUP:
            for e in existing:
                if e.get("symbol") == symbol and (e.get("side") or "").upper() == side_u:
                    # Refresh one symbol/side target while preserving age and attempts.
                    e["requested_price"] = requested_price
                    e["ref_price"] = ref_price
                    e["qty"] = qty
                    e["place_kwargs"] = dict(place_kwargs or {})
                    e["created_ts"] = min(float(e.get("created_ts", cts)), cts)
                    e["attempts"] = max(int(e.get("attempts", 0)), int(attempts))
                    e["last_attempt_ts"] = max(float(e.get("last_attempt_ts", 0)),
                                               float(last_attempt_ts))
                    _write_nolock(existing)
                    return e.get("id")
        if RETRY_MAX_QUEUE > 0 and len(existing) >= RETRY_MAX_QUEUE:
            print(f"[order_retry] coada plina ({len(existing)}/{RETRY_MAX_QUEUE}) "
                  f"— NU adaug {side_u} {symbol}")
            return None
        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return rec["id"]


def load_all(now=None):
    """Toate intrarile din coada (sub lock). Liniile corupte sunt sarite (defensiv)."""
    _ensure_dir()
    with FileLock(LOCK_FILE):
        return _read_nolock()


def claim(ids, now=None):
    """Atomically remove and return entries whose IDs are claimed by the worker.

    The worker claims before external submission to prevent concurrent retries and
    re-enqueues an ordinary failure. A process crash after this removal and before
    re-enqueue is not recoverable from this queue alone.
    """
    ids = set(ids)
    if not ids:
        return []
    _ensure_dir()
    claimed = []
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        remaining = [r for r in existing if r.get("id") not in ids]
        claimed = [r for r in existing if r.get("id") in ids]
        if claimed:
            _write_nolock(remaining)
    return claimed


def resolve(symbol, side):
    """Atomically remove pending records satisfied by a normal successful placement.

    Local retry callers can succeed before the global worker. Removing the old record
    prevents a later duplicate order. Returns the number of removed records.
    """
    side_u = (side or "").upper()
    _ensure_dir()
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        remaining = [
            rec for rec in existing
            if not (rec.get("symbol") == symbol
                    and (rec.get("side") or "").upper() == side_u)
        ]
        removed = len(existing) - len(remaining)
        if removed:
            _write_nolock(remaining)
        return removed


def _write_nolock(items):
    """Replace the queue file atomically while the caller holds the lock.

    The temporary-file rename prevents partial-file visibility, but no directory or
    file ``fsync`` is performed, so this is not a power-loss durability guarantee.
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(QUEUE_FILE), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in items:
                f.write(json.dumps(rec) + "\n")
        os.replace(tmp, QUEUE_FILE)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def rewrite(items):
    """Replace the queue with ``items`` atomically while holding the lock."""
    _ensure_dir()
    with FileLock(LOCK_FILE):
        _write_nolock(items)


def is_expired(rec, now=None):
    """Return true after the record exceeds its TTL or hard attempt limit."""
    now = now if now is not None else time.time()
    if now - float(rec.get("created_ts", 0)) > RETRY_TTL_SEC:
        return True
    if RETRY_MAX_ATTEMPTS > 0 and int(rec.get("attempts", 0)) >= RETRY_MAX_ATTEMPTS:
        return True
    return False


def is_due(rec, now=None):
    """Return true once the retry interval has elapsed since creation or last attempt."""
    now = now if now is not None else time.time()
    base = max(float(rec.get("last_attempt_ts", 0)), float(rec.get("created_ts", 0)))
    return (now - base) >= RETRY_INTERVAL_SEC


def price_gate_ok(rec, current_price, tol=None):
    """Return whether the current price is favorable relative to the stored request.

    SELL requires ``current >= requested*(1-tol)`` and BUY requires
    ``current <= requested*(1+tol)``. Missing or invalid prices fail closed.
    """
    if current_price is None:
        return False
    req = rec.get("requested_price")
    if req is None:
        return False
    try:
        req = float(req)
    except (TypeError, ValueError):
        return False
    if req <= 0:
        return False
    tol = RETRY_PRICE_TOL if tol is None else tol
    side = (rec.get("side") or "").upper()
    if side == "SELL":
        return current_price >= req * (1.0 - tol)
    if side == "BUY":
        return current_price <= req * (1.0 + tol)
    return True
