#!/usr/bin/env python3
"""
ipo_common.py — utilitare pentru watcher-ul T212.
Nucleul comun (log/.env/parse_dotenv/float_env/http_get) vine din botcore.py (radacina);
aici raman DOAR specificele T212: timezone ET, now_str (cu ET), http_post_json, http_request.
Re-exportul pastreaza compat inapoi: `from ipo_common import log, http_get, float_env, ...`.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # radacina repo
from botcore import (  # noqa: E402,F401  (re-export: compat `from ipo_common import ...`)
    BUCHAREST, HTTP_TIMEOUT, log, load_dotenv, parse_dotenv, float_env, http_get, single_instance,
    are_close, diff_percent,
)

ET = timezone(timedelta(hours=-4))        # US Eastern vara (EDT)


def now_str() -> str:
    """Timestamp clar in ET si Bucuresti."""
    n = datetime.now(timezone.utc)
    return (
        f"{n.astimezone(ET):%Y-%m-%d %H:%M:%S} ET  |  "
        f"{n.astimezone(BUCHAREST):%H:%M:%S} Bucuresti"
    )


def http_post_json(url: str, payload: dict, headers: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(payload).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        log(f"  ! eroare retea POST: {e}")
        return 0, b""


def http_request(method: str, url: str, headers: dict | None = None,
                 payload: dict | None = None) -> tuple[int, bytes]:
    """HTTP generic (DELETE/PUT/etc.). Returneaza (status, body)."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    h = dict(headers or {})
    if data is not None:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=h, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        log(f"  ! eroare retea {method}: {e}")
        return 0, b""
