#!/usr/bin/env python3
"""
common.py — utilitare pentru botul Hyperliquid.
Nucleul comun (log/.env/float_env) vine din botcore.py (radacina); aici raman DOAR
specificele HL: timeout-ul global de socket si now_str.
Re-exportul pastreaza compat inapoi: `from common import log, load_dotenv, ...`.
"""
from __future__ import annotations

import os
import sys
import socket
from datetime import datetime, timezone

# REZILIENTA: SDK-ul Hyperliquid face cereri FARA read-timeout — daca netul cade
# in mijlocul unei cereri deschise, procesul ar atarna la nesfarsit (fara eroare,
# fara puls). Timeout implicit global: orice socket fara timeout explicit = 30s.
socket.setdefaulttimeout(30)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # radacina repo
from botcore import BUCHAREST, log, load_dotenv, float_env, single_instance  # noqa: E402,F401  (re-export compat)


def now_str() -> str:
    n = datetime.now(timezone.utc)
    return f"{n.astimezone(BUCHAREST):%Y-%m-%d %H:%M:%S} Bucuresti"
