#!/usr/bin/env python3
"""
common.py — utilitare pentru botul Kraken.
Nucleul comun (log/.env/float_env/HTTP) vine din botcore.py (radacina);
aici ramane DOAR formatul timestamp-ului Kraken.
Re-exportul de mai jos pastreaza compat inapoi: `from common import log, http_get, ...`.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # radacina repo
from botcore import (  # noqa: E402,F401  (re-export: compat `from common import ...`)
    BUCHAREST, HTTP_TIMEOUT, log, load_dotenv, float_env, http_get,
    http_post_form, http_request, single_instance, are_close, diff_percent,
)


def now_str() -> str:
    n = datetime.now(timezone.utc)
    return f"{n.astimezone(BUCHAREST):%Y-%m-%d %H:%M:%S} Bucuresti"
