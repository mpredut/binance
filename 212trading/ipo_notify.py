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
from alertnotifiers import bind_notify  # noqa: E402

# ``symbol=`` explicit rămâne prioritar pentru thread-urile multi-activ T212.
notify = bind_notify(("SYMBOL_LABEL", "YAHOO_SYMBOL"), "STOCK")
