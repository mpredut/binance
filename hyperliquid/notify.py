#!/usr/bin/env python3
"""notify.py — subtire peste notify() PARTAJAT din alertnotifiers.py (radacina).
Doar rezolva simbolul specific HL si deleaga (logica comuna = in alertnotifiers)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from alertnotifiers import notify as _notify  # noqa: E402


def notify(title: str, body: str, source: str,
           price: float | None = None, desktop: bool = False, email: bool = False) -> None:
    symbol = os.environ.get("SYMBOL_LABEL") or os.environ.get("HL_COIN") or "HL"
    _notify(title, body, source, symbol, price=price, desktop=desktop, email=email)
