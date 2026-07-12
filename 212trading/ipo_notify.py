#!/usr/bin/env python3
"""
ipo_notify.py — subtire peste notify() PARTAJAT din alertnotifiers.py (radacina).
Doar rezolva simbolul (cu param explicit pt multi-activ) si deleaga.
"""
from __future__ import annotations

import os
import sys

# alertnotifiers.py e in radacina (parinte fata de 212trading/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from alertnotifiers import notify as _notify  # noqa: E402


def notify(title: str, body: str, source: str,
           price: float | None = None, desktop: bool = False,
           symbol: str | None = None, email: bool = False) -> None:
    """`symbol` explicit e preferat (multi-activ intr-un proces) fata de os.environ,
    care e global si ar da simbolul gresit cand ruleaza mai multe active deodata.
    `email=True` DOAR pt urgente (stop-loss/trailing) -> informativele merg doar pe ntfy."""
    symbol = symbol or os.environ.get("SYMBOL_LABEL") or os.environ.get("YAHOO_SYMBOL") or "STOCK"
    _notify(title, body, source, symbol, price=price, desktop=desktop, email=email)
