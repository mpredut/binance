"""Store atomic propriu rundelor rtrade; separat de outbox-ul generic."""
from __future__ import annotations

import json
import os
import tempfile
import time

from lock import FileLock


class RTradePairStore:
    def __init__(self, path=None):
        root = os.path.dirname(os.path.abspath(__file__))
        self.path = path or os.path.join(root, "cachedb", "rtrade_pairs.json")
        self.lock_path = self.path + ".lock"

    def _read(self):
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, dict) else {"version": 1, "pairs": {}}
        except FileNotFoundError:
            return {"version": 1, "pairs": {}}

    def _write(self, data):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def mutate(self, fn):
        with FileLock(self.lock_path):
            data = self._read()
            data.setdefault("version", 1)
            data.setdefault("pairs", {})
            result = fn(data["pairs"])
            self._write(data)
            return result

    def begin(self, symbol, pair_id, start_side, qty):
        now = time.time()
        def op(pairs):
            pairs[pair_id] = {
                "symbol": symbol, "pair_id": pair_id, "start_side": start_side,
                "qty": float(qty), "phase": "reserved", "terminal": False,
                "intents": {}, "state": None, "created_ts": now, "updated_ts": now,
            }
        self.mutate(op)

    def intent(self, pair_id, side, price, qty, client_order_id, kind="limit"):
        def op(pairs):
            rec = pairs[pair_id]
            key = f"{kind}:{side.upper()}"
            rec["intents"][key] = {
                "side": side.upper(), "price": price, "qty": float(qty),
                "client_order_id": client_order_id, "kind": kind,
                "order_id": None,
            }
            rec["updated_ts"] = time.time()
        self.mutate(op)

    def accepted(self, pair_id, side, order_id, kind="limit"):
        def op(pairs):
            key = f"{kind}:{side.upper()}"
            pairs[pair_id]["intents"][key]["order_id"] = str(order_id)
            pairs[pair_id]["updated_ts"] = time.time()
        self.mutate(op)

    def checkpoint(self, pair_id, state, terminal=False):
        def op(pairs):
            rec = pairs[pair_id]
            rec["state"] = state
            rec["phase"] = state.get("phase")
            rec["terminal"] = bool(terminal)
            rec["updated_ts"] = time.time()
        self.mutate(op)

    def active(self, symbol):
        with FileLock(self.lock_path):
            pairs = self._read().get("pairs", {})
        return [rec for rec in pairs.values()
                if rec.get("symbol") == symbol and not rec.get("terminal")]
