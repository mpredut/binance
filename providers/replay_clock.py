# providers/replay_clock.py
"""Mutable clock shared by replay and backtest drivers.

Calling the object returns ``ts``. The driver is responsible for assigning the
timestamp of every replayed event; this class does not advance automatically or enforce
monotonic time. Its initial wall-clock value preserves the historical implementation,
but a correct replay replaces that value before strategy code reads the clock.
"""


import time


class SimClock:
    """Callable simulated time source whose ``ts`` value is set by its driver."""

    def __init__(self):
        self.ts = time.time()

    def __call__(self) -> float:
        return self.ts
