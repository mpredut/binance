"""Reusable local synchronization primitives, not limited to trading.

FileLock  — cross-process and cross-thread mutex using fcntl.flock on its own fd.
Cooldown  — persisted gate that prevents repeating an operation for <key> more
            often than every <ttl>s, with atomic reservation and an RAII slot.

See root-level ``trade_cooldown.py`` for the trading-order specialization.
"""
from .file_lock import FileLock
from .cooldown import Cooldown, Reservation

__all__ = ["FileLock", "Cooldown", "Reservation"]
