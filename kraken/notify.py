#!/usr/bin/env python3
"""
notify.py — subtire peste notify() PARTAJAT din alertnotifiers.py (radacina).
Doar rezolva simbolul specific Kraken si deleaga (logica comuna = in alertnotifiers).
"""
from __future__ import annotations

import os
import sys

# alertnotifiers.py e in radacina proiectului (parinte fata de kraken/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from alertnotifiers import bind_notify  # noqa: E402

notify = bind_notify(("SYMBOL_LABEL", "KRAKEN_PAIR"), "CRYPTO")
