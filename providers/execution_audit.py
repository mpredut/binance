"""Observational lifecycle audit for ``StrategyExecutor`` orders.

This layer neither authorizes an order nor changes the submitted financial values.
Writes are best-effort, so an audit I/O failure does not change the executor result.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable, Optional

from .strategy_executor import OrderStatus

try:  # Linux live: cross-process serialization; safe per-process fallback.
    import fcntl
except ImportError:  # pragma: no cover - Windows/import tooling
    fcntl = None


_WRITE_LOCK = threading.Lock()
_UUID_HEX_SUFFIX = re.compile(r"([0-9a-fA-F]{32})$")
_CACHE_MAX = max(100, int(os.environ.get("EXECUTION_AUDIT_CACHE_MAX", "10000")))


def _slug(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    return text.strip("-") or "unknown"


def new_intent_id(venue: str, symbol: str, kind: Optional[str] = None) -> str:
    """Create a unique intent ID before submission; callers persist it as needed."""
    prefix = "-".join((_slug(venue).lower(), _slug(symbol), _slug(kind or "order").lower()))
    return f"{prefix}-{uuid.uuid4().hex}"


def intent_client_order_id(venue: str, intent_id: str) -> Optional[str]:
    """Encode an intent in the venue's client-ID format without additional state.

    Preserve the complete 128-bit UUID. Legacy/nonstandard intents use a
    deterministic hash so the same intent produces the same identifier after restart.
    """
    raw = str(intent_id)
    match = _UUID_HEX_SUFFIX.search(raw)
    token = (match.group(1).lower() if match else
             hashlib.blake2s(raw.encode("utf-8"), digest_size=16).hexdigest())
    normalized_venue = _slug(venue).lower()
    if normalized_venue == "kraken":
        return token                         # cl_ord_id: UUID without hyphens
    if normalized_venue == "binance":
        return f"SD_{token}"                 # newClientOrderId: 35 <= 36 characters
    if normalized_venue == "hyperliquid":
        return f"0x{token}"                  # cloid: uint128 hexadecimal
    return None                              # T212/unknown venue: local correlation


class ExecutionAudit:
    """Best-effort JSONL writer shared by Kraken, T212, HL, and Binance."""

    def __init__(self, directory: Optional[str] = None,
                 clock: Callable[[], float] = time.time):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.directory = directory or os.environ.get(
            "EXECUTION_AUDIT_DIR", os.path.join(root, "logger", "execution_audit")
        )
        self._clock = clock

    def record(self, event: str, *, intent_id: str, venue: str, symbol: str,
               **fields) -> bool:
        """Append one serialized event; return ``False`` when audit is unavailable.

        The process lock and Linux ``flock`` prevent interleaved writers, but the
        append is not fsynced and therefore is not a durability boundary.
        """
        try:
            ts = float(self._clock())
            payload = {
                "ts": ts,
                "ts_utc": datetime.fromtimestamp(ts, timezone.utc).isoformat(),
                "event": str(event),
                "intent_id": str(intent_id),
                "venue": str(venue),
                "symbol": str(symbol),
            }
            payload.update({key: value for key, value in fields.items() if value is not None})
            line = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            os.makedirs(self.directory, exist_ok=True)
            day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
            path = os.path.join(self.directory, f"execution_audit_{day}.jsonl")
            with _WRITE_LOCK:
                with open(path, "a", encoding="utf-8") as handle:
                    if fcntl is not None:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    try:
                        handle.write(line + "\n")
                        handle.flush()
                    finally:
                        if fcntl is not None:
                            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return True
        except Exception as exc:  # noqa: BLE001 - auditul nu opreste tradingul
            print(f"[execution_audit] scriere esuata: {exc}")
            return False


class AuditedStrategyExecutor:
    """Execution decorator that records lifecycle calls without changing decisions."""

    def __init__(self, executor, audit: Optional[ExecutionAudit] = None,
                 venue: Optional[str] = None):
        self._executor = executor
        self.audit = audit or ExecutionAudit()
        self.name = venue or str(getattr(executor, "name", executor.__class__.__name__))
        self._intent_by_order = OrderedDict()
        self._last_status = OrderedDict()
        self._lock = threading.Lock()

    def _remember(self, symbol: str, order_id: str, intent_id: str) -> None:
        with self._lock:
            key = (symbol, str(order_id))
            self._intent_by_order[key] = intent_id
            self._intent_by_order.move_to_end(key)
            while len(self._intent_by_order) > _CACHE_MAX:
                self._intent_by_order.popitem(last=False)

    def _intent(self, symbol: str, order_id: str, explicit: Optional[str] = None) -> str:
        if explicit:
            self._remember(symbol, order_id, explicit)
            return explicit
        with self._lock:
            known = self._intent_by_order.get((symbol, str(order_id)))
        return known or f"recovered-{_slug(self.name).lower()}-{_slug(symbol)}-{_slug(order_id)}"

    def submit_order_with_intent(self, intent_id: str, symbol: str, side: str, qty: float,
                                 price: Optional[float] = None, *, market: bool = False,
                                 kind: Optional[str] = None,
                                 reference_price: Optional[float] = None,
                                 client_order_id: Optional[str] = None) -> str:
        client_order_id = client_order_id or intent_client_order_id(self.name, intent_id)
        fields = {
            "side": str(side).lower(), "qty": qty, "price": price,
            "market": bool(market), "kind": kind,
            "reference_price": reference_price,
            "client_order_id": client_order_id,
        }
        self.audit.record("submit_requested", intent_id=intent_id, venue=self.name,
                          symbol=symbol, **fields)
        try:
            submit_kwargs = {"market": market, "kind": kind}
            if client_order_id is not None:
                submit_kwargs["client_order_id"] = client_order_id
            order_id = self._executor.submit_order(symbol, side, qty, price, **submit_kwargs)
        except Exception as exc:
            self.audit.record(
                "submit_rejected", intent_id=intent_id, venue=self.name, symbol=symbol,
                error_type=exc.__class__.__name__, error=str(exc), **fields,
            )
            raise
        self._remember(symbol, str(order_id), intent_id)
        self.audit.record("submit_accepted", intent_id=intent_id, venue=self.name,
                          symbol=symbol, order_id=str(order_id), **fields)
        return str(order_id)

    def submit_order(self, symbol: str, side: str, qty: float,
                     price: Optional[float] = None, *, market: bool = False,
                     kind: Optional[str] = None,
                     client_order_id: Optional[str] = None) -> str:
        return self.submit_order_with_intent(
            new_intent_id(self.name, symbol, kind), symbol, side, qty, price,
            market=market, kind=kind, client_order_id=client_order_id,
        )

    def order_status_with_intent(self, intent_id: str, symbol: str,
                                 order_id: str) -> OrderStatus:
        intent_id = self._intent(symbol, order_id, intent_id)
        try:
            status = self._executor.order_status(symbol, order_id)
        except Exception as exc:
            self.audit.record(
                "status_error", intent_id=intent_id, venue=self.name, symbol=symbol,
                order_id=str(order_id), error_type=exc.__class__.__name__, error=str(exc),
            )
            raise
        fingerprint = (status.status, status.filled_qty, status.cost, status.fee)
        key = (symbol, str(order_id))
        with self._lock:
            changed = self._last_status.get(key) != fingerprint
            self._last_status[key] = fingerprint
            self._last_status.move_to_end(key)
            while len(self._last_status) > _CACHE_MAX:
                self._last_status.popitem(last=False)
        if changed:
            self.audit.record(
                "order_status", intent_id=intent_id, venue=self.name, symbol=symbol,
                order_id=str(order_id), status=status.status,
                filled_qty=status.filled_qty, cost=status.cost, fee=status.fee,
            )
        return status

    def order_status(self, symbol: str, order_id: str) -> OrderStatus:
        return self.order_status_with_intent(
            self._intent(symbol, order_id), symbol, order_id,
        )

    def cancel_order_with_intent(self, intent_id: str, symbol: str, order_id: str) -> None:
        intent_id = self._intent(symbol, order_id, intent_id)
        self.audit.record(
            "cancel_requested", intent_id=intent_id, venue=self.name,
            symbol=symbol, order_id=str(order_id),
        )
        try:
            self._executor.cancel_order(symbol, order_id)
        except Exception as exc:
            self.audit.record(
                "cancel_rejected", intent_id=intent_id, venue=self.name, symbol=symbol,
                order_id=str(order_id), error_type=exc.__class__.__name__, error=str(exc),
            )
            raise
        self.audit.record(
            "cancel_accepted", intent_id=intent_id, venue=self.name,
            symbol=symbol, order_id=str(order_id),
        )

    def cancel_order(self, symbol: str, order_id: str) -> None:
        return self.cancel_order_with_intent(
            self._intent(symbol, order_id), symbol, order_id,
        )

    def order_by_client_id(self, symbol: str, client_order_id: str):
        """Delegate deterministic recovery without inventing audit acceptance."""
        lookup = getattr(self._executor, "order_by_client_id", None)
        if not callable(lookup):
            raise RuntimeError(f"{self.name}: order_by_client_id is unsupported")
        return lookup(symbol, str(client_order_id))

    def get_current_price(self, symbol: str):
        return self._executor.get_current_price(symbol)

    def pair_precision(self, symbol: str):
        return self._executor.pair_precision(symbol)

    def free_balance(self, asset: str):
        return self._executor.free_balance(asset)

    def preflight_order(self, symbol: str, side: str, qty: float,
                        price=None, *, market: bool = False,
                        kind: Optional[str] = None) -> None:
        """Run the adapter's optional preflight before a submit is audited."""
        preflight = getattr(self._executor, "preflight_order", None)
        if callable(preflight):
            preflight(
                symbol, side, qty, price, market=market, kind=kind,
            )

    def ohlc_closes(self, symbol: str, interval_min: int):
        return self._executor.ohlc_closes(symbol, interval_min)
