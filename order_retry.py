# order_retry.py
"""Provider-agnostic persistent submission outbox.

``Instrument.place`` persists a valid intent before its provider submit and also
enqueues attempts rejected by an earlier guard;
``order_retry_worker.py`` is intended to be the single consumer. Records are stored
as JSONL under ``cachedb`` and queue mutations are serialized with a cross-process
file lock.

Each record preserves the symbol, side, quantity, placement options, requested and
reference prices, timestamps, and attempt count. A retry recalculates its placement
from the current market price and proceeds only when ``price_gate_ok`` accepts that
price. Optional legacy deduplication can retain one pending record per symbol and
side, but it is disabled operationally so independent strategy intents are preserved.

Claims are durable leases: a worker crash leaves the record unavailable only until the
lease expires. A trend deferral consumes neither attempt count nor TTL. The outbox owns
delivery through provider acceptance; it does not call an accepted LIMIT order a fill.
Queue retry still cannot reconstruct every originating strategy signal, so it relies on
placement guards and the stored price constraint. Configuration lives in
``order_retry_config.env``.
"""
import os
import json
import math
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
# Optional legacy compaction. Disabled operationally so independent strategies or
# distinct same-side intents cannot overwrite each other in the shared outbox.
RETRY_DEDUP = os.environ.get("RETRY_DEDUP", "false").strip().lower() == "true"
# Hard queue-size bound; zero disables the bound.
RETRY_MAX_QUEUE = int(float(os.environ.get("RETRY_MAX_QUEUE", "500")))
RETRY_CLAIM_LEASE_SEC = float(os.environ.get("RETRY_CLAIM_LEASE_SEC", "120"))

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


def _client_order_id(record_id, revision):
    """Return a Binance-compatible stable ID for one queued intent revision."""
    return f"OR_{record_id[:24]}_{int(revision)}"


def valid_record(rec):
    """Return whether a persisted record is safe to evaluate or submit."""
    try:
        qty = float(rec.get("qty"))
        created = float(rec.get("created_ts"))
        revision = int(rec.get("revision", 0))
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(
        rec.get("id") and rec.get("symbol") and
        str(rec.get("side") or "").upper() in {"BUY", "SELL"} and
        math.isfinite(qty) and qty > 0 and
        math.isfinite(created) and created > 0 and revision >= 0
    )


def enqueue(symbol, side, qty, place_kwargs=None, requested_price=None, ref_price=None,
            now=None, created_ts=None, attempts=0, last_attempt_ts=0.0,
            failure_reason=None):
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
    try:
        qty = float(qty)
        now = float(now)
    except (TypeError, ValueError, OverflowError):
        return None
    if (not symbol or side_u not in {"BUY", "SELL"} or
            not math.isfinite(qty) or qty <= 0 or
            not math.isfinite(now) or now <= 0):
        return None
    try:
        cts = float(created_ts) if created_ts is not None else now
        attempts = int(attempts)
        last_attempt_ts = float(last_attempt_ts)
    except (TypeError, ValueError, OverflowError):
        return None
    if (not math.isfinite(cts) or cts <= 0 or not math.isfinite(last_attempt_ts) or
            attempts < 0):
        return None
    record_id = uuid.uuid4().hex
    placement = dict(place_kwargs or {})
    placement.setdefault("client_order_id", _client_order_id(record_id, 0))
    rec = {
        "id": record_id,
        "symbol": symbol,
        "side": side_u,
        "qty": qty,
        "place_kwargs": placement,
        "requested_price": requested_price,
        "ref_price": ref_price,
        "created_ts": cts,
        "attempts": attempts,
        "last_attempt_ts": last_attempt_ts,
        "last_failure_reason": (str(failure_reason)
                                if failure_reason is not None else None),
        "ttl_started_ts": cts,
        "revision": 0,
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
                    revision = int(e.get("revision", 0)) + 1
                    refreshed_kwargs = dict(place_kwargs or {})
                    refreshed_kwargs.setdefault(
                        "client_order_id", _client_order_id(e.get("id", record_id), revision))
                    e["place_kwargs"] = refreshed_kwargs
                    e["created_ts"] = min(float(e.get("created_ts", cts)), cts)
                    e["attempts"] = max(int(e.get("attempts", 0)), int(attempts))
                    e["last_attempt_ts"] = max(float(e.get("last_attempt_ts", 0)),
                                               float(last_attempt_ts))
                    previous_reason = e.get("last_failure_reason")
                    next_reason = (str(failure_reason)
                                   if failure_reason is not None else None)
                    if (previous_reason == "trend_deferred"
                            and next_reason != "trend_deferred"):
                        e["ttl_started_ts"] = now
                    e["last_failure_reason"] = next_reason
                    e["revision"] = revision
                    _write_nolock(existing)
                    return e.get("id")
        if RETRY_MAX_QUEUE > 0 and len(existing) >= RETRY_MAX_QUEUE:
            print(f"[order_retry] coada plina ({len(existing)}/{RETRY_MAX_QUEUE}) "
                  f"— NU adaug {side_u} {symbol}")
            return None
        _write_nolock(existing + [rec])
    return rec["id"]


def load_all(now=None):
    """Return all queued entries under lock, defensively skipping corrupt lines."""
    _ensure_dir()
    with FileLock(LOCK_FILE):
        return _read_nolock()


def get(record_id):
    """Return one current queue record by ID under lock, or ``None``."""
    if not record_id:
        return None
    _ensure_dir()
    with FileLock(LOCK_FILE):
        for rec in _read_nolock():
            if rec.get("id") == record_id:
                return dict(rec)
    return None


def mark_failure(record_id, failure_reason, *, now=None, client_order_id=None):
    """Annotate an already persisted pre-submit record without changing its ID.

    ``client_order_id`` protects a newer concurrent revision from being overwritten
    by the result of an older submit call.
    """
    if not record_id:
        return False
    now = float(time.time() if now is None else now)
    changed = False
    _ensure_dir()
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        for rec in existing:
            if rec.get("id") != record_id:
                continue
            current_cid = dict(rec.get("place_kwargs") or {}).get("client_order_id")
            if client_order_id is not None and current_cid != client_order_id:
                continue
            previous = rec.get("last_failure_reason")
            reason = str(failure_reason) if failure_reason is not None else None
            if previous == "trend_deferred" and reason != "trend_deferred":
                rec["ttl_started_ts"] = now
            rec["last_failure_reason"] = reason
            changed = True
            break
        if changed:
            _write_nolock(existing)
    return changed


def resolve_record(record_id, *, client_order_id=None):
    """Remove only the exact persisted intent/revision satisfied by acceptance."""
    if not record_id:
        return False
    removed = False
    _ensure_dir()
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        remaining = []
        for rec in existing:
            matches = rec.get("id") == record_id
            if matches and client_order_id is not None:
                matches = (dict(rec.get("place_kwargs") or {}).get(
                    "client_order_id") == client_order_id)
            if matches:
                removed = True
            else:
                remaining.append(rec)
        if removed:
            _write_nolock(remaining)
    return removed


def claim(ids, now=None, lease_sec=None):
    """Lease entries without removing them, making crash recovery deterministic."""
    ids = set(ids)
    if not ids:
        return []
    _ensure_dir()
    now = float(time.time() if now is None else now)
    lease_sec = float(RETRY_CLAIM_LEASE_SEC if lease_sec is None else lease_sec)
    if not math.isfinite(lease_sec) or lease_sec <= 0:
        raise ValueError("claim lease must be finite and positive")
    claimed = []
    token = uuid.uuid4().hex
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        for rec in existing:
            if rec.get("id") not in ids:
                continue
            if float(rec.get("claim_until", 0) or 0) > now:
                continue
            revision = int(rec.get("revision", 0))
            placement = dict(rec.get("place_kwargs") or {})
            placement.setdefault(
                "client_order_id", _client_order_id(rec.get("id", ""), revision))
            rec["place_kwargs"] = placement
            rec["revision"] = revision
            rec["claim_token"] = token
            rec["claim_until"] = now + lease_sec
            rec["claim_revision"] = revision
            claimed.append(dict(rec))
        if claimed:
            _write_nolock(existing)
    return claimed


def complete_claim(claimed, outcome, now=None, *, failure_reason=None):
    """Finalize one leased record as success, failure, or release.

    A successful placement removes only the exact revision that was submitted. If a
    writer refreshed the intent while it was leased, that newer revision remains due.
    Failure increments attempts in place. Release simply clears the lease.
    """
    if outcome not in {"success", "failure", "deferred", "release"}:
        raise ValueError(f"invalid claim outcome: {outcome}")
    now = float(time.time() if now is None else now)
    changed = False
    _ensure_dir()
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        remaining = []
        for rec in existing:
            matches = (rec.get("id") == claimed.get("id") and
                       rec.get("claim_token") == claimed.get("claim_token"))
            if not matches:
                remaining.append(rec)
                continue
            changed = True
            same_revision = int(rec.get("revision", 0)) == int(
                claimed.get("claim_revision", 0))
            if outcome == "success" and same_revision:
                continue
            if outcome == "failure" and same_revision:
                rec["attempts"] = max(int(rec.get("attempts", 0)),
                                      int(claimed.get("attempts", 0))) + 1
                rec["last_attempt_ts"] = now
                next_reason = (str(failure_reason)
                               if failure_reason is not None else None)
                if (rec.get("last_failure_reason") == "trend_deferred"
                        and next_reason != "trend_deferred"):
                    # TTL begins only after the trend gate stops deferring.  Time
                    # spent waiting for a favorable trend cannot discard an intent.
                    rec["ttl_started_ts"] = now
                rec["last_failure_reason"] = next_reason
            elif outcome == "deferred" and same_revision:
                rec["last_attempt_ts"] = now
                rec["last_failure_reason"] = "trend_deferred"
            for key in ("claim_token", "claim_until", "claim_revision"):
                rec.pop(key, None)
            remaining.append(rec)
        if changed:
            _write_nolock(remaining)
    return changed


def discard(ids):
    """Atomically remove records by ID without submitting them (expiry/give-up)."""
    ids = set(ids)
    if not ids:
        return []
    _ensure_dir()
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        removed = [rec for rec in existing if rec.get("id") in ids]
        if removed:
            _write_nolock([rec for rec in existing if rec.get("id") not in ids])
        return removed


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

    The temporary file and parent directory are fsynced around the atomic rename so a
    successful mutation survives an ordinary process crash and is power-loss resilient
    on filesystems that honor those primitives.
    """
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(QUEUE_FILE), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for rec in items:
                f.write(json.dumps(rec) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, QUEUE_FILE)
        try:
            dfd = os.open(os.path.dirname(QUEUE_FILE), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
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
    """Return true after active retry TTL or hard attempt limit.

    A trend-deferred intent has made no venue attempt and therefore never consumes
    TTL or the attempt budget.  Once another refusal reason appears, ``ttl_started_ts``
    is reset and normal expiry resumes.
    """
    try:
        now = float(time.time() if now is None else now)
        claim_until = float(rec.get("claim_until", 0) or 0)
        created = float(rec.get("created_ts", 0))
        ttl_started = float(rec.get("ttl_started_ts", created))
        attempts = int(rec.get("attempts", 0))
    except (TypeError, ValueError, OverflowError):
        return True
    if (not all(math.isfinite(v) for v in (now, claim_until, created, ttl_started))
            or created <= 0 or ttl_started <= 0):
        return True
    if claim_until > now:
        return False
    if rec.get("last_failure_reason") == "trend_deferred":
        return False
    return ((now - ttl_started) > RETRY_TTL_SEC or
            (RETRY_MAX_ATTEMPTS > 0 and attempts >= RETRY_MAX_ATTEMPTS))


def is_due(rec, now=None):
    """Return true once the retry interval has elapsed since creation or last attempt."""
    try:
        now = float(time.time() if now is None else now)
        claim_until = float(rec.get("claim_until", 0) or 0)
        base = max(float(rec.get("last_attempt_ts", 0)),
                   float(rec.get("created_ts", 0)))
    except (TypeError, ValueError, OverflowError):
        return False
    if not all(math.isfinite(v) for v in (now, claim_until, base)):
        return False
    return claim_until <= now and (now - base) >= RETRY_INTERVAL_SEC


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
        current_price = float(current_price)
        req = float(req)
        tol = float(RETRY_PRICE_TOL if tol is None else tol)
    except (TypeError, ValueError, OverflowError):
        return False
    if (not math.isfinite(current_price) or current_price <= 0 or
            not math.isfinite(req) or req <= 0 or
            not math.isfinite(tol) or tol < 0):
        return False
    side = (rec.get("side") or "").upper()
    if side == "SELL":
        return current_price >= req * (1.0 - tol)
    if side == "BUY":
        return current_price <= req * (1.0 + tol)
    return False
