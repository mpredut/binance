#!/usr/bin/env python3
"""
common.py — utilitare pentru botul Kraken.
Nucleul comun (log/.env/float_env/http_get) vine din botcore.py (radacina);
aici raman DOAR specificele Kraken: now_str si http_post_form (form-encoding).
Re-exportul de mai jos pastreaza compat inapoi: `from common import log, http_get, ...`.
"""
from __future__ import annotations

import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # radacina repo
from botcore import (  # noqa: E402,F401  (re-export: compat `from common import ...`)
    BUCHAREST, HTTP_TIMEOUT, log, load_dotenv, float_env, http_get, single_instance,
)


def now_str() -> str:
    n = datetime.now(timezone.utc)
    return f"{n.astimezone(BUCHAREST):%Y-%m-%d %H:%M:%S} Bucuresti"


def http_post_form(url: str, data: dict, headers: dict | None = None) -> tuple[int, bytes]:
    """POST application/x-www-form-urlencoded (formatul cerut de Kraken)."""
    body = urllib.parse.urlencode(data).encode()
    h = {"Content-Type": "application/x-www-form-urlencoded"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        log(f"  ! eroare retea POST: {e}")
        return 0, b""
