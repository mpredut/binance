#!/usr/bin/env python3
"""notify.py — subtire peste notify() PARTAJAT din alertnotifiers.py (radacina).
Doar rezolva simbolul specific HL si deleaga (logica comuna = in alertnotifiers)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from alertnotifiers import bind_notify  # noqa: E402

notify = bind_notify(("SYMBOL_LABEL", "HL_COIN"), "HL")
