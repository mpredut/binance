#!/usr/bin/env python3
"""
common.py — utilitare partajate (logging, .env) pentru botul Hyperliquid.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta

BUCHAREST = timezone(timedelta(hours=3))


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).astimezone(BUCHAREST):%H:%M:%S}] {msg}", flush=True)


def now_str() -> str:
    n = datetime.now(timezone.utc)
    return f"{n.astimezone(BUCHAREST):%Y-%m-%d %H:%M:%S} Bucuresti"


def load_dotenv(path: str = ".env") -> None:
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
                if not (val.startswith('"') or val.startswith("'")):
                    val = val.split("#")[0].strip()
                val = val.strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
        log(f"  .env incarcat din {path}")
    except OSError as e:
        log(f"  ! nu pot citi {path}: {e}")


def float_env(key: str) -> float | None:
    raw = os.environ.get(key, "").split("#")[0].strip()
    try:
        return float(raw) if raw else None
    except ValueError:
        return None
