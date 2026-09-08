"""Persistent incident aggregation, independent of venue and delivery transport.

The first batch is due immediately. Further events are summarized once per
interval, including on an empty poll. Reservations precede delivery: this limits
restart duplicates but is not a guaranteed-delivery outbox.
"""
import json
import math
import os

from lock import FileLock
from state_io import atomic_write_json, load_json_state


class IncidentDigest:
    def __init__(self, path, interval_seconds):
        self.path = os.fspath(path)
        self.interval = float(interval_seconds)
        if not math.isfinite(self.interval) or self.interval <= 0:
            raise ValueError("digest interval must be finite and positive")

    def collect(self, events, *, now):
        """Return due summaries of events with labels, quantity and sample ID.

        Labels define an incident. Store counts and one sample, not an unbounded
        list of IDs; detailed records belong in the caller's existing audit.
        Invalid state raises instead of resetting cooldowns and causing a storm.
        """
        if not math.isfinite(now) or now <= 0:
            raise ValueError("digest time must be finite and positive")
        if not events and not os.path.exists(self.path):
            return []
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with FileLock(self.path + ".lock"):
            state = load_json_state(
                self.path, default_factory=lambda: {"version": 1, "groups": {}},
                fail_closed=True, label="notification digest")
            if state.get("version") != 1 or not isinstance(state.get("groups"), dict):
                raise ValueError("invalid notification digest schema")
            groups = state["groups"]
            changed = False
            for key, group in list(groups.items()):
                if (not isinstance(group["count"], int) or group["count"] < 0
                        or not math.isfinite(group["next_at"]) or group["next_at"] <= 0
                        or not math.isfinite(group["quantity"]) or group["quantity"] < 0):
                    raise ValueError("invalid notification digest group")
                if group["count"] == 0 and group["next_at"] <= now:
                    del groups[key]
                    changed = True
            for event in events:
                labels = event["labels"]
                quantity = float(event["quantity"])
                if (not isinstance(labels, dict) or not labels
                        or not all(isinstance(k, str) and isinstance(v, str)
                                   for k, v in labels.items())
                        or not math.isfinite(quantity) or quantity < 0):
                    raise ValueError("invalid notification digest event")
                key = json.dumps(labels, sort_keys=True, separators=(",", ":"))
                group = groups.setdefault(key, {
                    "labels": labels, "next_at": now, "reported": False,
                    "count": 0, "quantity": 0.0,
                })
                if group["count"] == 0:
                    group["first_ts"] = now
                    group["sample_id"] = str(event["sample_id"])
                group["last_ts"] = now
                group["count"] += 1
                group["quantity"] += quantity
                changed = True
            due = []
            for group in groups.values():
                if group["count"] and group["next_at"] <= now:
                    due.append(dict(group))
                    group.update(next_at=now + self.interval, reported=True,
                                 count=0, quantity=0.0)
                    changed = True
            if changed:
                atomic_write_json(self.path, state, separators=(",", ":"))
            return due
