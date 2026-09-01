"""Read-only index of active financial intents across strategy-owned state files.

The index normalizes visibility only. It never writes state, submits orders, cancels
orders, or decides retry. Each strategy remains the authority for its campaign and
financial policy.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class StateSource:
    owner: str
    pattern: str
    json_lines: bool = False


DEFAULT_SOURCES = (
    StateSource("global_outbox", "cachedb/order_retry_queue.jsonl", True),
    StateSource("assetguardian", "cachedb/assetguardian_state.json"),
    StateSource("rtrade", "cachedb/rtrade_pairs.json"),
    StateSource("binance_trailing", "cachedb/trailing_state.json"),
    StateSource("kraken", "kraken/.state_*.json"),
    StateSource("kraken_trailing", "kraken/trailing_state.json"),
    StateSource("t212", "212trading/.state_*.json"),
    StateSource("t212_one_shot", "212trading/.t212_order.*.json"),
    StateSource("hyperliquid", "hyperliquid/.state_*.json"),
)

_TERMINAL = {"closed", "filled", "canceled", "cancelled", "expired", "rejected"}


def _first(row: dict, *names):
    for name in names:
        value = row.get(name)
        if value is not None and value != "":
            return value
    return None


def _intent_row(row: dict, *, owner: str, source: str, location: str,
                inherited: dict) -> dict | None:
    intent_id = _first(row, "intent_id", "client_order_id")
    side = _first(row, "side", "start_side")
    quantity = _first(row, "requested_qty", "qty", "quantity")
    if not intent_id or not side or quantity is None:
        return None
    status = str(_first(
        row, "submission_outcome", "lifecycle", "last_status", "status",
    ) or ("accepted" if _first(row, "order_id", "id", "txid") else "pending")).lower()
    terminal_payload = row.get("terminal_status")
    if isinstance(terminal_payload, dict):
        status = str(terminal_payload.get("status") or status).lower()
    if status in _TERMINAL:
        return None
    symbol = (_first(row, "symbol", "ticker", "pair")
              or _first(inherited, "symbol", "ticker", "pair"))
    venue = (_first(row, "provider_name", "venue") or inherited.get("venue")
             or owner.split("_", 1)[0])
    return {
        "intent_id": str(intent_id),
        "owner": owner,
        "venue": str(venue),
        "symbol": None if symbol is None else str(symbol),
        "side": str(side).upper(),
        "kind": _first(row, "kind", "motivation"),
        "status": status,
        "order_id": _first(row, "order_id", "id", "txid"),
        "client_order_id": row.get("client_order_id"),
        "requested_qty": quantity,
        "executed_qty": _first(row, "filled_qty", "executed_qty", "delivered_qty"),
        "requested_price": _first(row, "requested_price", "price", "limit"),
        "source": source,
        "location": location,
    }


def _walk(value, *, owner: str, source: str, location: str = "$",
          inherited: dict | None = None) -> Iterable[dict]:
    inherited = dict(inherited or {})
    if isinstance(value, dict):
        if value.get("terminal") is True:
            return
        next_context = dict(inherited)
        for key in ("symbol", "ticker", "pair", "venue", "provider_name"):
            if value.get(key) not in (None, ""):
                next_context["venue" if key == "provider_name" else key] = value[key]
        row = _intent_row(
            value, owner=owner, source=source, location=location,
            inherited=next_context,
        )
        if row is not None:
            yield row
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                yield from _walk(
                    child, owner=owner, source=source,
                    location=f"{location}.{key}", inherited=next_context,
                )
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(
                child, owner=owner, source=source,
                location=f"{location}[{index}]", inherited=inherited,
            )


def _read(path: str, *, json_lines: bool):
    with open(path, encoding="utf-8") as handle:
        if json_lines:
            return [json.loads(line) for line in handle if line.strip()]
        return json.load(handle)


def _path_context(path: str) -> dict:
    name = os.path.basename(path)
    if name.startswith(".state_") and name.endswith(".json"):
        return {"symbol": name[len(".state_"):-len(".json")]}
    return {}


def build_active_intent_index(root: str, sources=DEFAULT_SOURCES) -> dict:
    """Return normalized active intents and read errors without mutating sources."""
    root = os.path.abspath(root)
    intents = []
    errors = []
    files = []
    for spec in sources:
        for path in sorted(glob.glob(os.path.join(root, spec.pattern))):
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            files.append(relative)
            try:
                payload = _read(path, json_lines=spec.json_lines)
                intents.extend(_walk(
                    payload, owner=spec.owner, source=relative,
                    inherited=_path_context(path),
                ))
            except (OSError, ValueError, TypeError) as exc:
                errors.append({"source": relative, "error": f"{type(exc).__name__}: {exc}"})
    intents.sort(key=lambda row: (
        row["owner"], row.get("symbol") or "", row["side"], row["intent_id"],
        row["source"], row["location"],
    ))
    return {
        "schema_version": 1,
        "read_only": True,
        "files": files,
        "intents": intents,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the read-only active-intent index.")
    parser.add_argument("--root", default=os.path.dirname(os.path.abspath(__file__)))
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    result = build_active_intent_index(args.root)
    print(json.dumps(result, indent=None if args.compact else 2, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
