"""Store atomic propriu rundelor rtrade; separat de outbox-ul generic."""
from __future__ import annotations

import json
import hashlib
import os
import tempfile
import time

from lock import FileLock


def rtrade_client_order_id(pair_id, side, kind="limit"):
    raw = f"{pair_id}:{str(side).upper()}:{kind}".encode("utf-8")
    return "RT_" + hashlib.blake2s(raw, digest_size=16).hexdigest()


class RTradePairStore:
    def __init__(self, path=None, terminal_retention=200):
        root = os.path.dirname(os.path.abspath(__file__))
        self.path = path or os.path.join(root, "cachedb", "rtrade_pairs.json")
        self.lock_path = self.path + ".lock"
        self.terminal_retention = max(0, int(terminal_retention))

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
            if result is not False:
                self._prune_terminal(data["pairs"])
                self._write(data)
            return result

    def _prune_terminal(self, pairs):
        terminal = sorted(
            (rec for rec in pairs.values() if rec.get("terminal")),
            key=lambda rec: float(rec.get("updated_ts", 0)), reverse=True)
        keep = {rec.get("pair_id") for rec in terminal[:self.terminal_retention]}
        for pair_id, rec in list(pairs.items()):
            if rec.get("terminal") and pair_id not in keep:
                del pairs[pair_id]

    def begin(self, symbol, pair_id, start_side, qty):
        now = time.time()
        def op(pairs):
            pairs[pair_id] = {
                "symbol": symbol, "pair_id": pair_id, "start_side": start_side,
                "qty": float(qty), "phase": "reserved", "terminal": False,
                "intents": {}, "state": None, "created_ts": now, "updated_ts": now,
            }
        self.mutate(op)

    def intent(self, pair_id, side, price, qty, client_order_id, kind="limit",
               symbol=None, start_side=None):
        def op(pairs):
            now = time.time()
            rec = pairs.get(pair_id)
            if rec is None:
                if not symbol:
                    raise KeyError(f"pair necunoscut: {pair_id}")
                rec = pairs[pair_id] = {
                    "symbol": symbol, "pair_id": pair_id,
                    "start_side": (start_side or side).upper(),
                    "qty": float(qty), "phase": "reserved", "terminal": False,
                    "intents": {}, "state": None,
                    "created_ts": now, "updated_ts": now,
                }
            key = f"{kind}:{side.upper()}"
            value = {
                "side": side.upper(), "price": price, "qty": float(qty),
                "client_order_id": client_order_id, "kind": kind,
                "order_id": None,
            }
            if rec["intents"].get(key) == value:
                return False
            rec["intents"][key] = value
            rec["updated_ts"] = now
            return True
        self.mutate(op)

    def persist_intent(self, pair_id, side, kind, pending, *, symbol=None,
                       start_side=None, qty=None):
        """Durably replace one canonical tracked intent, or remove it.

        ``TrackedOrderLifecycle`` calls this function before and after every
        external observation.  The legacy ``price``/``qty`` aliases are retained
        in the JSON so an older checkout can still recover records written by the
        new coordinator.
        """
        side = str(side).upper()
        kind = str(kind)
        key = f"{kind}:{side}"

        def op(pairs):
            now = time.time()
            rec = pairs.get(pair_id)
            if pending is None:
                if rec is None or key not in rec.get("intents", {}):
                    return False
                del rec["intents"][key]
                rec["updated_ts"] = now
                return True

            value = dict(pending)
            requested_qty = value.get("requested_qty", value.get("qty", qty))
            if requested_qty is None:
                raise ValueError("tracked rtrade intent missing requested_qty")
            requested_qty = float(requested_qty)
            requested_price = value.get(
                "requested_price", value.get("price"))
            if requested_price is not None:
                requested_price = float(requested_price)

            if rec is None:
                if not symbol:
                    raise KeyError(f"pair necunoscut: {pair_id}")
                rec = pairs[pair_id] = {
                    "symbol": symbol, "pair_id": pair_id,
                    "start_side": (start_side or side).upper(),
                    "qty": requested_qty, "phase": "reserved",
                    "terminal": False, "intents": {}, "state": None,
                    "created_ts": now, "updated_ts": now,
                }

            value["side"] = side
            value["kind"] = kind
            value["requested_qty"] = requested_qty
            value["requested_price"] = requested_price
            # Backward-compatible aliases for the old startup recovery code.
            value["qty"] = requested_qty
            value["price"] = requested_price
            if rec.setdefault("intents", {}).get(key) == value:
                return False
            rec["intents"][key] = value
            rec["updated_ts"] = now
            return True

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

    def checkpoint_many(self, checkpoints):
        """Use one lock and fsync for every round processed during the tick."""
        checkpoints = list(checkpoints)
        if not checkpoints:
            return
        def op(pairs):
            now = time.time()
            for pair_id, state, terminal in checkpoints:
                rec = pairs[pair_id]
                rec["state"] = state
                rec["phase"] = state.get("phase")
                rec["terminal"] = bool(terminal)
                rec["updated_ts"] = now
            return True
        self.mutate(op)

    def active(self, symbol):
        with FileLock(self.lock_path):
            pairs = self._read().get("pairs", {})
        return [rec for rec in pairs.values()
                if rec.get("symbol") == symbol and not rec.get("terminal")]
