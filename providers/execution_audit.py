"""Audit observational pentru ciclul de viata al ordinelor StrategyExecutor.

Stratul nu decide daca un ordin este permis si nu modifica argumentele trimise
providerului. Scrierea este best-effort: o eroare de disc nu poate transforma un
ordin acceptat intr-un esec al strategiei.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from .strategy_executor import OrderStatus

try:  # Linux live: serializare intre procese; fallback sigur per proces.
    import fcntl
except ImportError:  # pragma: no cover - Windows/import tooling
    fcntl = None


_WRITE_LOCK = threading.Lock()
_UUID_HEX_SUFFIX = re.compile(r"([0-9a-fA-F]{32})$")


def _slug(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    return text.strip("-") or "unknown"


def new_intent_id(venue: str, symbol: str, kind: Optional[str] = None) -> str:
    """ID unic creat inainte de submit si pastrat in starea strategiei."""
    prefix = "-".join((_slug(venue).lower(), _slug(symbol), _slug(kind or "order").lower()))
    return f"{prefix}-{uuid.uuid4().hex}"


def intent_client_order_id(venue: str, intent_id: str) -> Optional[str]:
    """Encodeaza intentia in formatul acceptat de venue, fara stare suplimentara.

    UUID-ul de 128 biti ramane complet. Pentru intentiile legacy/non-standard se
    foloseste un hash determinist, astfel incat aceeasi intentie produce acelasi
    identificator si dupa restart.
    """
    raw = str(intent_id)
    match = _UUID_HEX_SUFFIX.search(raw)
    token = (match.group(1).lower() if match else
             hashlib.blake2s(raw.encode("utf-8"), digest_size=16).hexdigest())
    normalized_venue = _slug(venue).lower()
    if normalized_venue == "kraken":
        return token                         # cl_ord_id: UUID fara cratime
    if normalized_venue == "binance":
        return f"SD_{token}"                 # newClientOrderId: 35 <= 36 caractere
    if normalized_venue == "hyperliquid":
        return f"0x{token}"                  # cloid: uint128 hex
    return None                              # T212/venue necunoscut: corelare locala


class ExecutionAudit:
    """Writer JSONL comun pentru Kraken/T212/HL/Binance."""

    def __init__(self, directory: Optional[str] = None,
                 clock: Callable[[], float] = time.time):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.directory = directory or os.environ.get(
            "EXECUTION_AUDIT_DIR", os.path.join(root, "logger", "execution_audit")
        )
        self._clock = clock

    def record(self, event: str, *, intent_id: str, venue: str, symbol: str,
               **fields) -> bool:
        """Adauga atomic un eveniment. False inseamna doar audit indisponibil."""
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
    """Decorator behavior-neutral peste contractul strict StrategyExecutor."""

    def __init__(self, executor, audit: Optional[ExecutionAudit] = None,
                 venue: Optional[str] = None):
        self._executor = executor
        self.audit = audit or ExecutionAudit()
        self.name = venue or str(getattr(executor, "name", executor.__class__.__name__))
        self._intent_by_order: dict[tuple[str, str], str] = {}
        self._last_status: dict[tuple[str, str], tuple] = {}
        self._lock = threading.Lock()

    def _remember(self, symbol: str, order_id: str, intent_id: str) -> None:
        with self._lock:
            self._intent_by_order[(symbol, str(order_id))] = intent_id

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

    def get_current_price(self, symbol: str):
        return self._executor.get_current_price(symbol)

    def pair_precision(self, symbol: str):
        return self._executor.pair_precision(symbol)

    def free_balance(self, asset: str):
        return self._executor.free_balance(asset)

    def ohlc_closes(self, symbol: str, interval_min: int):
        return self._executor.ohlc_closes(symbol, interval_min)
