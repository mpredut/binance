# order_retry.py
"""Provider-agnostic order lifecycle and persistent submission outbox.

This is the canonical home of reusable ``OrderLifecycle`` mechanics. Stateful
strategies may keep their financial campaign state, but use
``TrackedOrderLifecycle`` here for persist-before-submit, response-loss recovery,
status reconciliation, partial/terminal observation, and bounded cancellation.

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
delivery through terminal venue truth: acceptance is persisted and monitored, never
called a fill. Rejected/expired orders can retry only their unfilled remainder, while
an intentional/ambiguous cancellation is never blindly resubmitted. Queue retry still
cannot reconstruct every originating strategy signal, so it relies on placement guards
and the stored price constraint. Configuration lives in ``order_retry_config.env``.
"""
import os
import json
import math
import time
import uuid
import tempfile
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from lock import FileLock
from providers.strategy_executor import OrderStatus

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


def order_id_from_response(response):
    """Extract one venue order ID from common Binance/Kraken/HL/T212 shapes."""
    if not isinstance(response, dict):
        return None
    native = response.get("orderId", response.get("id", response.get("txid")))
    if isinstance(native, (list, tuple)):
        native = next((value for value in native if str(value).strip()), None)
    if native is None or not str(native).strip():
        return None
    return str(native)


def valid_record(rec):
    """Return whether a persisted record is safe to evaluate or submit."""
    try:
        qty = float(rec.get("qty"))
        created = float(rec.get("created_ts"))
        revision = int(rec.get("revision", 0))
    except (TypeError, ValueError, OverflowError):
        return False
    lifecycle = str(rec.get("lifecycle") or "submit_pending").lower()
    lifecycle_ok = lifecycle in {"submit_pending", "accepted"}
    if lifecycle == "accepted" and not str(rec.get("order_id") or "").strip():
        lifecycle_ok = False
    return bool(
        rec.get("id") and rec.get("symbol") and
        str(rec.get("side") or "").upper() in {"BUY", "SELL"} and
        math.isfinite(qty) and qty > 0 and
        math.isfinite(created) and created > 0 and revision >= 0 and lifecycle_ok
    )


def enqueue(symbol, side, qty, place_kwargs=None, requested_price=None, ref_price=None,
            now=None, created_ts=None, attempts=0, last_attempt_ts=0.0,
            failure_reason=None, *, provider_name=None, intent_id=None, kind=None):
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
        "intent_id": str(intent_id or f"outbox-{record_id}"),
        "provider_name": (None if provider_name is None else str(provider_name)),
        "symbol": symbol,
        "side": side_u,
        "kind": (None if kind is None else str(kind)),
        "qty": qty,
        "requested_qty_total": qty,
        "delivered_qty": 0.0,
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
        "lifecycle": "submit_pending",
    }
    _ensure_dir()
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        if RETRY_DEDUP:
            for e in existing:
                if (e.get("symbol") == symbol
                        and (e.get("side") or "").upper() == side_u
                        and str(e.get("lifecycle") or "submit_pending").lower()
                        == "submit_pending"):
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
                    e["provider_name"] = (None if provider_name is None
                                            else str(provider_name))
                    e["intent_id"] = str(intent_id or e.get("intent_id")
                                         or f"outbox-{e.get('id', record_id)}")
                    e["kind"] = None if kind is None else str(kind)
                    e["lifecycle"] = "submit_pending"
                    e.pop("order_id", None)
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


def _append_order_history(rec, *, observed_at, status=None):
    """Retain a bounded audit trail when one logical intent has several orders."""
    order_id = rec.get("order_id")
    if not order_id:
        return
    row = {
        "order_id": str(order_id),
        "client_order_id": dict(rec.get("place_kwargs") or {}).get(
            "client_order_id"),
        "revision": int(rec.get("revision", 0)),
        "observed_at": float(observed_at),
    }
    if status is not None:
        row.update({
            "status": str(getattr(status, "status", "") or ""),
            "venue_status": str(getattr(status, "venue_status", "") or ""),
            "filled_qty": float(getattr(status, "filled_qty", 0.0) or 0.0),
            "cost": float(getattr(status, "cost", 0.0) or 0.0),
            "fee": float(getattr(status, "fee", 0.0) or 0.0),
        })
    history = list(rec.get("order_history") or [])
    if not history or history[-1].get("order_id") != row["order_id"]:
        history.append(row)
    else:
        history[-1].update(row)
    rec["order_history"] = history[-20:]


def _apply_accepted(rec, order, now, provider_name=None):
    order_id = order_id_from_response(order)
    if order_id is None:
        return False
    rec["order_id"] = order_id
    rec["lifecycle"] = "accepted"
    rec["accepted_ts"] = float(now)
    rec["last_status_ts"] = 0.0
    rec["last_failure_reason"] = None
    if provider_name is not None:
        rec["provider_name"] = str(provider_name)
    if isinstance(order, dict) and order.get("status") is not None:
        rec["submit_status"] = str(order.get("status"))
    _append_order_history(rec, observed_at=now)
    return True


def mark_accepted(record_id, order, *, now=None, client_order_id=None,
                  provider_name=None):
    """Move one exact pre-submit revision to durable accepted tracking."""
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
            changed = _apply_accepted(
                rec, order, now, provider_name=provider_name)
            break
        if changed:
            _write_nolock(existing)
    return changed


def resolve_record(record_id, *, client_order_id=None):
    """Legacy helper: remove one exact *unsubmitted* persisted revision.

    Once a venue ID has been accepted, only terminal reconciliation may remove the
    tracker. This prevents an old caller from turning acceptance into an implicit
    fill acknowledgement.
    """
    if not record_id:
        return False
    removed = False
    _ensure_dir()
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        remaining = []
        for rec in existing:
            matches = (
                rec.get("id") == record_id
                and str(rec.get("lifecycle") or "submit_pending").lower()
                == "submit_pending"
            )
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


def complete_claim(claimed, outcome, now=None, *, failure_reason=None,
                   order=None, status=None, provider_name=None):
    """Finalize one exact leased revision without losing accepted-order tracking.

    ``accepted`` persists the venue ID instead of deleting the record. ``observed``
    keeps an open/partial order. ``retry_terminal`` creates a new client-ID revision
    for only the unfilled remainder. ``success`` removes a filled or intentionally
    canceled terminal order.
    """
    allowed = {
        "success", "failure", "deferred", "release", "accepted",
        "observed", "status_error", "retry_terminal",
    }
    if outcome not in allowed:
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
            if outcome == "accepted" and same_revision:
                if not _apply_accepted(
                        rec, order, now, provider_name=provider_name):
                    rec["last_failure_reason"] = "response_without_order_id"
                    rec["attempts"] = max(int(rec.get("attempts", 0)),
                                          int(claimed.get("attempts", 0))) + 1
                    rec["last_attempt_ts"] = now
            elif outcome == "observed" and same_revision:
                rec["last_status_ts"] = now
                rec["last_status"] = str(getattr(status, "status", "") or "")
                rec["venue_status"] = str(
                    getattr(status, "venue_status", "") or "")
                rec["filled_qty"] = float(
                    getattr(status, "filled_qty", 0.0) or 0.0)
                rec["filled_cost"] = float(getattr(status, "cost", 0.0) or 0.0)
                rec["filled_fee"] = float(getattr(status, "fee", 0.0) or 0.0)
                rec.pop("status_error", None)
            elif outcome == "status_error" and same_revision:
                rec["last_status_ts"] = now
                rec["status_error"] = (str(failure_reason)
                                       if failure_reason is not None else "unknown")
            elif outcome == "retry_terminal" and same_revision:
                filled_qty = max(0.0, float(
                    getattr(status, "filled_qty", 0.0) or 0.0))
                current_qty = float(rec.get("qty") or 0.0)
                remaining_qty = max(0.0, current_qty - filled_qty)
                _append_order_history(rec, observed_at=now, status=status)
                rec["delivered_qty"] = float(rec.get("delivered_qty") or 0.0) + min(
                    current_qty, filled_qty)
                if remaining_qty <= max(1e-12, current_qty * 1e-9):
                    continue
                revision = int(rec.get("revision", 0)) + 1
                rec["revision"] = revision
                rec["qty"] = remaining_qty
                rec["attempts"] = max(int(rec.get("attempts", 0)),
                                      int(claimed.get("attempts", 0))) + 1
                rec["last_attempt_ts"] = now
                rec["last_failure_reason"] = (
                    "terminal_" + str(getattr(status, "venue_status", "")
                                      or getattr(status, "status", "unknown")).lower())
                rec["lifecycle"] = "submit_pending"
                for key in (
                        "order_id", "accepted_ts", "last_status_ts", "last_status",
                        "venue_status", "submit_status", "filled_qty", "filled_cost",
                        "filled_fee"):
                    rec.pop(key, None)
                placement = dict(rec.get("place_kwargs") or {})
                placement["client_order_id"] = _client_order_id(
                    rec.get("id", ""), revision)
                rec["place_kwargs"] = placement
            elif outcome == "failure" and same_revision:
                rec["attempts"] = max(int(rec.get("attempts", 0)),
                                      int(claimed.get("attempts", 0))) + 1
                rec["last_attempt_ts"] = now
                next_reason = (str(failure_reason)
                               if failure_reason is not None else None)
                if (rec.get("last_failure_reason") == "trend_deferred"
                        and next_reason != "trend_deferred"):
                    # TTL begins only after the trend gate stops deferring. Time
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


@dataclass(frozen=True)
class OutboxStatusTransition:
    """Result of one accepted outbox record advancing from one venue snapshot."""

    action: str  # observed, filled, retry_terminal, terminal
    status_changed: bool = False
    remaining_qty: float = 0.0

    def __post_init__(self):
        if self.action not in {
                "observed", "filled", "retry_terminal", "terminal"}:
            raise ValueError(f"invalid outbox status action: {self.action!r}")


def _outbox_retryable_terminal(status: OrderStatus) -> bool:
    """Preserve the current mechanical outbox terminal rule.

    This is deliberately not the future financial-policy API. Rejected/expired
    orders retain the existing remainder-retry behavior; other cancellations remain
    terminal. Strategy-specific policy will be added separately.
    """
    native = str(status.venue_status or "").upper()
    return native in {"REJECTED", "EXPIRED"} or status.status == "expired"


def advance_claimed_status(claimed: dict, status: OrderStatus,
                           now=None) -> OutboxStatusTransition:
    """Persist one accepted order-status transition without venue I/O.

    ``Instrument.place`` never calls this function. The external worker obtains one
    status snapshot, then delegates the state mutation here. Therefore terminal
    monitoring remains outside the submit call and every invocation is bounded.
    """
    if not isinstance(status, OrderStatus):
        raise TypeError("status must be OrderStatus")
    if str(claimed.get("lifecycle") or "").lower() != "accepted":
        raise ValueError("status transition requires an accepted claimed record")

    if not status.terminal:
        fingerprint = (
            str(status.status), float(status.filled_qty),
            str(status.venue_status),
        )
        previous = (
            str(claimed.get("last_status") or ""),
            float(claimed.get("filled_qty") or 0.0),
            str(claimed.get("venue_status") or ""),
        )
        complete_claim(claimed, "observed", now, status=status)
        return OutboxStatusTransition(
            "observed", status_changed=fingerprint != previous)

    if status.status == "closed":
        complete_claim(claimed, "success", now, status=status)
        return OutboxStatusTransition("filled", status_changed=True)

    current_qty = float(claimed.get("qty") or 0.0)
    remaining_qty = max(0.0, current_qty - status.filled_qty)
    if _outbox_retryable_terminal(status):
        complete_claim(claimed, "retry_terminal", now, status=status)
        return OutboxStatusTransition(
            "retry_terminal", status_changed=True,
            remaining_qty=remaining_qty)

    complete_claim(claimed, "success", now, status=status)
    return OutboxStatusTransition(
        "terminal", status_changed=True, remaining_qty=remaining_qty)


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
    """Legacy helper: remove only unaccepted pending records by symbol and side.

    Accepted trackers are venue truth and can only be removed by exact terminal
    reconciliation. New code should use ``mark_accepted``/``complete_claim``.
    """
    side_u = (side or "").upper()
    _ensure_dir()
    with FileLock(LOCK_FILE):
        existing = _read_nolock()
        remaining = [
            rec for rec in existing
            if not (rec.get("symbol") == symbol
                    and (rec.get("side") or "").upper() == side_u
                    and str(rec.get("lifecycle") or "submit_pending").lower()
                    == "submit_pending")
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
    # An accepted order is real venue state, not a stale submit attempt. Never
    # discard its tracker merely because the submission TTL elapsed.
    if str(rec.get("lifecycle") or "submit_pending").lower() == "accepted":
        return False
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
        if str(rec.get("lifecycle") or "submit_pending").lower() == "accepted":
            base = max(float(rec.get("last_status_ts", 0) or 0),
                       float(rec.get("accepted_ts", 0) or 0),
                       float(rec.get("created_ts", 0)))
        else:
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


# ---------------------------------------------------------------------------
# Strategy-owned lifecycle facade
# ---------------------------------------------------------------------------

PersistPending = Callable[[Optional[dict]], None]
SubmitIntent = Callable[[], object]


@dataclass(frozen=True)
class TrackedOrderResult:
    """One lifecycle observation without equating submit acceptance with a fill."""

    outcome: str  # active, terminal, absent, retryable
    intent: dict
    status: Optional[OrderStatus] = None

    def __post_init__(self):
        if self.outcome not in {"active", "terminal", "absent", "retryable"}:
            raise ValueError(f"invalid tracked-order outcome: {self.outcome!r}")
        if self.outcome == "terminal" and self.status is None:
            raise ValueError("terminal tracked-order result requires status")

    @property
    def order_known(self) -> bool:
        return bool(self.intent.get("order_id"))


class StrategyExecutorLifecycleApi:
    """Adapt one strict executor to the lifecycle lookup contract.

    Stateful strategies already own a credential-scoped executor and should not
    create a second global provider merely to reconcile an ambiguous submission.
    This adapter keeps lookup, status, and cancel calls on that same executor.
    """

    def __init__(self, executor):
        self.executor = executor

    def order_by_client_id(self, symbol: str, client_order_id: str, *,
                           provider_name=None):
        method = getattr(self.executor, "order_by_client_id", None)
        if not callable(method):
            raise RuntimeError(
                f"{provider_name or type(self.executor).__name__}: "
                "order_by_client_id is unsupported")
        return method(symbol, str(client_order_id))

    def order_status(self, symbol: str, order_id: str, *, provider_name=None):
        return self.executor.order_status(symbol, str(order_id))

    def cancel_order(self, symbol: str, order_id: str, *, provider_name=None):
        return self.executor.cancel_order(symbol, str(order_id))


def _lifecycle_finite(raw, *, positive=False):
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or (positive and value <= 0):
        return None
    return value


def _terminal_payload(status: OrderStatus, observed_at: float) -> dict:
    return {
        "status": status.status,
        "venue_status": status.venue_status,
        "filled_qty": status.filled_qty,
        "cost": status.cost,
        "fee": status.fee,
        "observed_at": observed_at,
    }


def _status_from_terminal_payload(payload) -> Optional[OrderStatus]:
    if not isinstance(payload, dict):
        return None
    try:
        status = OrderStatus(
            str(payload["status"]),
            payload["filled_qty"],
            payload["cost"],
            payload["fee"],
            venue_status=payload.get("venue_status", ""),
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return status if status.terminal else None


class TrackedOrderLifecycle:
    """State machine for one persisted order owned by a strategy.

    The caller supplies a persistence callback because campaign state belongs to
    the strategy. The callback must durably replace the pending intent; raising
    before submit prevents the external side effect. Terminal venue truth stays
    persisted in ``terminal_status`` until the strategy atomically applies the fill
    and removes pending from its own state.

    The class does not run as a daemon. The owning strategy calls ``submit`` once
    and ``reconcile`` from later bounded ticks. The fleet-wide daemon is still
    ``order_retry_worker.py`` and operates only on the shared outbox records.
    """

    def __init__(self, market_api, *, provider_name: str, venue: Optional[str] = None,
                 missing_confirmations: int = 2,
                 retry_on_lookup_error: bool = False,
                 max_age_seconds: Optional[float] = None,
                 audit=None, clock: Callable[[], float] = time.time):
        if not str(provider_name or "").strip():
            raise ValueError("provider_name is required")
        if int(missing_confirmations) <= 0:
            raise ValueError("missing_confirmations must be positive")
        if max_age_seconds is not None:
            parsed_age = _lifecycle_finite(max_age_seconds, positive=True)
            if parsed_age is None:
                raise ValueError("max_age_seconds must be finite and positive")
            max_age_seconds = parsed_age
        self.market_api = market_api
        self.provider_name = str(provider_name)
        self.venue = str(venue or provider_name)
        self.missing_confirmations = int(missing_confirmations)
        self.retry_on_lookup_error = bool(retry_on_lookup_error)
        self.max_age_seconds = max_age_seconds
        self.audit = audit
        self.clock = clock

    def _now(self) -> float:
        now = _lifecycle_finite(self.clock(), positive=True)
        if now is None:
            raise ValueError("tracked-order clock returned invalid time")
        return now

    def _audit(self, event: str, intent: dict, **fields) -> None:
        if self.audit is None:
            return
        base = {
            "intent_id": intent.get("intent_id"),
            "venue": self.venue,
            "symbol": intent.get("symbol"),
            "side": intent.get("side"),
            "kind": intent.get("kind"),
            "client_order_id": intent.get("client_order_id"),
            "order_id": intent.get("order_id"),
        }
        base.update(fields)
        self.audit.record(event, **base)

    @staticmethod
    def _validate_identity(intent: dict) -> None:
        for key in ("intent_id", "client_order_id", "symbol", "side"):
            if not str(intent.get(key) or "").strip():
                raise ValueError(f"tracked intent missing {key}")
        if str(intent["side"]).upper() not in {"BUY", "SELL"}:
            raise ValueError("tracked intent side must be BUY or SELL")
        if _lifecycle_finite(intent.get("requested_qty"), positive=True) is None:
            raise ValueError("tracked intent requested_qty must be positive")
        price = intent.get("requested_price")
        if price is not None and _lifecycle_finite(price, positive=True) is None:
            raise ValueError("tracked intent requested_price must be positive")

    def new_intent(self, *, intent_id: str, client_order_id: str, symbol: str,
                   side: str, requested_qty: float,
                   requested_price: Optional[float] = None,
                   kind: Optional[str] = None, attempt: int = 1,
                   metadata: Optional[Dict] = None) -> dict:
        now = self._now()
        intent = {
            "intent_id": str(intent_id),
            "client_order_id": str(client_order_id),
            "symbol": str(symbol),
            "side": str(side).upper(),
            "kind": None if kind is None else str(kind),
            "requested_qty": float(requested_qty),
            "requested_price": (None if requested_price is None
                                else float(requested_price)),
            "attempt": int(attempt),
            "created_at": now,
            "lookup_misses": 0,
        }
        reserved = set(intent) | {
            "order_id", "last_status", "filled_qty", "terminal_status",
            "cancel_attempted_at",
        }
        for key, value in dict(metadata or {}).items():
            if key in reserved:
                raise ValueError(f"tracked intent metadata uses reserved key: {key}")
            intent[str(key)] = value
        self._validate_identity(intent)
        if intent["attempt"] <= 0:
            raise ValueError("tracked intent attempt must be positive")
        return intent

    @staticmethod
    def _order_fields(response) -> dict:
        if isinstance(response, dict):
            order_id = (response.get("orderId") or response.get("order_id")
                        or response.get("id") or response.get("txid"))
            fields = {}
            if order_id is not None:
                fields["order_id"] = str(order_id)
            status = response.get("status")
            if status is not None:
                fields["submit_status"] = str(status)
            submitted_qty = _lifecycle_finite(
                response.get("origQty", response.get("qty")), positive=True)
            if submitted_qty is not None:
                fields["submitted_qty"] = submitted_qty
            return fields
        if (not isinstance(response, bool)
                and isinstance(response, (str, int)) and str(response).strip()):
            return {"order_id": str(response)}
        return {}

    def submit(self, intent: dict, *, persist: PersistPending,
               submit: SubmitIntent) -> TrackedOrderResult:
        """Persist and submit exactly once; reconcile status from a later tick."""
        pending = dict(intent)
        self._validate_identity(pending)
        persist(dict(pending))
        self._audit(
            "submit_requested", pending,
            qty=pending["requested_qty"], price=pending.get("requested_price"),
        )
        try:
            response = submit()
        except Exception as exc:
            pending["submit_error"] = f"{exc.__class__.__name__}: {exc}"
            persist(dict(pending))
            self._audit(
                "submit_ambiguous", pending,
                error_type=exc.__class__.__name__, error=str(exc),
            )
            return TrackedOrderResult("active", pending)

        fields = self._order_fields(response)
        pending.update(fields)
        persist(dict(pending))
        if pending.get("order_id"):
            self._audit(
                "submit_accepted", pending,
                qty=pending["requested_qty"],
                submitted_qty=pending.get("submitted_qty"),
                status=pending.get("submit_status"),
            )
        else:
            self._audit("submit_ambiguous", pending, error="response_without_order_id")
        return TrackedOrderResult("active", pending)

    def _persist_terminal(self, pending: dict, status: OrderStatus,
                          persist: PersistPending) -> TrackedOrderResult:
        pending = dict(pending)
        pending["last_status"] = status.status
        pending["filled_qty"] = status.filled_qty
        pending["terminal_status"] = _terminal_payload(status, self._now())
        persist(dict(pending))
        self._audit(
            "order_terminal", pending, status=status.status,
            venue_status=status.venue_status,
            filled_qty=status.filled_qty, cost=status.cost, fee=status.fee,
        )
        return TrackedOrderResult("terminal", pending, status)

    def reconcile(self, intent: dict, *, persist: PersistPending) -> TrackedOrderResult:
        """Reconcile one pending intent against venue truth; never submit here."""
        pending = dict(intent)
        self._validate_identity(pending)

        cached_terminal = _status_from_terminal_payload(
            pending.get("terminal_status"))
        if cached_terminal is not None:
            return TrackedOrderResult("terminal", pending, cached_terminal)

        symbol = pending["symbol"]
        client_order_id = pending["client_order_id"]
        order_id = pending.get("order_id")
        if not order_id:
            try:
                native = self.market_api.order_by_client_id(
                    symbol, client_order_id, provider_name=self.provider_name)
            except Exception as exc:
                pending["lookup_error"] = f"{exc.__class__.__name__}: {exc}"
                persist(dict(pending))
                self._audit(
                    "lookup_error", pending,
                    error_type=exc.__class__.__name__, error=str(exc),
                )
                if self.retry_on_lookup_error:
                    persist(None)
                    self._audit("lookup_retryable", pending)
                    return TrackedOrderResult("retryable", pending)
                return TrackedOrderResult("active", pending)
            fields = self._order_fields(native)
            if not fields.get("order_id"):
                misses = int(pending.get("lookup_misses") or 0) + 1
                pending["lookup_misses"] = misses
                persist(dict(pending))
                self._audit(
                    "order_missing", pending, missing_confirmation=misses,
                    missing_confirmations_required=self.missing_confirmations,
                )
                if misses < self.missing_confirmations:
                    return TrackedOrderResult("active", pending)
                persist(None)
                return TrackedOrderResult("absent", pending)
            pending.update(fields)
            pending["lookup_misses"] = 0
            pending.pop("lookup_error", None)
            persist(dict(pending))
            order_id = pending["order_id"]
            self._audit("order_recovered", pending)

        try:
            status = self.market_api.order_status(
                symbol, str(order_id), provider_name=self.provider_name)
        except Exception as exc:
            pending["status_error"] = f"{exc.__class__.__name__}: {exc}"
            persist(dict(pending))
            self._audit(
                "status_error", pending,
                error_type=exc.__class__.__name__, error=str(exc),
            )
            return TrackedOrderResult("active", pending)

        pending.pop("status_error", None)
        if status.terminal:
            return self._persist_terminal(pending, status, persist)

        pending["last_status"] = status.status
        pending["filled_qty"] = status.filled_qty
        persist(dict(pending))
        self._audit(
            "order_status", pending, status=status.status,
            venue_status=status.venue_status,
            filled_qty=status.filled_qty, cost=status.cost, fee=status.fee,
        )

        created_at = _lifecycle_finite(pending.get("created_at"), positive=True)
        age_seconds = (max(0.0, self._now() - created_at)
                       if created_at is not None else None)
        cancel_attempted = _lifecycle_finite(
            pending.get("cancel_attempted_at"), positive=True)
        if (self.max_age_seconds is None or age_seconds is None
                or age_seconds < self.max_age_seconds or cancel_attempted is not None):
            return TrackedOrderResult("active", pending, status)

        pending["cancel_attempted_at"] = self._now()
        persist(dict(pending))
        self._audit("cancel_requested", pending, age_seconds=age_seconds)
        try:
            self.market_api.cancel_order(
                symbol, str(order_id), provider_name=self.provider_name)
        except Exception as exc:
            pending["cancel_error"] = f"{exc.__class__.__name__}: {exc}"
            persist(dict(pending))
            self._audit(
                "cancel_ambiguous", pending,
                error_type=exc.__class__.__name__, error=str(exc),
            )
            return TrackedOrderResult("active", pending, status)

        pending.pop("cancel_error", None)
        persist(dict(pending))
        self._audit("cancel_accepted", pending)
        try:
            post_cancel = self.market_api.order_status(
                symbol, str(order_id), provider_name=self.provider_name)
        except Exception as exc:
            pending["status_error"] = f"{exc.__class__.__name__}: {exc}"
            persist(dict(pending))
            self._audit(
                "post_cancel_status_error", pending,
                error_type=exc.__class__.__name__, error=str(exc),
            )
            return TrackedOrderResult("active", pending, status)
        if post_cancel.terminal:
            return self._persist_terminal(pending, post_cancel, persist)
        pending["last_status"] = post_cancel.status
        pending["filled_qty"] = post_cancel.filled_qty
        persist(dict(pending))
        self._audit(
            "cancel_pending", pending, status=post_cancel.status,
            filled_qty=post_cancel.filled_qty,
        )
        return TrackedOrderResult("active", pending, post_cancel)
