#!/usr/bin/env python3
"""
ipo_common.py — utilities for the T212 watcher.
The shared log/.env/parse_dotenv/float_env/HTTP core comes from root botcore.py.
Only the ET timezone and T212 timestamp format remain here. Re-exports preserve
compatibility with `from ipo_common import log, http_get, float_env, ...`.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repository root
from botcore import (  # noqa: E402,F401  (re-export: compat `from ipo_common import ...`)
    BUCHAREST, HTTP_TIMEOUT, log, load_dotenv, parse_dotenv, float_env,
    http_get, http_post_json, http_request, single_instance, are_close, diff_percent,
)

ET = timezone(timedelta(hours=-4))        # US Eastern summer time (EDT)


def now_str() -> str:
    """Return a clear timestamp in ET and Bucharest time."""
    n = datetime.now(timezone.utc)
    return (
        f"{n.astimezone(ET):%Y-%m-%d %H:%M:%S} ET  |  "
        f"{n.astimezone(BUCHAREST):%H:%M:%S} Bucuresti"
    )
