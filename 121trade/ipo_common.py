#!/usr/bin/env python3
"""
ipo_common.py — utilitare partajate (logging, .env, HTTP, timp).

Zero dependinte externe (doar stdlib). Folosit de toate modulele watcher-ului.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone, timedelta

HTTP_TIMEOUT = 25

ET = timezone(timedelta(hours=-4))        # US Eastern vara (EDT)
BUCHAREST = timezone(timedelta(hours=3))  # EEST vara


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).astimezone(BUCHAREST):%H:%M:%S}] {msg}", flush=True)


def now_str() -> str:
    """Timestamp clar in ET si Bucuresti."""
    n = datetime.now(timezone.utc)
    return (
        f"{n.astimezone(ET):%Y-%m-%d %H:%M:%S} ET  |  "
        f"{n.astimezone(BUCHAREST):%H:%M:%S} Bucuresti"
    )


def load_dotenv(path: str = ".env") -> None:
    """Incarca KEY=VALUE dintr-un .env in os.environ (fara a suprascrie mediul real)."""
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                # sterge comentariile inline pt valori neghilimelate (VALUE=x  # comment)
                if not (val.startswith('"') or val.startswith("'")):
                    val = val.split("#")[0].strip()
                val = val.strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        log(f"  .env incarcat din {path}")
    except OSError as e:
        log(f"  ! nu pot citi {path}: {e}")


def parse_dotenv(path: str) -> dict:
    """Ca load_dotenv, dar RETURNEAZA un dict (nu atinge os.environ).

    Necesar cand rulam mai multe active in ACELASI proces: fiecare activ isi ia
    config-ul intr-un dict separat, fara sa se calce pe os.environ unul pe altul.
    """
    out: dict[str, str] = {}
    if not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):]
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if not (val.startswith('"') or val.startswith("'")):
                    val = val.split("#")[0].strip()
                out[key.strip()] = val.strip('"').strip("'") if key else val
    except OSError as e:
        log(f"  ! nu pot citi {path}: {e}")
    return out


def float_env(key: str, env: dict | None = None) -> float | None:
    """Citeste un float din env (os.environ implicit, sau un dict dat), ignorand
    comentariile inline."""
    src = os.environ if env is None else env
    raw = (src.get(key, "") or "").split("#")[0].strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def http_get(url: str, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:  # noqa: BLE001
        log(f"  ! eroare retea: {e}")
        return 0, b""


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
